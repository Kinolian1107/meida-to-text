from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.pipeline.cloud_llm import CloudLLMClient, load_prompt_file

logger = logging.getLogger(__name__)

C1_CHUNK_CHAR_LIMIT = 5000


def _estimate_max_tokens(payload_chars: int) -> int:
    """Output needs room for the full corrected JSON; Chinese ≈ ~1–2 chars/token."""
    # Roughly 1.5× input size in tokens, clamped.
    estimate = max(4096, int(payload_chars * 1.2) + 1024)
    return min(estimate, 65536)


def _chunk_by_text_length(
    segments: list[dict[str, Any]], max_chars: int = C1_CHUNK_CHAR_LIMIT
) -> list[list[dict[str, Any]]]:
    """Group segments so each chunk's total text stays under max_chars.

    A chunk boundary only ever falls between segments — a segment's text is
    never split mid-way, even if that single segment alone exceeds max_chars.
    """
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for seg in segments:
        text_len = len(seg.get("text") or "")
        if current and current_chars + text_len > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(seg)
        current_chars += text_len
    if current:
        chunks.append(current)
    return chunks


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_correction_meta(work_dir: Path) -> dict[str, Any] | None:
    path = work_dir / "correction_meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read correction_meta.json")
        return None


def _write_correction_meta(work_dir: Path, meta: dict[str, Any]) -> None:
    (work_dir / "correction_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        if m:
            return json.loads(m.group(0))
        raise


async def correct_transcript(
    client: CloudLLMClient,
    settings: Settings,
    segments: list[dict[str, Any]],
    *,
    video_id: str,
    caption_source: str,
    on_progress: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Returns (segments, step_meta) where step_meta has model/status/reason."""
    if caption_source == "manual":
        logger.info("caption_source=manual: skip heavy C1")
        return segments, {
            "status": "skipped",
            "reason": "manual_caption",
            "model": None,
        }

    if not client.configured:
        logger.warning("Cloud LLM not configured; using raw transcript for C1")
        return segments, {
            "status": "skipped",
            "reason": "llm_not_configured",
            "model": None,
        }

    if not segments:
        return segments, {"status": "skipped", "reason": "no_segments", "model": None}

    system = load_prompt_file(settings, "transcript_correction.txt")
    if on_progress:
        on_progress("逐字稿校稿", 82)

    chunks = _chunk_by_text_length(segments)
    logger.info(
        "C1 chunked (%s segs, %s chunks, limit=%s chars)",
        len(segments),
        len(chunks),
        C1_CHUNK_CHAR_LIMIT,
    )

    fixed_all: list[dict[str, Any]] = []
    model_used: str | None = None
    any_ok = False
    for i, chunk in enumerate(chunks, start=1):
        payload = [
            {"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in chunk
        ]
        payload_json = json.dumps(payload, ensure_ascii=False)
        logger.info(
            "C1 chunk %s/%s (%s segs, %s chars)",
            i,
            len(chunks),
            len(chunk),
            len(payload_json),
        )
        if on_progress and len(chunks) > 1:
            # Spread progress across 82–89% so the UI visibly advances chunk
            # by chunk instead of sitting frozen on a flat number.
            step_progress = 82 + round((i - 1) / len(chunks) * 7)
            on_progress(f"逐字稿校稿（{i}/{len(chunks)}）", step_progress)
        user = (
            "請校正以下逐字稿 JSON 陣列中每個 segment 的 text。"
            "必須一次回傳完整同樣結構的 JSON 陣列，保留 start/end 不變，"
            "不可省略任何 segment。\n\n"
            + payload_json
        )
        try:
            result = await client.complete(
                system=system,
                user=user,
                purpose="c1",
                video_id=video_id,
                max_tokens=_estimate_max_tokens(len(payload_json)),
            )
            fixed = _extract_json(result.text)
            if isinstance(fixed, dict) and "segments" in fixed:
                fixed = fixed["segments"]
            if not isinstance(fixed, list) or len(fixed) != len(chunk):
                raise ValueError(
                    f"C1 chunk {i}/{len(chunks)} returned "
                    f"{len(fixed) if isinstance(fixed, list) else type(fixed)} items, "
                    f"expected {len(chunk)}"
                )
            fixed_all.extend(
                {**orig, "text": new.get("text", orig["text"])}
                for orig, new in zip(chunk, fixed)
            )
            model_used = result.model
            any_ok = True
        except Exception:
            logger.exception(
                "C1 chunk %s/%s failed; keeping original text for this chunk",
                i,
                len(chunks),
            )
            fixed_all.extend(chunk)

    status = "ok" if any_ok else "failed"
    reason = None if any_ok else "parse_failed"
    return fixed_all, {"status": status, "reason": reason, "model": model_used}


async def correct_frames(
    client: CloudLLMClient,
    settings: Settings,
    frames: list[dict[str, Any]],
    *,
    video_id: str,
    on_progress: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not frames:
        return [], {"status": "skipped", "reason": "no_frames", "model": None}
    if not client.configured:
        return frames, {
            "status": "skipped",
            "reason": "llm_not_configured",
            "model": None,
        }

    if on_progress:
        on_progress("畫格描述修正", 90)
    system = load_prompt_file(settings, "frame_correction.txt")
    payload = [
        {
            "timestamp": f.get("timestamp"),
            "frame_path": f.get("frame_path"),
            "text": f.get("text"),
        }
        for f in frames
    ]
    user = (
        "請修正以下畫格描述 JSON 陣列：去重、統一用詞。"
        "回傳同樣結構的 JSON 陣列。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    result = await client.complete(
        system=system, user=user, purpose="c2", video_id=video_id
    )
    try:
        fixed = _extract_json(result.text)
        if isinstance(fixed, dict):
            fixed = fixed.get("frames") or fixed.get("items") or []
        out = []
        for orig, new in zip(frames, fixed):
            out.append({**orig, "text": new.get("text", orig["text"])})
        return out, {"status": "ok", "reason": None, "model": result.model}
    except Exception:
        logger.exception("C2 parse failed; keeping original frames")
        return frames, {
            "status": "failed",
            "reason": "parse_failed",
            "model": result.model,
        }


def merge_timeline_local(
    speech_segments: list[dict[str, Any]],
    frame_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for s in speech_segments:
        timeline.append(
            {
                "id": str(uuid.uuid4()),
                "start": float(s["start"]),
                "end": float(s["end"]),
                "type": "speech",
                "text": s.get("text") or "",
                "frame_path": None,
                "edited": False,
                "speaker": s.get("speaker"),
            }
        )
    for f in frame_segments:
        ts = float(f.get("timestamp") or f.get("start") or 0)
        timeline.append(
            {
                "id": str(uuid.uuid4()),
                "start": ts,
                "end": ts,
                "type": "frame",
                "text": f.get("text") or "",
                "frame_path": f.get("frame_path"),
                "edited": False,
                "speaker": None,
            }
        )
    timeline.sort(key=lambda x: (x["start"], 0 if x["type"] == "speech" else 1))
    return timeline


def build_correction_diff(
    before_segments: list[dict[str, Any]],
    after_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pair by index/time and return changed text rows for UI diff."""
    items: list[dict[str, Any]] = []
    n = max(len(before_segments), len(after_segments))
    for i in range(n):
        b = before_segments[i] if i < len(before_segments) else {}
        a = after_segments[i] if i < len(after_segments) else {}
        before = (b.get("text") or "").strip()
        after = (a.get("text") or "").strip()
        if before == after:
            continue
        items.append(
            {
                "start": float(a.get("start", b.get("start", 0)) or 0),
                "end": float(a.get("end", b.get("end", 0)) or 0),
                "type": a.get("type") or b.get("type") or "speech",
                "before": before,
                "after": after,
            }
        )
    return items


async def run_merge(
    *,
    client: CloudLLMClient,
    settings: Settings,
    work_dir: Path,
    video_id: str,
    caption_source: str,
    transcript: dict[str, Any],
    frames: list[dict[str, Any]],
    on_progress: Any | None = None,
) -> list[dict[str, Any]]:
    raw_speech = transcript.get("segments") or []
    speech, c1_meta = await correct_transcript(
        client,
        settings,
        raw_speech,
        video_id=video_id,
        caption_source=caption_source,
        on_progress=on_progress,
    )
    frames_fixed, c2_meta = await correct_frames(
        client,
        settings,
        frames,
        video_id=video_id,
        on_progress=on_progress,
    )

    # Persist intermediates for correction diff UI
    (work_dir / "transcript.corrected.json").write_text(
        json.dumps({"segments": speech}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (work_dir / "frames.corrected.json").write_text(
        json.dumps(frames_fixed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    speech_diff = build_correction_diff(raw_speech, speech)
    frame_diff = build_correction_diff(frames, frames_fixed)
    (work_dir / "correction_diff.json").write_text(
        json.dumps(
            {"speech": speech_diff, "frames": frame_diff},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # C3: local merge only. Cloud C3 used to re-echo the full timeline JSON
    # (often 400+ caption segments / 80KB+) which hangs bridge-cursor-cli for
    # minutes; C1 already polished speech text in one shot.
    if on_progress:
        on_progress("合併時間軸", 94)
    timeline = merge_timeline_local(speech, frames_fixed)
    (work_dir / "timeline.pre_c3.json").write_text(
        json.dumps({"timeline": timeline}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    c3_meta: dict[str, Any] = {
        "status": "skipped",
        "reason": "local_merge_only",
        "model": None,
    }
    logger.info(
        "C3 using local merge (%s segments); cloud C3 disabled",
        len(timeline),
    )

    models_used = [
        m
        for m in (c1_meta.get("model"), c2_meta.get("model"), c3_meta.get("model"))
        if m
    ]
    primary_model = models_used[0] if models_used else None
    applied = any(s.get("status") == "ok" for s in (c1_meta, c2_meta, c3_meta))
    correction_meta = {
        "applied": applied,
        "model": primary_model,
        "models": {
            "c1": c1_meta.get("model"),
            "c2": c2_meta.get("model"),
            "c3": c3_meta.get("model"),
        },
        "steps": {"c1": c1_meta, "c2": c2_meta, "c3": c3_meta},
        "corrected_at": _utc_now(),
    }
    _write_correction_meta(work_dir, correction_meta)

    out = {
        "video_id": video_id,
        "timeline": timeline,
        "correction": {
            "model": primary_model,
            "applied": applied,
        },
    }
    (work_dir / "timeline.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return timeline
