from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.pipeline.errors import (
    AuthRequiredError,
    CaptionEmptyError,
    CookieExpiredError,
    GeoBlockedError,
    LivestreamProcessingError,
    PotProviderUnavailableError,
    VideoUnavailableError,
    YtdlpExtractFailedError,
)

logger = logging.getLogger(__name__)

LANG_PRIORITY = ["zh-Hant", "zh-TW", "zh-Hans", "zh", "en"]

MEDIA_DOWNLOAD_ATTEMPTS = 4
MEDIA_DOWNLOAD_RETRY_BASE_SECONDS = 3


def ytdlp_version() -> str | None:
    try:
        import yt_dlp

        return getattr(yt_dlp, "version", None) and yt_dlp.version.__version__
    except Exception:
        return None


def _cookie_opts(settings: Settings) -> dict[str, Any]:
    opts: dict[str, Any] = {}
    if settings.youtube_cookies_file:
        path = Path(settings.youtube_cookies_file)
        if path.exists():
            opts["cookiefile"] = str(path)
    return opts


def _js_runtime_path(settings: Settings) -> str | None:
    """Locate the deno binary yt-dlp needs to solve YouTube's n challenges.

    The systemd unit execs .venv/bin/uvicorn directly, so .venv/bin never lands on
    PATH and a bare `which` misses the pip-installed deno. Resolving it next to the
    running interpreter covers that; None lets yt-dlp fall back to its own lookup.
    """
    if settings.ytdlp_js_runtime_path:
        return settings.ytdlp_js_runtime_path
    bundled = Path(sys.executable).with_name("deno")
    if bundled.exists():
        return str(bundled)
    return shutil.which("deno")


def _player_clients(settings: Settings) -> list[str]:
    return [c.strip() for c in settings.ytdlp_player_clients.split(",") if c.strip()]


def _ytdlp_base_opts(settings: Settings) -> dict[str, Any]:
    """Options shared by probing and downloading.

    Both paths must agree on the player client: probing with a different client
    would report a live_status and format availability the download never sees.
    """
    opts: dict[str, Any] = {
        "noplaylist": True,
        "js_runtimes": {"deno": {"path": _js_runtime_path(settings)}},
        **_cookie_opts(settings),
    }
    clients = _player_clients(settings)
    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": clients}}
    return opts


def _pick_caption(
    info: dict[str, Any], lang_priority: list[str] | None = None
) -> tuple[str | None, str | None, str]:
    """Return (lang, kind, caption_source) where kind is 'manual'|'auto'|None."""
    priority = lang_priority or LANG_PRIORITY
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    def match_lang(available: dict[str, Any]) -> str | None:
        keys = list(available.keys())
        for pref in priority:
            if pref in available:
                return pref
            # wildcard: zh.*
            prefix = pref.rstrip(".*") if pref.endswith(".*") else pref
            for k in keys:
                if k == pref or k.startswith(prefix):
                    return k
        # zh.* style sweep
        for k in keys:
            if k.startswith("zh"):
                return k
        return None

    lang = match_lang(manual)
    if lang:
        return lang, "manual", "manual"
    lang = match_lang(auto)
    if lang:
        return lang, "auto", "auto"
    return None, None, "none"


