from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import shutil
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

logger = logging.getLogger(__name__)

from app.security.account_store import materialize_youtube_cookies
from app.models.schemas import (
    STAGE_LABELS,
    BatchUploadResponse,
    CorrectionInfo,
    DirectUrlSubmitRequest,
    ExistingLookupRequest,
    ExistingLookupResponse,
    ExistingMatch,
    GoogleDriveSubmitRequest,
    RetranscribeRequest,
    SummarizeRequest,
    SummaryItem,
    TagItem,
    TimelineDiffItem,
    TimelineDiffResponse,
    TimelineResponse,
    TimelineSegment,
    TimelineSegmentPatch,
    TranslationState,
    VideoCreateResponse,
    VideoListItem,
    VideoListResponse,
    VideoStatusResponse,
    YoutubeSubmitRequest,
)
from app.pipeline.embeddings import embed_text_remote
from app.pipeline.export_docs import (
    ascii_download_stem,
    build_export_bytes,
    content_disposition,
    timeline_to_markdown,
    utf8_download_stem,
)
from app.pipeline.merge import load_correction_meta
from app.pipeline.source_identity import LookupQuery, collect_matches
from app.pipeline.source_normalize import save_upload, video_work_dir
from app.pipeline.summarize import generate_summary
from app.pipeline.tagging import finalize_summary_extras
from app.pipeline.translate import (
    SUBTITLE_LANGS,
    build_srt,
    build_vtt,
    get_translation_meta,
    run_translation_job,
    update_translation_meta,
)
from app.pipeline.youtube import probe_youtube

router = APIRouter(prefix="/api/videos", tags=["videos"])


def _deps(request: Request):
    return request.app.state.settings, request.app.state.store, request.app.state.worker


def _correction_info(request: Request, video_id: str) -> CorrectionInfo | None:
    settings, store, _ = _deps(request)
    work_dir = video_work_dir(settings, video_id)
    meta = load_correction_meta(work_dir)
    if meta:
        models = meta.get("models") or {}
        return CorrectionInfo(
            applied=bool(meta.get("applied")),
            model=meta.get("model"),
            models={
                "c1": models.get("c1"),
                "c2": models.get("c2"),
                "c3": models.get("c3"),
            },
            corrected_at=meta.get("corrected_at"),
        )
    # Fallback for videos corrected before correction_meta.json existed
    latest = store.get_latest_correction_models(video_id)
    models_used = [m for m in latest.values() if m]
    if not models_used:
        return CorrectionInfo(applied=False, model=None, models=latest)
    return CorrectionInfo(
        applied=True,
        model=models_used[0],
        models=latest,
        corrected_at=None,
    )


def _translation_state(request: Request, video_id: str) -> TranslationState:
    """Merge the persisted job meta with what is actually on disk / in flight.

    Reality wins over the stored status: a timeline rebuild wipes text_zh
    without touching the meta, and a backend restart kills the in-flight task
    without touching it either. Both would otherwise leave the UI staring at a
    status that can never change.
    """
    _, store, _ = _deps(request)
    meta = get_translation_meta(store, video_id)
    translated, total = store.count_translatable_segments(video_id)
    status = str(meta.get("status") or "idle")
    error = meta.get("error")
    if status not in {"idle", "running", "done", "failed"}:
        status = "idle"
    if status in {"done", "failed"} and translated == 0:
        status = "idle"
    if status == "running":
        task = _translation_tasks(request.app).get(video_id)
        if task is None or task.done():
            status, error = "failed", "翻譯中斷（伺服器重啟或已停止），請重新翻譯"
            update_translation_meta(store, video_id, status=status, error=error)
    return TranslationState(
        status=status,
        translated=translated,
        total=total,
        model=meta.get("model"),
        error=error,
        updated_at=meta.get("updated_at"),
    )


def _translation_tasks(app) -> dict[str, asyncio.Task]:
    tasks = getattr(app.state, "translation_tasks", None)
    if tasks is None:
        tasks = {}
        app.state.translation_tasks = tasks
    return tasks


