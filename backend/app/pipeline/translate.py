from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.db.sqlite_store import SQLiteStore
from app.pipeline.cloud_llm import CloudLLMClient, load_prompt_file
from app.pipeline.merge import _extract_json

logger = logging.getLogger(__name__)

TRANSLATION_PROMPT = "subtitle_translation.txt"
TRANSLATION_META_KEY = "translation"
SUBTITLE_LANGS = ("zh", "en", "both")
# Per-segment cost of the JSON envelope around the text: braces, the "i"/"zh"
# keys, the index digits and the separating comma.
JSON_OVERHEAD_TOKENS_PER_SEGMENT = 12
# Gaps left by a failed batch get a second pass at a fraction of the budget —
# a whole batch usually dies because it was too big for the model's reply
# limit, and a quarter of it normally fits.
RETRY_BUDGET_DIVISOR = 4
MIN_CHUNK_TOKENS = 64
# A cue shorter than this flickers past unread; ASR sometimes emits sub-100ms
# segments, so every cue gets at least this much screen time when there is room.
MIN_CUE_SECONDS = 0.4

# Salvage pattern for a reply that got cut off mid-array: every complete
# {"i": N, "zh": "..."} object before the truncation point is still usable.
_TRANSLATION_OBJECT_RE = re.compile(
    r'\{\s*"i"\s*:\s*\d+\s*,\s*"zh"\s*:\s*"(?:[^"\\]|\\.)*"\s*\}'
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TranslationOutcome:
    translations: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    chunks_ok: int = 0
    chunks_failed: int = 0


def estimate_tokens(text: str) -> int:
    """Rough, deliberately pessimistic token count for a chunk budget.

    No tokenizer is bundled: tiktoken would only approximate whatever model sits
    behind the cloud proxy anyway, and it downloads its BPE tables on first use,
    which a local-first app should not depend on. CJK is charged 1.5 tokens per
    character and everything else 1 per 3.5 characters — both above the usual
    real ratios, so the estimate overshoots and chunks stay under the cap.
    """
    cjk = sum(1 for ch in text if ord(ch) >= 0x2E80)
    return math.ceil(cjk * 1.5 + (len(text) - cjk) / 3.5)


def _estimate_max_tokens(payload_tokens: int) -> int:
    """Reply budget: the same content in Chinese, plus JSON, plus headroom."""
    return min(max(4096, payload_tokens * 2 + 1024), 65536)


def _chunk_by_token_budget(
    items: list[dict[str, Any]], max_tokens: int
) -> list[list[dict[str, Any]]]:
    """Group segments so each request stays under max_tokens.

    A boundary only ever falls between segments: a single segment that blows the
    budget on its own is still sent whole rather than cut in half, because half a
    sentence cannot be translated and re-aligned afterwards.
    """
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0
    for item in items:
        cost = estimate_tokens(item["text"]) + JSON_OVERHEAD_TOKENS_PER_SEGMENT
        if current and current_tokens + cost > max_tokens:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(item)
        current_tokens += cost
    if current:
        chunks.append(current)
    return chunks


def _salvage_objects(text: str) -> list[dict[str, Any]]:
    """Pull whole {"i","zh"} objects out of a reply that failed to parse.

    A batch this large mostly fails by running into the model's output limit
    mid-array. Everything before the cut is still correctly indexed, so it can be
    placed on the right segments instead of being thrown away.
    """
    salvaged: list[dict[str, Any]] = []
    for match in _TRANSLATION_OBJECT_RE.finditer(text):
        try:
            salvaged.append(json.loads(match.group(0)))
        except json.JSONDecodeError:
            continue
    return salvaged


def _speech_items(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translatable payload rows: {id, i, text}, ordered by timeline position."""
    speech = [s for s in segments if s.get("type") == "speech"]
    speech.sort(key=lambda s: float(s.get("start") or 0.0))
    return [
        {"id": s["id"], "i": i, "text": (s.get("text") or "").strip()}
        for i, s in enumerate(speech)
        if (s.get("text") or "").strip()
    ]


def _parse_translation(raw: Any, chunk: list[dict[str, Any]]) -> dict[str, str]:
    """Map an LLM reply back onto segment ids.

    Prefers the `i` keys the prompt asks for, so a chunk that comes back with a
    few lines missing still lands the rest on the right segments; falls back to
    positional zip only when the reply has no usable keys and the count matches.
    """
    if isinstance(raw, dict):
        raw = raw.get("segments") or raw.get("items") or raw.get("translations") or []
    if not isinstance(raw, list):
        raise ValueError(f"expected a JSON array, got {type(raw).__name__}")

    by_index = {item["i"]: item for item in chunk}
    keyed = bool(raw) and all(isinstance(x, dict) and "i" in x for x in raw)
    out: dict[str, str] = {}

    if keyed:
        for entry in raw:
            try:
                idx = int(entry["i"])
            except (TypeError, ValueError):
                continue
            item = by_index.get(idx)
            zh = str(entry.get("zh") or entry.get("text") or "").strip()
            if item and zh:
                out[item["id"]] = zh
    else:
        if len(raw) != len(chunk):
            raise ValueError(
                f"unkeyed reply has {len(raw)} items, expected {len(chunk)}"
            )
        for item, entry in zip(chunk, raw):
            if isinstance(entry, str):
                zh = entry.strip()
            elif isinstance(entry, dict):
                zh = str(entry.get("zh") or entry.get("text") or "").strip()
            else:
                zh = ""
            if zh:
                out[item["id"]] = zh

    if not out:
        raise ValueError("no usable translations in reply")
    return out


def _parse_reply(text: str, chunk: list[dict[str, Any]]) -> dict[str, str]:
    try:
        return _parse_translation(_extract_json(text), chunk)
    except Exception:
        salvaged = _salvage_objects(text)
        if not salvaged:
            raise
        logger.warning(
            "Translation reply did not parse; salvaged %s/%s objects from it",
            len(salvaged),
            len(chunk),
        )
        return _parse_translation(salvaged, chunk)


async def _translate_pass(
    client: CloudLLMClient,
    *,
    system: str,
    items: list[dict[str, Any]],
    budget: int,
    video_id: str,
    outcome: TranslationOutcome,
    total: int,
    on_chunk: Callable[[dict[str, str], int, int], None] | None,
    pass_label: str,
) -> None:
    """Send `items` in as few requests as `budget` allows, folding results in."""
    chunks = _chunk_by_token_budget(items, budget)
    logger.info(
        "Translation %s video=%s: %s segs -> %s requests (budget=%s tokens)",
        pass_label,
        video_id,
        len(items),
        len(chunks),
        budget,
    )

    for n, chunk in enumerate(chunks, start=1):
        payload = json.dumps(
            [{"i": item["i"], "text": item["text"]} for item in chunk],
            ensure_ascii=False,
        )
        user = (
            "請把以下字幕 JSON 陣列翻譯成台灣繁體中文，"
            "回傳長度相同的 JSON 陣列，每個元素為 {\"i\": 同樣的 i, \"zh\": 翻譯}。\n\n"
            + payload
        )
        try:
            result = await client.complete(
                system=system,
                user=user,
                purpose="translate",
                video_id=video_id,
                max_tokens=_estimate_max_tokens(estimate_tokens(payload)),
            )
            mapping = _parse_reply(result.text, chunk)
            outcome.translations.update(mapping)
            outcome.model = result.model
            outcome.chunks_ok += 1
        except Exception:
            logger.exception(
                "Translation %s request %s/%s failed video=%s (%s segs)",
                pass_label,
                n,
                len(chunks),
                video_id,
                len(chunk),
            )
            outcome.chunks_failed += 1
            mapping = {}

        if on_chunk:
            on_chunk(mapping, len(outcome.translations), total)


async def translate_segments(
    client: CloudLLMClient,
    settings: Settings,
    segments: list[dict[str, Any]],
    *,
    video_id: str,
    on_chunk: Callable[[dict[str, str], int, int], None] | None = None,
) -> TranslationOutcome:
    """Translate speech segments to Traditional Chinese in as few calls as fit.

    Segments are packed up to `settings.translation_chunk_tokens` per request
    (never splitting a segment), then whatever the reply carries is re-aligned
    onto the right segments by its `i` index. Anything still missing afterwards —
    a request that failed outright, or lines the model dropped — gets one more
    pass at a quarter of the budget, so a single bad batch costs a retry rather
    than a hole in the subtitles.

    `on_chunk(mapping, translated_so_far, total)` fires after every request so
    callers can persist partial results while a long video is still running.
    """
    items = _speech_items(segments)
    outcome = TranslationOutcome()
    if not items:
        return outcome

    system = load_prompt_file(settings, TRANSLATION_PROMPT)
    budget = max(settings.translation_chunk_tokens, MIN_CHUNK_TOKENS)

    await _translate_pass(
        client,
        system=system,
        items=items,
        budget=budget,
        video_id=video_id,
        outcome=outcome,
        total=len(items),
        on_chunk=on_chunk,
        pass_label="pass 1",
    )

    missing = [item for item in items if item["id"] not in outcome.translations]
    retry_budget = max(budget // RETRY_BUDGET_DIVISOR, MIN_CHUNK_TOKENS)
    if missing and retry_budget < budget:
        await _translate_pass(
            client,
            system=system,
            items=missing,
            budget=retry_budget,
            video_id=video_id,
            outcome=outcome,
            total=len(items),
            on_chunk=on_chunk,
            pass_label="retry",
        )

    return outcome


# --- persisted job state (videos.meta_json["translation"]) -------------------


def get_translation_meta(store: SQLiteStore, video_id: str) -> dict[str, Any]:
    video = store.get_video(video_id) or {}
    try:
        meta = json.loads(video.get("meta_json") or "{}")
    except json.JSONDecodeError:
        return {}
    value = meta.get(TRANSLATION_META_KEY)
    return dict(value) if isinstance(value, dict) else {}


def update_translation_meta(
    store: SQLiteStore, video_id: str, **fields: Any
) -> dict[str, Any]:
    video = store.get_video(video_id) or {}
    try:
        meta = json.loads(video.get("meta_json") or "{}")
    except json.JSONDecodeError:
        meta = {}
    current = meta.get(TRANSLATION_META_KEY)
    updated = {
        **(current if isinstance(current, dict) else {}),
        **fields,
        "updated_at": _utc_now(),
    }
    store.update_video(video_id, meta={**meta, TRANSLATION_META_KEY: updated})
    return updated


async def run_translation_job(
    *,
    client: CloudLLMClient,
    settings: Settings,
    store: SQLiteStore,
    video_id: str,
) -> None:
    """Background entry point. Never raises — failures land in the job meta."""
    try:
        segments = store.get_timeline(video_id)
        outcome = await translate_segments(
            client,
            settings,
            segments,
            video_id=video_id,
            on_chunk=lambda mapping, done, total: _persist_chunk(
                store, video_id, mapping, done, total
            ),
        )
    except Exception as exc:
        logger.exception("Translation job crashed video=%s", video_id)
        update_translation_meta(
            store, video_id, status="failed", error=str(exc)
        )
        return

    if outcome.chunks_ok == 0 and outcome.chunks_failed > 0:
        update_translation_meta(
            store,
            video_id,
            status="failed",
            error="所有翻譯批次都失敗（雲端 LLM 無回應或格式錯誤）",
        )
        return

    update_translation_meta(
        store,
        video_id,
        status="done",
        model=outcome.model,
        error=(
            f"{outcome.chunks_failed} 個批次翻譯失敗，該區段維持原文"
            if outcome.chunks_failed
            else None
        ),
    )


def _persist_chunk(
    store: SQLiteStore,
    video_id: str,
    mapping: dict[str, str],
    done: int,
    total: int,
) -> None:
    if mapping:
        store.set_segment_translations(video_id, mapping)
    update_translation_meta(store, video_id, status="running", done=done, total=total)


# --- WebVTT -----------------------------------------------------------------


def _fmt_ts(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cue_body(segment: dict[str, Any], lang: str) -> str:
    original = (segment.get("text") or "").strip()
    zh = (segment.get("text_zh") or "").strip()
    if lang == "en":
        return _escape(original)
    if lang == "zh":
        return _escape(zh or original)
    # both: translation on top, original underneath in a dimmer class
    if not zh:
        return _escape(original)
    if not original:
        return _escape(zh)
    return f"{_escape(zh)}\n<c.orig>{_escape(original)}</c.orig>"


def build_vtt(segments: list[dict[str, Any]], lang: str = "zh") -> str:
    """Render speech segments as a WebVTT track for a <track> element."""
    if lang not in SUBTITLE_LANGS:
        raise ValueError(f"lang must be one of {SUBTITLE_LANGS}")
    speech = [s for s in segments if s.get("type") == "speech"]
    speech.sort(key=lambda s: float(s.get("start") or 0.0))

    lines = ["WEBVTT", ""]
    for i, segment in enumerate(speech):
        body = _cue_body(segment, lang)
        if not body:
            continue
        start = max(0.0, float(segment.get("start") or 0.0))
        end = max(float(segment.get("end") or 0.0), start + MIN_CUE_SECONDS)
        if i + 1 < len(speech):
            # Never overlap the next cue: two stacked cues cover twice the frame.
            next_start = float(speech[i + 1].get("start") or 0.0)
            end = min(end, max(next_start, start + 0.05))
        lines.append(str(i + 1))
        lines.append(f"{_fmt_ts(start)} --> {_fmt_ts(end)}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)