def probe_youtube(url: str, settings: Settings) -> dict[str, Any]:
    import yt_dlp

    opts: dict[str, Any] = {
        **_ytdlp_base_opts(settings),
        "quiet": True,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise _map_ytdlp_error(exc) from exc

    if not info:
        raise VideoUnavailableError()

    lang, kind, caption_source = _pick_caption(info)
    manual_langs = sorted((info.get("subtitles") or {}).keys())
    auto_langs = sorted((info.get("automatic_captions") or {}).keys())
    live_status = info.get("live_status")

    if caption_source == "manual":
        message = f"偵測到人工字幕（{lang}），將直接抓字幕並下載影片供畫格分析"
    elif caption_source == "auto":
        message = f"偵測到自動字幕（{lang}），將抓字幕並下載影片；品質可能不如本地 WhisperX"
    else:
        message = "無字幕，將下載影片後以 WhisperX 轉錄"

    if live_status == "post_live":
        message += "；⚠️ 直播剛結束，YouTube 尚在轉檔，現在處理可能因片段缺失而失敗，建議稍後再試"

    return {
        "title": info.get("title"),
        "duration_sec": info.get("duration"),
        "caption_source": caption_source,
        "caption_lang": lang,
        "caption_kind": kind,
        "message": message,
        "available_manual": manual_langs,
        "available_auto": auto_langs,
        "id": info.get("id"),
        "channel": info.get("channel") or info.get("uploader"),
        "channel_id": info.get("channel_id"),
        "live_status": live_status,
    }


def _map_ytdlp_error(exc: Exception) -> Exception:
    msg = str(exc)
    low = msg.lower()
    if "private video" in low or "sign in" in low or "login required" in low:
        if "cookie" in low or "cookies" in low:
            return CookieExpiredError(msg)
        return AuthRequiredError(msg)
    if "members-only" in low or "membership" in low:
        return AuthRequiredError(msg)
    if "geo" in low or "not available in your country" in low:
        return GeoBlockedError(msg)
    if "unavailable" in low or "does not exist" in low or "removed" in low:
        return VideoUnavailableError(msg)
    if "403" in low or "forbidden" in low:
        return PotProviderUnavailableError()
    return YtdlpExtractFailedError(msg)


def _strip_vtt_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_vtt_to_segments(vtt_text: str) -> list[dict[str, Any]]:
    lines = vtt_text.replace("\r\n", "\n").split("\n")
    segments: list[dict[str, Any]] = []
    i = 0
    time_re = re.compile(
        r"(\d{2}:)?\d{2}:\d{2}\.\d{3}\s+-->\s+(\d{2}:)?\d{2}:\d{2}\.\d{3}"
    )

    def to_sec(ts: str) -> float:
        parts = ts.split(":")
        if len(parts) == 3:
            h, m, s = parts
        else:
            h, m, s = "0", parts[0], parts[1]
        return int(h) * 3600 + int(m) * 60 + float(s)

    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line and time_re.search(line):
            start_s, end_s = [p.strip() for p in line.split("-->")]
            start_s = start_s.split()[0]
            end_s = end_s.split()[0]
            i += 1
            text_lines: list[str] = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(_strip_vtt_tags(lines[i]))
                i += 1
            text = _strip_vtt_tags(" ".join(text_lines))
            if text:
                segments.append(
                    {
                        "start": to_sec(start_s),
                        "end": to_sec(end_s),
                        "text": text,
                        "words": [],
                    }
                )
        i += 1

    # Deduplicate overlapping auto-caption duplicates
    deduped: list[dict[str, Any]] = []
    for seg in segments:
        if deduped and seg["text"] == deduped[-1]["text"] and abs(
            seg["start"] - deduped[-1]["start"]
        ) < 0.05:
            deduped[-1]["end"] = max(deduped[-1]["end"], seg["end"])
            continue
        deduped.append(seg)
    return deduped


def parse_srt_to_segments(srt_text: str) -> list[dict[str, Any]]:
    blocks = re.split(r"\n\s*\n", srt_text.replace("\r\n", "\n").strip())
    segments: list[dict[str, Any]] = []
    time_re = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
        r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
    )

    def hms_to_sec(h: str, m: str, s: str, ms: str) -> float:
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue
        m = time_re.search(lines[0] if "-->" in lines[0] else (lines[1] if len(lines) > 1 else ""))
        if not m:
            continue
        start = hms_to_sec(*m.group(1, 2, 3, 4))
        end = hms_to_sec(*m.group(5, 6, 7, 8))
        text_lines = lines[2:] if "-->" in lines[1] else lines[1:]
        if "-->" in lines[0]:
            text_lines = lines[1:]
        text = _strip_vtt_tags(" ".join(text_lines))
        if text:
            segments.append(
                {"start": start, "end": end, "text": text, "words": []}
            )
    return segments


def _is_retryable_download_error(exc: Exception) -> bool:
    """YouTube intermittently 403s a freshly-issued media URL; a new session fixes it."""
    low = str(exc).lower()
    return "403" in low or "forbidden" in low


