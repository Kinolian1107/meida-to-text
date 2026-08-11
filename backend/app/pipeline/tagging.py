from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.db.lancedb_store import LanceDBStore
from app.db.sqlite_store import SQLiteStore
from app.pipeline.cloud_llm import CloudLLMClient, load_prompt_file
from app.pipeline.embeddings import embed_text_remote
from app.pipeline.merge import _extract_json

logger = logging.getLogger(__name__)

VALID_KINDS = {"speaker", "show", "channel", "topic"}
MAX_TAGS = 8
MAX_NAME_LEN = 20


def _normalize_tags(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    seen: set[tuple[str, str]] = set()
    tags: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or len(name) > MAX_NAME_LEN:
            continue
        kind = str(item.get("kind") or "topic").strip().lower()
        if kind not in VALID_KINDS:
            kind = "topic"
        key = (name, kind)
        if key in seen:
            continue
        seen.add(key)
        tags.append({"name": name, "kind": kind})
        if len(tags) >= MAX_TAGS:
            break
    return tags


async def generate_tags(
    client: CloudLLMClient,
    settings: Settings,
    *,
    video_id: str,
    title: str,
    summary_text: str,
) -> list[dict[str, str]]:
    """Ask the cloud LLM for speaker/show/channel/topic tags for a video.

    Never raises — failures are logged and result in an empty tag list so a
    flaky LLM call never blocks summary generation.
    """
    if not client.configured or not (summary_text or "").strip():
        return []
    try:
        system = load_prompt_file(settings, "tag_generation.txt")
        user = f"標題：{title}\n\n摘要：\n{summary_text}"
        result = await client.complete(
            system=system, user=user, purpose="tagging", video_id=video_id, max_tokens=512
        )
        raw = _extract_json(result.text)
        return _normalize_tags(raw)
    except Exception:
        logger.exception("generate_tags failed video=%s", video_id)
        return []


async def finalize_summary_extras(
    *,
    client: CloudLLMClient,
    settings: Settings,
    store: SQLiteStore,
    lance: LanceDBStore,
    video_id: str,
    title: str,
    summary_row: dict[str, Any],
) -> None:
    """After a summary is persisted: compute its embedding and AI tags.

    Shared by the manual /summarize endpoint and the orchestrator's
    auto-generated first summary so both paths behave identically.
    Never raises — each half degrades independently on failure.
    """
    content = summary_row.get("content") or ""
    vector = await embed_text_remote(content, settings)
    lance.upsert_summary(summary_row, vector=vector)

    tags = await generate_tags(
        client, settings, video_id=video_id, title=title, summary_text=content
    )
    if tags:
        # Only overwrite AI tags when generation actually succeeded, so a
        # transient LLM failure doesn't wipe out a previously good set.
        store.replace_ai_tags(video_id, tags)
