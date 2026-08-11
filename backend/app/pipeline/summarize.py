from __future__ import annotations

import logging
import re
from pathlib import Path

from app.config import Settings
from app.pipeline.cloud_llm import CloudLLMClient, load_prompt_file
from app.pipeline.errors import CloudLlmFailedError

logger = logging.getLogger(__name__)

FRAME_MARKER_RE = re.compile(r"\{\{\s*frame\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*\}\}")

FRAME_MARKER_INSTRUCTION = (
    "\n\n【畫面標註規則】若摘要中某個重點主要依據畫面（影格）內容而非語音逐字稿，"
    "請在該重點文字結尾加上標註 `{{frame:TIMESTAMP}}`，"
    "TIMESTAMP 須完全等於時間軸中對應「[TIMESTAMPs][畫面]」項目的秒數"
    "（含小數點後一位，例如 12.3）。只有主要依據畫面內容的重點才需要標註，"
    "其餘重點不要加，也不要編造不存在的秒數。"
)


def _frame_lookup(segments: list[dict]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for seg in segments:
        if seg.get("type") == "frame" and seg.get("frame_path"):
            lookup[f"{seg.get('start', 0):.1f}"] = seg["frame_path"]
    return lookup


def inject_frame_images(content: str, video_id: str, frame_lookup: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        frame_path = frame_lookup.get(match.group(1))
        if not frame_path:
            return ""
        name = Path(frame_path).name
        return f"\n\n![關鍵畫面 {match.group(1)}s](/api/videos/{video_id}/frames/{name})\n"

    return FRAME_MARKER_RE.sub(replace, content)


def list_summary_templates(settings: Settings) -> list[dict[str, str]]:
    directory = settings.prompts_dir / "summary"
    if not directory.exists():
        return []
    items = []
    names = {
        "bullet_points": "條列重點摘要",
        "meeting_minutes": "會議紀要",
        "tutorial_outline": "教學章節大綱",
        "custom_default": "自由格式",
    }
    for path in sorted(directory.glob("*.txt")):
        tid = path.stem
        items.append(
            {
                "id": tid,
                "name": names.get(tid, tid),
                "content": path.read_text(encoding="utf-8"),
            }
        )
    return items


def timeline_to_text(segments: list[dict]) -> str:
    lines: list[str] = []
    for seg in segments:
        t = seg.get("type")
        start = seg.get("start", 0)
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if t == "frame":
            lines.append(f"[{start:.1f}s][畫面] {text}")
        else:
            end = seg.get("end", start)
            lines.append(f"[{start:.1f}-{end:.1f}s] {text}")
    return "\n".join(lines)


async def generate_summary(
    *,
    client: CloudLLMClient,
    settings: Settings,
    video_id: str,
    segments: list[dict],
    prompt_template: str = "bullet_points",
    custom_prompt: str | None = None,
    work_dir: Path | None = None,
) -> str:
    if custom_prompt:
        system = custom_prompt
    else:
        rel = f"summary/{prompt_template}.txt"
        try:
            system = load_prompt_file(settings, rel)
        except FileNotFoundError:
            system = load_prompt_file(settings, "summary/bullet_points.txt")

    frame_lookup = _frame_lookup(segments)
    if frame_lookup:
        system = system + FRAME_MARKER_INSTRUCTION

    body = timeline_to_text(segments)
    user = f"以下是影片時間軸內容，請依指示產出摘要：\n\n{body}"

    if not client.configured:
        # Offline fallback for smoke without cloud LLM
        bullets = []
        for seg in segments:
            if seg.get("type") == "speech" and seg.get("text"):
                bullets.append(f"- {seg['text'][:80]}")
            if len(bullets) >= 8:
                break
        content = "## 摘要（離線 fallback）\n\n" + (
            "\n".join(bullets) if bullets else "- （無內容）"
        )
        logger.warning("Cloud LLM not configured; using offline summary fallback")
    else:
        try:
            result = await client.complete(
                system=system,
                user=user,
                purpose="summary",
                video_id=video_id,
                max_tokens=3000,
            )
            content = result.text.strip()
        except CloudLlmFailedError:
            raise

    if frame_lookup:
        content = inject_frame_images(content, video_id, frame_lookup)

    if work_dir:
        (work_dir / "summary_latest.md").write_text(content, encoding="utf-8")
    return content
