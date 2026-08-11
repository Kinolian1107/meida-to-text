from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings
from app.pipeline.cloud_llm import CloudLLMClient, load_prompt_file
from app.pipeline.errors import CloudLlmFailedError

logger = logging.getLogger(__name__)


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

    if work_dir:
        (work_dir / "summary_latest.md").write_text(content, encoding="utf-8")
    return content