def _media_type_from_name(filename: str) -> tuple[str, str]:
    ext = Path(filename).suffix.lower()
    media_type = (
        "audio"
        if ext in {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus"}
        else "video"
    )
    source_type = "upload_audio" if media_type == "audio" else "upload_video"
    return media_type, source_type


async def _create_from_upload(
    *,
    settings,
    store,
    worker,
    filename: str,
    data: bytes,
    topic: str,
    hotwords: str,
) -> str:
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > settings.max_download_bytes:
        raise HTTPException(400, "File too large")
    media_type, source_type = _media_type_from_name(filename)
    video_id = store.create_video(
        filename=filename,
        media_type=media_type,
        source_type=source_type,
        topic=topic,
        hotwords=hotwords,
    )
    await asyncio.to_thread(
        save_upload,
        settings=settings,
        video_id=video_id,
        filename=filename,
        data=data,
        media_type_hint=media_type,
    )
    await worker.enqueue(video_id)
    return video_id


@router.post("/upload", response_model=VideoCreateResponse)
async def upload_video(
    request: Request,
    file: UploadFile = File(...),
    topic: str = Form(""),
    hotwords: str = Form(""),
):
    settings, store, worker = _deps(request)
    data = await file.read()
    video_id = await _create_from_upload(
        settings=settings,
        store=store,
        worker=worker,
        filename=file.filename or "upload.bin",
        data=data,
        topic=topic,
        hotwords=hotwords,
    )
    return VideoCreateResponse(id=video_id, status="pending")


@router.post("/upload-batch", response_model=BatchUploadResponse)
async def upload_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    topic: str = Form(""),
    hotwords: str = Form(""),
):
    settings, store, worker = _deps(request)
    if not files:
        raise HTTPException(400, "No files")
    ids: list[str] = []
    for f in files:
        data = await f.read()
        vid = await _create_from_upload(
            settings=settings,
            store=store,
            worker=worker,
            filename=f.filename or "upload.bin",
            data=data,
            topic=topic,
            hotwords=hotwords,
        )
        ids.append(vid)
    return BatchUploadResponse(ids=ids)


@router.post("/from-youtube", response_model=VideoCreateResponse)
async def from_youtube(request: Request, body: YoutubeSubmitRequest):
    settings, store, worker = _deps(request)
    cookie_path = materialize_youtube_cookies(settings, store, body.account_id)
    old = settings.youtube_cookies_file
    if cookie_path:
        settings.youtube_cookies_file = str(cookie_path)
    try:
        probe = await asyncio.to_thread(probe_youtube, body.url, settings)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        settings.youtube_cookies_file = old

    video_id = store.create_video(
        filename=probe.get("title") or body.url,
        media_type="video",
        source_type="youtube",
        source_url=body.url,
        topic=body.topic,
        hotwords=body.hotwords,
        caption_source=probe.get("caption_source") or "none",
        meta={**probe, "account_id": body.account_id},
    )
    if body.account_id:
        store.update_video(video_id, used_account=body.account_id)
    channel = probe.get("channel")
    if channel:
        store.add_video_tag(video_id, channel, kind="channel", source="channel")
    await worker.enqueue(video_id)
    return VideoCreateResponse(id=video_id, status="pending")


@router.post("/from-url", response_model=VideoCreateResponse)
async def from_url(request: Request, body: DirectUrlSubmitRequest):
    _, store, worker = _deps(request)
    video_id = store.create_video(
        filename=body.url.rsplit("/", 1)[-1] or body.url,
        media_type="video",
        source_type="direct_url",
        source_url=body.url,
        topic=body.topic,
        hotwords=body.hotwords,
    )
    await worker.enqueue(video_id)
    return VideoCreateResponse(id=video_id, status="pending")


@router.post("/from-google-drive", response_model=VideoCreateResponse)
async def from_google_drive(request: Request, body: GoogleDriveSubmitRequest):
    _, store, worker = _deps(request)
    video_id = store.create_video(
        filename="google-drive",
        media_type="video",
        source_type="google_drive",
        source_url=body.url,
        topic=body.topic,
        hotwords=body.hotwords,
        meta={"account_id": body.account_id} if body.account_id else None,
    )
    if body.account_id:
        store.update_video(video_id, used_account=body.account_id)
    await worker.enqueue(video_id)
    return VideoCreateResponse(id=video_id, status="pending")


@router.post("/lookup-existing", response_model=ExistingLookupResponse)
async def lookup_existing(request: Request, body: ExistingLookupRequest):
    """Find library items that look like the media the user is about to add."""
    settings, store, _ = _deps(request)
    queries = [
        LookupQuery(
            source_type=item.source_type,
            url=item.url,
            filename=item.filename,
            size=item.size,
        )
        for item in body.items
    ]
    videos = store.list_videos()
    upload_sizes: dict[str, int] = {}
    if any(q.source_type == "upload" for q in queries):
        for video in videos:
            if video.get("source_type") not in {"upload_video", "upload_audio"}:
                continue
            size = _existing_media_size(settings, video)
            if size is not None:
                upload_sizes[video["id"]] = size
    hits = collect_matches(videos, queries, upload_sizes=upload_sizes)
    matches: list[ExistingMatch] = []
    for hit in hits:
        video = hit.video
        job = store.get_job(video["id"]) or {}
        matches.append(
            ExistingMatch(
                item_index=hit.item_index,
                id=video["id"],
                filename=video.get("filename") or "",
                source_type=video.get("source_type") or "",
                source_url=video.get("source_url") or None,
                status=video.get("status") or "pending",
                progress=job.get("progress") or 0,
                upload_time=video.get("upload_time"),
                match_reason=hit.match_reason,
                size_matched=hit.size_matched,
            )
        )
    return ExistingLookupResponse(matches=matches)


@router.get("", response_model=VideoListResponse)
async def list_videos(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    search_mode: str = "keyword",
    tags: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    settings, store, _ = _deps(request)
    tag_ids = [t for t in (tags or "").split(",") if t] or None

    video_ids: list[str] | None = None
    keyword: str | None = None
    if q and search_mode == "semantic":
        vector = await embed_text_remote(q, settings)
        hits = request.app.state.lance.search_summaries(vector, limit=500)
        seen: set[str] = set()
        video_ids = []
        for h in hits:
            vid = h["video_id"]
            if vid not in seen:
                seen.add(vid)
                video_ids.append(vid)
    elif q:
        keyword = q

    rows = store.list_videos(
        status=status, keyword=keyword, tag_ids=tag_ids, video_ids=video_ids
    )

    total = len(rows)
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    tags_by_video = store.get_tags_for_videos([r["id"] for r in page_rows])
    items = []
    for r in page_rows:
        job = store.get_job(r["id"])
        items.append(
            VideoListItem(
                id=r["id"],
                filename=r["filename"],
                media_type=r["media_type"],
                source_type=r["source_type"],
                status=r["status"],
                duration_sec=r.get("duration_sec"),
                upload_time=r.get("upload_time"),
                caption_source=r.get("caption_source") or "none",
                progress=(job or {}).get("progress") or 0,
                error_code=r.get("error_code"),
                source_url=r.get("source_url") or None,
                has_media=_resolve_media_path(settings, r) is not None,
                tags=[TagItem(**t) for t in tags_by_video.get(r["id"], [])],
            )
        )
    return VideoListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{video_id}", response_model=VideoStatusResponse)
@router.get("/{video_id}/status", response_model=VideoStatusResponse)
async def video_status(request: Request, video_id: str):
    _, store, _ = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    job = store.get_job(video_id) or {}
    status = video["status"]
    caption = video.get("caption_source") or "none"
    return VideoStatusResponse(
        id=video_id,
        status=status,
        stage_label=job.get("stage_label") or STAGE_LABELS.get(status, status),
        progress=job.get("progress") or 0,
        error_code=video.get("error_code") or job.get("error_code"),
        error_message=video.get("error_message") or job.get("error_message"),
        caption_source=caption,
        source_type=video.get("source_type") or "upload_video",
        filename=video.get("filename") or "",
        can_retranscribe_locally=caption in ("auto", "manual"),
        correction=_correction_info(request, video_id),
    )


@router.delete("/{video_id}")
async def delete_video(request: Request, video_id: str):
    """Hard-delete a video: SQLite, LanceDB, and on-disk media workdir."""
    settings, store, _ = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")

    summary_ids = [s["id"] for s in store.list_summaries(video_id)]
    ok = store.delete_video(video_id)
    if not ok:
        raise HTTPException(404, "Video not found")

    try:
        request.app.state.lance.delete_video(video_id, summary_ids=summary_ids)
    except Exception:
        logger.exception("LanceDB hard-delete failed for %s", video_id)

    work_dir = settings.media_dir / video_id
    if work_dir.exists():
        try:
            shutil.rmtree(work_dir)
        except Exception:
            logger.exception("Failed to remove media dir %s", work_dir)

    return {"ok": True, "id": video_id}


@router.get("/{video_id}/timeline", response_model=TimelineResponse)
async def get_timeline(request: Request, video_id: str):
    _, store, _ = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    rows = store.get_timeline(video_id)
    caption = video.get("caption_source") or "none"
    return TimelineResponse(
        video_id=video_id,
        caption_source=caption,
        can_retranscribe_locally=caption == "auto",
        correction=_correction_info(request, video_id),
        translation=_translation_state(request, video_id),
        segments=[
            TimelineSegment(
                id=r["id"],
                video_id=r["video_id"],
                start=r["start"],
                end=r["end"],
                type=r["type"],
                text=r["text"],
                text_zh=r.get("text_zh"),
                frame_path=r.get("frame_path"),
                edited=bool(r.get("edited")),
                speaker=r.get("speaker"),
            )
            for r in rows
        ],
    )


@router.patch(
    "/{video_id}/timeline/{segment_id}", response_model=TimelineSegment
)
async def patch_timeline_segment(
    request: Request,
    video_id: str,
    segment_id: str,
    body: TimelineSegmentPatch,
):
    settings, store, _ = _deps(request)
    if not store.get_video(video_id):
        raise HTTPException(404, "Video not found")
    row = store.update_timeline_segment(video_id, segment_id, body.text)
    if not row:
        raise HTTPException(404, "Segment not found")
    # Sync timeline.json on disk
    work_dir = video_work_dir(settings, video_id)
    segments = store.get_timeline(video_id)
    (work_dir / "timeline.json").write_text(
        json.dumps(
            {"video_id": video_id, "timeline": segments},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    request.app.state.lance.replace_timelines(video_id, segments)
    return TimelineSegment(
        id=row["id"],
        video_id=row["video_id"],
        start=row["start"],
        end=row["end"],
        type=row["type"],
        text=row["text"],
        text_zh=row.get("text_zh"),
        frame_path=row.get("frame_path"),
        edited=bool(row.get("edited")),
        speaker=row.get("speaker"),
    )


@router.get("/{video_id}/diff", response_model=TimelineDiffResponse)
async def get_correction_diff(request: Request, video_id: str):
    settings, store, _ = _deps(request)
    if not store.get_video(video_id):
        raise HTTPException(404, "Video not found")
    path = video_work_dir(settings, video_id) / "correction_diff.json"
    if not path.exists():
        return TimelineDiffResponse(video_id=video_id, items=[])
    data = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for key in ("speech", "frames"):
        for row in data.get(key) or []:
            items.append(TimelineDiffItem(**row))
    return TimelineDiffResponse(video_id=video_id, items=items)


@router.get("/{video_id}/translation", response_model=TranslationState)
async def get_translation_status(request: Request, video_id: str):
    _, store, _ = _deps(request)
    if not store.get_video(video_id):
        raise HTTPException(404, "Video not found")
    return _translation_state(request, video_id)


@router.post("/{video_id}/translate", response_model=TranslationState)
async def translate_subtitles(request: Request, video_id: str):
    """Kick off Traditional Chinese subtitle translation in the background.

    A two-hour talk is dozens of LLM round trips; holding the HTTP request open
    for that would time out in the browser, so the client polls
    GET /translation instead.
    """
    settings, store, _ = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if video["status"] != "ready":
        raise HTTPException(400, "Video not ready")

    tasks = _translation_tasks(request.app)
    running = tasks.get(video_id)
    if running and not running.done():
        raise HTTPException(409, "翻譯已在進行中")

    _, total = store.count_translatable_segments(video_id)
    if total == 0:
        raise HTTPException(400, "沒有可翻譯的語音段落")

    from app.pipeline.cloud_llm import CloudLLMClient

    client = CloudLLMClient(settings, store)
    if not client.configured:
        raise HTTPException(400, "CLOUD_LLM_BASE_URL 未設定，無法翻譯字幕")

    update_translation_meta(
        store, video_id, status="running", done=0, total=total, error=None
    )
    task = asyncio.create_task(
        run_translation_job(
            client=client, settings=settings, store=store, video_id=video_id
        )
    )
    tasks[video_id] = task
    task.add_done_callback(
        lambda t, vid=video_id: tasks.pop(vid, None) if tasks.get(vid) is t else None
    )
    return _translation_state(request, video_id)


@router.get("/{video_id}/subtitles.vtt")
async def get_subtitles(request: Request, video_id: str, lang: str = "zh"):
    """WebVTT track for the <video> element (lang=zh|en|both)."""
    _, store, _ = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if lang not in SUBTITLE_LANGS:
        raise HTTPException(400, f"lang must be one of {'|'.join(SUBTITLE_LANGS)}")
    body = build_vtt(store.get_timeline(video_id), lang)
    title = video.get("filename") or video_id
    return Response(
        content=body,
        media_type="text/vtt; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": content_disposition(
                f"{ascii_download_stem(title)}.{lang}.vtt",
                f"{utf8_download_stem(title)}.{lang}.vtt",
            ),
        },
    )


def _subtitle_download_name(title: str, lang: str, ext: str) -> tuple[str, str]:
    return (
        f"{ascii_download_stem(title)}.{lang}.{ext}",
        f"{utf8_download_stem(title)}.{lang}.{ext}",
    )


@router.get("/{video_id}/subtitles.srt")
async def get_subtitles_srt(request: Request, video_id: str, lang: str = "zh"):
    """Download timeline speech as SubRip (lang=zh|en|both)."""
    _, store, _ = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if lang not in SUBTITLE_LANGS:
        raise HTTPException(400, f"lang must be one of {'|'.join(SUBTITLE_LANGS)}")
    body = build_srt(store.get_timeline(video_id), lang)
    ascii_name, utf8_name = _subtitle_download_name(video.get("filename") or video_id, lang, "srt")
    return Response(
        content=body,
        media_type="application/x-subrip; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": content_disposition(ascii_name, utf8_name),
        },
    )


@router.post("/{video_id}/resume", response_model=VideoCreateResponse)
async def resume_pipeline(request: Request, video_id: str):
    """Re-enqueue a failed/interrupted job; keeps on-disk artifacts for resume."""
    _, store, worker = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    store.ensure_job_row(video_id)
    await worker.enqueue(video_id)
    return VideoCreateResponse(id=video_id, status="pending")


@router.get("/{video_id}/export")
async def export_video(
    request: Request,
    video_id: str,
    format: str = "md",
):
    settings, store, _ = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    fmt = format.lower().strip()
    if fmt not in {"md", "docx", "pdf"}:
        raise HTTPException(400, "format must be md|docx|pdf")
    segments = store.get_timeline(video_id)
    summaries = store.list_summaries(video_id)
    summary = summaries[0]["content"] if summaries else None
    title = video.get("filename") or video_id
    markdown = timeline_to_markdown(title=title, segments=segments, summary=summary)
    try:
        data, media_type, filename = build_export_bytes(fmt, title, markdown)
    except Exception as exc:
        raise HTTPException(500, f"Export failed: {exc}") from exc
    utf8_name = f"{utf8_download_stem(title)}.{fmt}"
    try:
        out = video_work_dir(settings, video_id) / "exports" / utf8_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    except Exception:
        logger.exception("Failed to persist export %s for %s", utf8_name, video_id)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition(filename, utf8_name)},
    )


