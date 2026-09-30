from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal
from urllib.parse import parse_qs, urlparse, urlunparse

from app.pipeline.google_drive import extract_drive_file_id

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {
    "youtu.be",
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
}
MatchReason = Literal["youtube_id", "drive_id", "url", "filename"]


@dataclass(frozen=True)
class LookupQuery:
    source_type: str
    url: str | None = None
    filename: str | None = None
    size: int | None = None


@dataclass(frozen=True)
class MatchHit:
    item_index: int
    video: dict[str, Any]
    match_reason: MatchReason
    size_matched: bool | None = None


def extract_youtube_video_id(url: str) -> str | None:
    """Parse a YouTube watch / short / embed / share URL into an 11-char id."""
    text = (url or "").strip()
    if not text:
        return None
    if YOUTUBE_ID_RE.fullmatch(text):
        return text
    if "://" not in text:
        text = f"https://{text}"
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path_parts = [p for p in parsed.path.split("/") if p]
    query = parse_qs(parsed.query)

    if host == "youtu.be" and path_parts:
        candidate = path_parts[0]
        return candidate if YOUTUBE_ID_RE.fullmatch(candidate) else None

    if host not in YOUTUBE_HOSTS and not host.endswith(".youtube.com"):
        return None

    for value in query.get("v", []):
        if YOUTUBE_ID_RE.fullmatch(value):
            return value

    if len(path_parts) >= 2 and path_parts[0] in {"embed", "shorts", "live", "v", "watch"}:
        candidate = path_parts[1]
        if YOUTUBE_ID_RE.fullmatch(candidate):
            return candidate
    if len(path_parts) == 1 and YOUTUBE_ID_RE.fullmatch(path_parts[0]):
        return path_parts[0]
    return None


def normalize_http_url(url: str) -> str:
    """Canonicalize a direct media URL so http/https and trailing slashes match."""
    text = (url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    try:
        parsed = urlparse(text)
    except ValueError:
        return text.rstrip("/")
    host = (parsed.hostname or "").lower()
    if not host:
        return text.rstrip("/")
    scheme = (parsed.scheme or "https").lower()
    if scheme in {"http", "https"}:
        scheme = "https"
    port = parsed.port
    if port and not (
        (parsed.scheme == "http" and port == 80)
        or (parsed.scheme == "https" and port == 443)
        or (scheme == "https" and port in {80, 443})
    ):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def _video_meta(video: dict[str, Any]) -> dict[str, Any]:
    raw = video.get("meta")
    if raw is None:
        raw = video.get("meta_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def identity_for_query(query: LookupQuery) -> tuple[MatchReason, str] | None:
    source_type = query.source_type
    if source_type == "youtube":
        video_id = extract_youtube_video_id(query.url or "")
        if video_id:
            return ("youtube_id", video_id)
        normalized = normalize_http_url(query.url or "")
        return ("url", normalized) if normalized else None
    if source_type == "google_drive":
        file_id = extract_drive_file_id(query.url or "")
        return ("drive_id", file_id) if file_id else None
    if source_type == "direct_url":
        normalized = normalize_http_url(query.url or "")
        return ("url", normalized) if normalized else None
    if source_type == "upload":
        name = (query.filename or "").strip()
        return ("filename", name) if name else None
    return None


def identity_for_video(video: dict[str, Any]) -> tuple[MatchReason, str] | None:
    source_type = video.get("source_type") or ""
    url = video.get("source_url") or ""
    if source_type == "youtube":
        video_id = extract_youtube_video_id(url)
        if not video_id:
            meta_id = _video_meta(video).get("id")
            if isinstance(meta_id, str) and YOUTUBE_ID_RE.fullmatch(meta_id):
                video_id = meta_id
        if video_id:
            return ("youtube_id", video_id)
        normalized = normalize_http_url(url)
        return ("url", normalized) if normalized else None
    if source_type == "google_drive":
        file_id = extract_drive_file_id(url)
        return ("drive_id", file_id) if file_id else None
    if source_type == "direct_url":
        normalized = normalize_http_url(url)
        return ("url", normalized) if normalized else None
    if source_type in {"upload_video", "upload_audio"}:
        name = (video.get("filename") or "").strip()
        return ("filename", name) if name else None
    return None


def collect_matches(
    videos: Iterable[dict[str, Any]],
    queries: list[LookupQuery],
    upload_sizes: dict[str, int] | None = None,
) -> list[MatchHit]:
    """Return existing videos that match the incoming add requests.

    Videos should already be newest-first; hits keep that order within each item.
    Upload matches also require equal byte size when both sides know it.
    """
    sizes = upload_sizes or {}
    indexed = [(video, identity_for_video(video)) for video in videos]
    hits: list[MatchHit] = []
    for index, query in enumerate(queries):
        incoming = identity_for_query(query)
        if incoming is None:
            continue
        reason, _key = incoming
        for video, identity in indexed:
            if identity != incoming:
                continue
            size_matched: bool | None = None
            if reason == "filename":
                existing_size = sizes.get(video["id"])
                if query.size is not None and existing_size is not None:
                    if query.size != existing_size:
                        continue
                    size_matched = True
                elif query.size is not None:
                    # Incoming size is known but the library file is gone — do not
                    # treat a shared filename as a duplicate.
                    continue
                else:
                    size_matched = False
            hits.append(
                MatchHit(
                    item_index=index,
                    video=video,
                    match_reason=reason,
                    size_matched=size_matched,
                )
            )
    return hits