def _download_media_with_retry(url: str, base_opts: dict[str, Any]) -> None:
    """Download media, retrying transient 403s with a fresh yt-dlp session.

    Each attempt builds a new YoutubeDL so the player response and PO token are
    re-fetched; reusing the old session would just replay the rejected URL.
    """
    import yt_dlp

    last_exc: Exception | None = None
    for attempt in range(MEDIA_DOWNLOAD_ATTEMPTS):
        try:
            with yt_dlp.YoutubeDL(base_opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            last_exc = exc
            is_last = attempt == MEDIA_DOWNLOAD_ATTEMPTS - 1
            if is_last or not _is_retryable_download_error(exc):
                raise _map_ytdlp_error(exc) from exc
            delay = MEDIA_DOWNLOAD_RETRY_BASE_SECONDS * (2**attempt)
            logger.warning(
                "Media download attempt %d/%d failed (%s); retrying in %ds",
                attempt + 1,
                MEDIA_DOWNLOAD_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)
        else:
            return

    # Unreachable: the final attempt either returns or raises above.
    raise _map_ytdlp_error(last_exc or YtdlpExtractFailedError())


def fetch_youtube(
    url: str, out_dir: Path, settings: Settings
) -> dict[str, Any]:
    """Download captions (if any) + video for frame analysis. Return meta dict."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    time.sleep(max(0, settings.ytdlp_sleep_seconds))

    probe = probe_youtube(url, settings)
    if probe.get("live_status") == "post_live":
        raise LivestreamProcessingError()
    caption_source = probe["caption_source"]
    caption_lang = probe["caption_lang"]

    media_tmpl = str(out_dir / "media.%(ext)s")
    base_opts: dict[str, Any] = {
        **_ytdlp_base_opts(settings),
        "quiet": False,
        "outtmpl": media_tmpl,
        "format": "bv*[height<=720]+ba/b[height<=720]/b",
        "merge_output_format": "mp4",
    }

    transcript: dict[str, Any] | None = None
    caption_path: Path | None = None

    if caption_source != "none" and caption_lang:
        sub_opts = {
            **base_opts,
            "skip_download": True,
            "writesubtitles": caption_source == "manual",
            "writeautomaticsub": caption_source == "auto",
            "subtitleslangs": [caption_lang],
            "subtitlesformat": "vtt",
        }
        try:
            with yt_dlp.YoutubeDL(sub_opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            logger.warning("Caption download failed, fallback WhisperX: %s", exc)
            caption_source = "none"
            caption_lang = None
        else:
            candidates = list(out_dir.glob("*.vtt")) + list(out_dir.glob("*.srt"))
            if not candidates:
                logger.warning("Caption empty on disk; fallback WhisperX")
                caption_source = "none"
            else:
                caption_path = candidates[0]
                raw = caption_path.read_text(encoding="utf-8", errors="replace")
                if caption_path.suffix.lower() == ".srt":
                    segments = parse_srt_to_segments(raw)
                else:
                    segments = parse_vtt_to_segments(raw)
                if not segments:
                    raise CaptionEmptyError()
                source_label = (
                    "youtube_caption_manual"
                    if caption_source == "manual"
                    else "youtube_caption_auto"
                )
                transcript = {
                    "source": source_label,
                    "language": caption_lang or "zh",
                    "segments": segments,
                }
                dest = out_dir / f"captions.raw{caption_path.suffix}"
                if caption_path != dest:
                    dest.write_text(raw, encoding="utf-8")
                    caption_path = dest

    # Always download video for frame analysis (spec)
    _download_media_with_retry(url, base_opts)

    media_files = [
        p
        for p in out_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3"}
        and not p.name.startswith("captions")
    ]
    if not media_files:
        raise YtdlpExtractFailedError("下載完成但找不到媒體檔")
    media_path = max(media_files, key=lambda p: p.stat().st_size)

    # Normalize name to media.{ext}
    final_media = out_dir / f"media{media_path.suffix.lower()}"
    if media_path != final_media:
        media_path.rename(final_media)
        media_path = final_media

    if transcript:
        (out_dir / "transcript.raw.json").write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    meta = {
        "source_type": "youtube",
        "source_url": url,
        "caption_source": caption_source,
        "caption_lang": caption_lang,
        "local_media_path": str(media_path),
        "title": probe.get("title"),
        "duration_sec": probe.get("duration_sec"),
        "used_account": None,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


# Exported for unit tests
pick_caption = _pick_caption
ytdlp_base_opts = _ytdlp_base_opts