@router.post("/{video_id}/timeline/rebuild", response_model=VideoCreateResponse)
async def rebuild_timeline(request: Request, video_id: str):
    """Re-run merging (and VL if frames exist) from existing media/transcript."""
    settings, store, worker = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    work_dir = video_work_dir(settings, video_id)
    # Keep transcript & frames; clear timeline only
    (work_dir / "timeline.json").unlink(missing_ok=True)
    meta_path = work_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["rebuild_from"] = "merge"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    store.ensure_job_row(video_id)
    await worker.enqueue(video_id)
    return VideoCreateResponse(id=video_id, status="pending")


@router.post("/{video_id}/retranscribe", response_model=VideoCreateResponse)
async def retranscribe_locally(
    request: Request, video_id: str, body: RetranscribeRequest | None = None
):
    """Force local WhisperX even if YouTube auto captions were used."""
    settings, store, worker = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    work_dir = video_work_dir(settings, video_id)
    for name in (
        "transcript.raw.json",
        "transcript.raw.srt",
        "timeline.json",
        "captions.raw.vtt",
        "captions.raw.srt",
    ):
        (work_dir / name).unlink(missing_ok=True)
    meta_path = work_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["caption_source"] = "none"
        meta["force_local_asr"] = True
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    fields: dict = {"caption_source": "none"}
    if body:
        if body.topic is not None:
            fields["topic"] = body.topic
        if body.hotwords is not None:
            fields["hotwords"] = body.hotwords
    store.update_video(video_id, **fields)
    store.ensure_job_row(video_id)
    await worker.enqueue(video_id)
    return VideoCreateResponse(id=video_id, status="pending")


