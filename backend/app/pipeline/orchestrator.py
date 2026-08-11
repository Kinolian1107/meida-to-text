from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db.lancedb_store import LanceDBStore
from app.db.sqlite_store import SQLiteStore
from app.events import progress_hub
from app.models.schemas import STAGE_LABELS
from app.pipeline.asr import load_transcript, run_whisperx
from app.pipeline.cloud_llm import CloudLLMClient
from app.pipeline.errors import PipelineError, VlFailedError
from app.pipeline.extract import run_extract
from app.pipeline.frames_vl import describe_frames, load_frame_descriptions
from app.pipeline.gpu_lock import llama_server_is_up
from app.pipeline.llama_manager import ensure_llama_server_running
from app.pipeline.merge import run_merge
from app.pipeline.source_normalize import (
    copy_hotwords_override,
    load_meta,
    normalize_from_direct_url,
    normalize_from_google_drive,
    normalize_from_youtube,
    video_work_dir,
)
from app.pipeline.summarize import generate_summary
from app.security.account_store import materialize_youtube_cookies, refresh_google_token

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        lance: LanceDBStore,
    ) -> None:
        self.settings = settings
        self.store = store
        self.lance = lance
        self.llm = CloudLLMClient(settings, store)

    def _set_stage(
        self, video_id: str, status: str, progress: int
    ) -> None:
        stage_label = STAGE_LABELS.get(status, status)
        self.store.update_job(
            video_id,
            status=status,
            stage_label=stage_label,
            progress=progress,
        )
        # Fire-and-forget publish for WebSocket clients
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                progress_hub.publish(
                    video_id,
                    {
                        "id": video_id,
                        "status": status,
                        "stage_label": stage_label,
                        "progress": progress,
                        "error_code": None,
                        "error_message": None,
                    },
                )
            )
        except RuntimeError:
            pass

    async def run(self, video_id: str) -> None:
        video = self.store.get_video(video_id)
        if not video:
            logger.error("Video %s not found", video_id)
            return

        work_dir = video_work_dir(self.settings, video_id)
        try:
            await self._run_inner(video_id, video, work_dir)
        except PipelineError as exc:
            logger.exception("Pipeline failed for %s", video_id)
            self.store.update_job(
                video_id,
                status="failed",
                stage_label=STAGE_LABELS["failed"],
                progress=100,
                error_code=exc.code,
                error_message=exc.message,
            )
            await progress_hub.publish(
                video_id,
                {
                    "id": video_id,
                    "status": "failed",
                    "stage_label": STAGE_LABELS["failed"],
                    "progress": 100,
                    "error_code": exc.code,
                    "error_message": exc.message,
                },
            )
        except Exception as exc:
            logger.exception("Unexpected pipeline error for %s", video_id)
            msg = str(exc)[:2000]
            self.store.update_job(
                video_id,
                status="failed",
                stage_label=STAGE_LABELS["failed"],
                progress=100,
                error_code="PIPELINE_ERROR",
                error_message=msg,
            )
            await progress_hub.publish(
                video_id,
                {
                    "id": video_id,
                    "status": "failed",
                    "stage_label": STAGE_LABELS["failed"],
                    "progress": 100,
                    "error_code": "PIPELINE_ERROR",
                    "error_message": msg,
                },
            )

    async def _run_inner(
        self, video_id: str, video: dict[str, Any], work_dir: Path
    ) -> None:
        # --- fetching_source ---
        self._set_stage(video_id, "fetching_source", 5)
        meta_path = work_dir / "meta.json"
        has_media = meta_path.exists() and any(work_dir.glob("media.*"))
        if has_media:
            meta = load_meta(self.settings, video_id)
        else:
            meta = await self._fetch_source(video_id, video, work_dir)

        media_path = Path(meta["local_media_path"])
        if not media_path.is_absolute():
            media_path = (Path.cwd() / media_path).resolve()
        if not media_path.exists():
            candidate = work_dir / media_path.name
            if candidate.exists():
                media_path = candidate
                meta["local_media_path"] = str(media_path)

        media_type = meta.get("media_type") or video.get("media_type") or "video"
        caption_source = meta.get("caption_source") or "none"
        rebuild_from = meta.get("rebuild_from")

        self.store.update_video(
            video_id,
            filename=meta.get("title") or video["filename"],
            caption_source=caption_source,
            local_media_path=str(media_path),
            duration_sec=meta.get("duration_sec"),
            media_type=media_type,
            meta=meta,
        )

        # --- extracting ---
        audio_path = work_dir / "audio.wav"
        frames_index_path = work_dir / "frames_index.json"
        if rebuild_from == "merge" and audio_path.exists():
            self._set_stage(video_id, "extracting", 25)
            extract_info = {
                "audio_path": str(audio_path),
                "duration_sec": video.get("duration_sec"),
                "frames_index": (
                    json.loads(frames_index_path.read_text(encoding="utf-8"))
                    if frames_index_path.exists()
                    else []
                ),
            }
        else:
            self._set_stage(video_id, "extracting", 20)
            extract_info = run_extract(
                work_dir=work_dir,
                media_path=media_path,
                media_type=media_type,
                settings=self.settings,
            )
            if extract_info.get("duration_sec") is not None:
                self.store.update_video(
                    video_id, duration_sec=extract_info["duration_sec"]
                )

        # --- transcribing ---
        transcript = load_transcript(work_dir)
        if transcript is None and caption_source != "none":
            # YouTube captions path may have written transcript already; if not, force ASR
            caption_source = "none"

        if transcript is None:
            self._set_stage(video_id, "transcribing", 40)
            hw = copy_hotwords_override(
                self.settings, video_id, video.get("hotwords") or ""
            )
            transcript = await run_whisperx(
                settings=self.settings,
                audio_path=Path(extract_info["audio_path"]),
                work_dir=work_dir,
                topic=video.get("topic") or "",
                hotwords_file=hw,
            )
        else:
            self._set_stage(video_id, "transcribing", 45)
            logger.info("Skipping WhisperX; using existing transcript")

        # --- describing ---
        frames: list[dict[str, Any]] = []
        existing_frames = load_frame_descriptions(work_dir)
        if rebuild_from == "merge" and existing_frames:
            self._set_stage(video_id, "describing", 65)
            frames = existing_frames
        elif media_type == "video" and extract_info.get("frames_index"):
            self._set_stage(video_id, "describing", 60)
            up = await llama_server_is_up(self.settings)
            if not up:
                up = await ensure_llama_server_running(self.settings)
            if up:
                try:
                    frames = await describe_frames(
                        settings=self.settings,
                        work_dir=work_dir,
                        frames_index=extract_info["frames_index"],
                    )
                except VlFailedError as exc:
                    logger.warning("VL failed, continuing without frames: %s", exc)
                    frames = []
            else:
                logger.warning(
                    "llama-server not up; skipping VL "
                    "(set QWEN_VL_* and LLAMA_AUTO_MANAGE=true)"
                )
                if not (work_dir / "frame_descriptions.json").exists():
                    (work_dir / "frame_descriptions.json").write_text(
                        "[]", encoding="utf-8"
                    )
                frames = load_frame_descriptions(work_dir)
        else:
            self._set_stage(video_id, "describing", 65)
            frames = existing_frames

        # clear one-shot flags
        if meta_path.exists():
            meta.pop("rebuild_from", None)
            meta.pop("force_local_asr", None)
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        # --- merging ---
        self._set_stage(video_id, "merging", 80)

        def _merge_progress(label: str, progress: int) -> None:
            self.store.update_job(
                video_id,
                status="merging",
                stage_label=f"雲端校稿合併 · {label}",
                progress=progress,
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    progress_hub.publish(
                        video_id,
                        {
                            "id": video_id,
                            "status": "merging",
                            "stage_label": f"雲端校稿合併 · {label}",
                            "progress": progress,
                            "error_code": None,
                            "error_message": None,
                        },
                    )
                )
            except RuntimeError:
                pass

        timeline = await run_merge(
            client=self.llm,
            settings=self.settings,
            work_dir=work_dir,
            video_id=video_id,
            caption_source=caption_source,
            transcript=transcript,
            frames=frames,
            on_progress=_merge_progress,
        )
        for seg in timeline:
            seg["video_id"] = video_id
        self.store.replace_timeline(video_id, timeline)

        content = await generate_summary(
            client=self.llm,
            settings=self.settings,
            video_id=video_id,
            segments=timeline,
            prompt_template="bullet_points",
            work_dir=work_dir,
        )
        summary_id = self.store.add_summary(
            video_id=video_id,
            prompt_template="bullet_points",
            content=content,
        )

        video_row = self.store.get_video(video_id) or {}
        video_row["status"] = "ready"
        self.lance.upsert_video(video_row)
        self.lance.replace_timelines(video_id, timeline)
        summaries = self.store.list_summaries(video_id)
        for s in summaries:
            if s["id"] == summary_id:
                self.lance.upsert_summary(s)

        self._set_stage(video_id, "ready", 100)
        logger.info("Pipeline ready for %s", video_id)

    async def _fetch_source(
        self, video_id: str, video: dict[str, Any], work_dir: Path
    ) -> dict[str, Any]:
        source_type = video["source_type"]
        source_url = video.get("source_url") or ""
        account_id = video.get("used_account")
        if not account_id:
            try:
                raw_meta = json.loads(video.get("meta_json") or "{}")
                account_id = raw_meta.get("account_id")
            except Exception:
                account_id = None

        if source_type == "youtube":
            cookie_path = materialize_youtube_cookies(
                self.settings, self.store, account_id
            )
            old = self.settings.youtube_cookies_file
            if cookie_path:
                self.settings.youtube_cookies_file = str(cookie_path)
            try:
                meta = normalize_from_youtube(
                    settings=self.settings, video_id=video_id, url=source_url
                )
            finally:
                self.settings.youtube_cookies_file = old
            if account_id:
                meta["used_account"] = account_id
            return meta

        if source_type == "direct_url":
            return normalize_from_direct_url(
                settings=self.settings, video_id=video_id, url=source_url
            )

        if source_type == "google_drive":
            access_token = None
            if account_id:
                row = self.store.get_account(account_id, with_secret=True)
                if row and row["account_type"] == "google_drive_oauth":
                    access_token = await refresh_google_token(
                        self.settings, self.store, account_id, row
                    )
            from app.pipeline.google_drive import download_google_drive

            return download_google_drive(
                source_url,
                work_dir,
                self.settings,
                access_token=access_token,
            )

        return load_meta(self.settings, video_id)
