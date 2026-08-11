from __future__ import annotations

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
    GoogleDriveSubmitRequest,
    RetranscribeRequest,
    SummarizeRequest,
    SummaryItem,
    TimelineDiffItem,
    TimelineDiffResponse,
    TimelineResponse,
    TimelineSegment,
    TimelineSegmentPatch,
    VideoCreateResponse,
    VideoListItem,
    VideoStatusResponse,
    YoutubeSubmitRequest,
)
from app.pipeline.export_docs import build_export_bytes, timeline_to_markdown
from app.pipeline.merge import load_correction_meta
from app.pipeline.source_normalize import save_upload, video_work_dir
from app.pipeline.summarize import generate_summary
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
    save_upload(
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
        probe = probe_youtube(body.url, settings)
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


@router.get("", response_model=list[VideoListItem])
async def list_videos(request: Request, status: str | None = None):
    _, store, _ = _deps(request)
    rows = store.list_videos(status=status)
    items = []
    for r in rows:
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
            )
        )
    return items


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
        segments=[
            TimelineSegment(
                id=r["id"],
                video_id=r["video_id"],
                start=r["start"],
                end=r["end"],
                type=r["type"],
                text=r["text"],
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
    # also persist under work dir
    out = video_work_dir(settings, video_id) / "exports" / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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


@router.get("/{video_id}/media")
async def get_media(request: Request, video_id: str):
    settings, store, _ = _deps(request)
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    path = video.get("local_media_path")
    media_path = Path(path) if path else None
    if not media_path or not media_path.exists():
        # fallback to work dir media.*
        work = video_work_dir(settings, video_id)
        candidates = list(work.glob("media.*"))
        media_path = candidates[0] if candidates else None
    if not media_path or not media_path.exists():
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
    request.app.state.lance.upsert_summary(row)
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
