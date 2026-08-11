from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import Settings
from app.pipeline.direct_url import download_direct_url
from app.pipeline.google_drive import download_google_drive
from app.pipeline.youtube import fetch_youtube

logger = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}


def video_work_dir(settings: Settings, video_id: str) -> Path:
    path = settings.media_dir / video_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_upload(
    *,
    settings: Settings,
    video_id: str,
    filename: str,
    data: bytes,
    media_type_hint: str | None = None,
) -> dict:
    out_dir = video_work_dir(settings, video_id)
    ext = Path(filename).suffix.lower() or ".bin"
    media_path = out_dir / f"media{ext}"
    media_path.write_bytes(data)

    if media_type_hint in ("video", "audio"):
        media_type = media_type_hint
    elif ext in AUDIO_EXTS:
        media_type = "audio"
    else:
        media_type = "video"

    source_type = "upload_audio" if media_type == "audio" else "upload_video"
    meta = {
        "source_type": source_type,
        "source_url": None,
        "caption_source": "none",
        "local_media_path": str(media_path),
        "title": filename,
        "duration_sec": None,
        "media_type": media_type,
        "used_account": None,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def normalize_from_youtube(
    *, settings: Settings, video_id: str, url: str
) -> dict:
    out_dir = video_work_dir(settings, video_id)
    meta = fetch_youtube(url, out_dir, settings)
    media_path = Path(meta["local_media_path"])
    meta["media_type"] = (
        "audio" if media_path.suffix.lower() in AUDIO_EXTS else "video"
    )
    return meta


def normalize_from_direct_url(
    *, settings: Settings, video_id: str, url: str
) -> dict:
    out_dir = video_work_dir(settings, video_id)
    return download_direct_url(url, out_dir, settings)


def normalize_from_google_drive(
    *, settings: Settings, video_id: str, url: str
) -> dict:
    out_dir = video_work_dir(settings, video_id)
    return download_google_drive(url, out_dir, settings)


def load_meta(settings: Settings, video_id: str) -> dict:
    path = video_work_dir(settings, video_id) / "meta.json"
    return json.loads(path.read_text(encoding="utf-8"))


def copy_hotwords_override(
    settings: Settings, video_id: str, hotwords: str
) -> Path | None:
    """Write per-video hotwords file if user provided extras."""
    if not hotwords.strip():
        return None
    out = video_work_dir(settings, video_id) / "hotwords.txt"
    base = ""
    base_path = Path(settings.asr_hotwords_file)
    if base_path.exists():
        base = base_path.read_text(encoding="utf-8")
    extras = "\n".join(h.strip() for h in hotwords.replace(",", "\n").splitlines() if h.strip())
    out.write_text(base.rstrip() + "\n" + extras + "\n", encoding="utf-8")
    return out