def _existing_media_size(settings, video: dict) -> int | None:
    """Byte size of an already-written media file, without creating work dirs."""
    path = _peek_media_path(settings, video)
    if path is None:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _peek_media_path(settings, video: dict) -> Path | None:
    path = video.get("local_media_path")
    media_path = Path(path) if path else None
    if media_path and media_path.exists():
        return media_path
    work = settings.media_dir / video["id"]
    if not work.exists():
        return None
    candidates = [p for p in work.glob("media.*") if p.is_file()]
    return candidates[0] if candidates else None


def _resolve_media_path(settings, video: dict) -> Path | None:
    """Locate the media file kept on disk for a video, or None if it is gone.

    Media is never auto-purged (only DELETE /videos/{id} removes the work dir),
    but the row can outlive the file if it was cleaned up by hand, so callers
    must treat a missing file as a normal case.
    """
    found = _peek_media_path(settings, video)
    if found is not None:
        return found
    # Creating the work dir is only for callers that will write into it next;
    # a missing folder still means there is no media file.
    work = video_work_dir(settings, video["id"])
    candidates = list(work.glob("media.*"))
    candidate = candidates[0] if candidates else None
    return candidate if candidate and candidate.exists() else None


@router.get("/{video_id}/media")
async def get_media(request: Request, video_id: str):
    settings, store, _ = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    media_path = _resolve_media_path(settings, video)
    if not media_path:
        raise HTTPException(404, "Media file not found")
    mime, _ = mimetypes.guess_type(str(media_path))
    return FileResponse(
        media_path,
        media_type=mime or "application/octet-stream",
        filename=media_path.name,
    )


@router.post("/{video_id}/summarize", response_model=SummaryItem)
async def summarize(request: Request, video_id: str, body: SummarizeRequest):
    settings, store, _ = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if video["status"] != "ready":
        raise HTTPException(400, "Video not ready")

    segments = store.get_timeline(video_id)
    from app.pipeline.cloud_llm import CloudLLMClient

    client = CloudLLMClient(settings, store)
    work_dir = video_work_dir(settings, video_id)
    content = await generate_summary(
        client=client,
        settings=settings,
        video_id=video_id,
        segments=segments,
        prompt_template=body.prompt_template,
        custom_prompt=body.custom_prompt,
        work_dir=work_dir,
    )
    summary_id = store.add_summary(
        video_id=video_id,
        prompt_template=body.prompt_template,
        content=content,
    )
    rows = store.list_summaries(video_id)
    row = next(r for r in rows if r["id"] == summary_id)
    await finalize_summary_extras(
        client=client,
        settings=settings,
        store=store,
        lance=request.app.state.lance,
        video_id=video_id,
        title=video.get("filename") or "",
        summary_row=row,
    )
    return SummaryItem(
        id=row["id"],
        video_id=row["video_id"],
        prompt_template=row["prompt_template"],
        content=row["content"],
        created_at=row["created_at"],
    )


@router.get("/{video_id}/summaries", response_model=list[SummaryItem])
async def list_summaries(request: Request, video_id: str):
    _, store, _ = _deps(request)
    if not store.get_video(video_id):
        raise HTTPException(404, "Video not found")
    return [
        SummaryItem(
            id=r["id"],
            video_id=r["video_id"],
            prompt_template=r["prompt_template"],
            content=r["content"],
            created_at=r["created_at"],
        )
        for r in store.list_summaries(video_id)
    ]


@router.get("/{video_id}/frames/{frame_name}")
async def get_frame(request: Request, video_id: str, frame_name: str):
    from fastapi.responses import FileResponse

    settings, store, _ = _deps(request)
    if not store.get_video(video_id):
        raise HTTPException(404, "Video not found")
    # Prevent path traversal
    safe = Path(frame_name).name
    path = video_work_dir(settings, video_id) / "frames" / safe
    if not path.exists():
        raise HTTPException(404, "Frame not found")
    return FileResponse(path, media_type="image/jpeg")
