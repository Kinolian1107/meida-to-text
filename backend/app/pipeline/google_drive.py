from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

from app.config import Settings
from app.pipeline.errors import AuthRequiredError, PipelineError

logger = logging.getLogger(__name__)

FILE_ID_RE = re.compile(
    r"(?:/file/d/|/open\?id=|id=)([a-zA-Z0-9_-]{10,})"
)
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus"}


def extract_drive_file_id(url: str) -> str | None:
    m = FILE_ID_RE.search(url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", url.strip()):
        return url.strip()
    return None


def _finalize_media(out_dir: Path, downloaded: Path, url: str, file_id: str) -> dict:
    ext = downloaded.suffix.lower() or ".bin"
    media_path = out_dir / f"media{ext}"
    if downloaded != media_path:
        if media_path.exists():
            media_path.unlink()
        downloaded.rename(media_path)
    media_type = "audio" if media_path.suffix.lower() in AUDIO_EXTS else "video"
    meta = {
        "source_type": "google_drive",
        "source_url": url,
        "caption_source": "none",
        "local_media_path": str(media_path),
        "title": media_path.name,
        "duration_sec": None,
        "media_type": media_type,
        "used_account": None,
        "drive_file_id": file_id,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def download_google_drive(
    url: str,
    out_dir: Path,
    settings: Settings,
    *,
    access_token: str | None = None,
) -> dict:
    """Public share via gdown; private via OAuth access_token (Phase 2)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    file_id = extract_drive_file_id(url)
    if not file_id:
        raise PipelineError("INVALID_DRIVE_URL", "無法解析 Google Drive file id")

    if access_token:
        return _download_with_oauth(file_id, url, out_dir, access_token)

    import gdown

    tmp_out = str(out_dir / "drive_download")
    try:
        result = gdown.download(
            id=file_id,
            output=tmp_out,
            quiet=False,
            fuzzy=True,
        )
    except Exception as exc:
        msg = str(exc)
        if "Permission" in msg or "403" in msg or "Cannot retrieve" in msg:
            raise AuthRequiredError(
                "無法下載：請將檔案設為「知道連結的人皆可檢視」，"
                "或在帳號管理授權 Google Drive"
            ) from exc
        raise PipelineError("DRIVE_DOWNLOAD_FAILED", msg) from exc

    if not result:
        raise AuthRequiredError(
            "gdown 下載失敗（可能非公開分享）。請改為「知道連結者可檢視」或使用 OAuth。"
        )

    downloaded = Path(result)
    if not downloaded.exists():
        candidates = [p for p in out_dir.iterdir() if p.is_file()]
        if not candidates:
            raise PipelineError("DRIVE_DOWNLOAD_FAILED", "下載後找不到檔案")
        downloaded = max(candidates, key=lambda p: p.stat().st_size)

    return _finalize_media(out_dir, downloaded, url, file_id)


def _download_with_oauth(
    file_id: str, url: str, out_dir: Path, access_token: str
) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    meta_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?fields=name,mimeType,size"
    with httpx.Client(timeout=httpx.Timeout(30.0, read=600.0)) as client:
        info = client.get(meta_url, headers=headers)
        if info.status_code == 401 or info.status_code == 403:
            raise AuthRequiredError("Google Drive 授權無效或無權限")
        if info.status_code >= 400:
            raise PipelineError("DRIVE_DOWNLOAD_FAILED", info.text[:500])
        file_meta = info.json()
        name = file_meta.get("name") or "drive.bin"
        ext = Path(name).suffix.lower() or ".bin"
        dest = out_dir / f"drive_oauth{ext}"
        dl = client.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media",
            headers=headers,
        )
        if dl.status_code >= 400:
            raise PipelineError("DRIVE_DOWNLOAD_FAILED", dl.text[:500])
        dest.write_bytes(dl.content)
    meta = _finalize_media(out_dir, dest, url, file_id)
    meta["title"] = name
    meta["used_account"] = "oauth"
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta
