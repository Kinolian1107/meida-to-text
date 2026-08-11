from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.pipeline.errors import DownloadTooLargeError, InvalidMediaError, PipelineError

logger = logging.getLogger(__name__)

ALLOWED_EXTS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".avi",
    ".m4v",
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".aac",
    ".opus",
}

ALLOWED_CONTENT_PREFIXES = ("video/", "audio/", "application/octet-stream")


def _ext_from_url(url: str) -> str:
    path = urlparse(url).path
    return Path(path).suffix.lower()


def _looks_like_media(content_type: str | None, ext: str) -> bool:
    if ext in ALLOWED_EXTS:
        return True
    if not content_type:
        return False
    ct = content_type.split(";")[0].strip().lower()
    return any(ct.startswith(p) for p in ALLOWED_CONTENT_PREFIXES)


def download_direct_url(
    url: str, out_dir: Path, settings: Settings
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Try yt-dlp first for generic extractors
    try:
        import yt_dlp

        media_tmpl = str(out_dir / "media.%(ext)s")
        opts = {
            "quiet": True,
            "noplaylist": True,
            "outtmpl": media_tmpl,
            "format": "bv*[height<=720]+ba/b[height<=720]/b",
            "merge_output_format": "mp4",
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        media_files = [
            p
            for p in out_dir.iterdir()
            if p.is_file() and p.suffix.lower() in ALLOWED_EXTS
        ]
        if media_files:
            media_path = max(media_files, key=lambda p: p.stat().st_size)
            final = out_dir / f"media{media_path.suffix.lower()}"
            if media_path != final:
                media_path.rename(final)
                media_path = final
            media_type = (
                "audio"
                if media_path.suffix.lower()
                in {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus"}
                else "video"
            )
            meta: dict[str, Any] = {
                "source_type": "direct_url",
                "source_url": url,
                "caption_source": "none",
                "local_media_path": str(media_path),
                "title": (info or {}).get("title") or media_path.name,
                "duration_sec": (info or {}).get("duration"),
                "media_type": media_type,
                "used_account": None,
            }
            (out_dir / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return meta
    except Exception as exc:
        logger.info("yt-dlp direct_url fallback to httpx: %s", exc)

    return _http_stream_download(url, out_dir, settings)


def _http_stream_download(
    url: str, out_dir: Path, settings: Settings
) -> dict:
    ext = _ext_from_url(url)
    headers: dict[str, str] = {
        "User-Agent": "media2text/0.1",
    }

    with httpx.Client(
        timeout=httpx.Timeout(30.0, read=600.0),
        follow_redirects=True,
        max_redirects=5,
    ) as client:
        content_type = None
        content_length = None
        try:
            head = client.head(url, headers=headers)
            content_type = head.headers.get("content-type")
            cl = head.headers.get("content-length")
            content_length = int(cl) if cl and cl.isdigit() else None
        except Exception:
            pass

        if content_length is not None and content_length > settings.max_download_bytes:
            raise DownloadTooLargeError(
                f"Content-Length {content_length} > MAX_DOWNLOAD_BYTES"
            )

        with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code >= 400:
                raise PipelineError(
                    "DOWNLOAD_FAILED", f"HTTP {resp.status_code} downloading URL"
                )
            content_type = content_type or resp.headers.get("content-type")
            cl = resp.headers.get("content-length")
            if cl and cl.isdigit():
                content_length = int(cl)
                if content_length > settings.max_download_bytes:
                    raise DownloadTooLargeError()

            if not _looks_like_media(content_type, ext):
                raise InvalidMediaError(
                    f"Content-Type={content_type!r} ext={ext!r} not allowed"
                )

            if not ext:
                guessed = mimetypes.guess_extension(
                    (content_type or "").split(";")[0].strip()
                )
                ext = guessed if guessed in ALLOWED_EXTS else ".bin"

            media_path = out_dir / f"media{ext}"
            written = 0
            with media_path.open("wb") as f:
                for chunk in resp.iter_bytes(1024 * 256):
                    written += len(chunk)
                    if written > settings.max_download_bytes:
                        media_path.unlink(missing_ok=True)
                        raise DownloadTooLargeError()
                    f.write(chunk)

    media_type = (
        "audio"
        if ext in {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus"}
        else "video"
    )
    meta = {
        "source_type": "direct_url",
        "source_url": url,
        "caption_source": "none",
        "local_media_path": str(media_path),
        "title": media_path.name,
        "duration_sec": None,
        "media_type": media_type,
        "used_account": None,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta
