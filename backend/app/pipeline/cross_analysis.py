from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import Settings
from app.db.sqlite_store import SQLiteStore
from app.pipeline.cloud_llm import CloudLLMClient, load_prompt_file

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
        raise


def _offline_result(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Heuristic fallback when cloud LLM is off."""
    common: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    titles = [i["title"] for i in items]
    # Extract first bullet-like lines as pseudo common points
    for item in items:
        for line in (item["content"] or "").splitlines():
            line = line.strip(" -•*\t")
            if 8 <= len(line) <= 120:
                common.append({"text": line, "sources": [item["title"]]})
                break
    if len(items) >= 2:
        conflicts.append(
            {
                "text": "（離線模式）未執行雲端比較；請設定 CLOUD_LLM_* 後重跑以取得衝突分析。",
                "sources": titles[:2],
            }
        )
    return {"common_points": common[:8], "conflicts": conflicts}


async def run_cross_analysis(
    *,
    client: CloudLLMClient,
    settings: Settings,
    store: SQLiteStore,
    summary_ids: list[str],
    user_prompt: str = "",
) -> dict[str, Any]:
    if len(summary_ids) < 2:
        raise ValueError("至少需要 2 份摘要")

    items: list[dict[str, Any]] = []
    for sid in summary_ids:
        row = store.get_summary(sid)
        if not row:
            raise ValueError(f"找不到摘要 {sid}")
        video = store.get_video(row["video_id"]) or {}
        title = video.get("filename") or row["video_id"]
        items.append(
            {
                "summary_id": sid,
                "video_id": row["video_id"],
                "title": title,
                "content": row["content"],
            }
        )

    if not client.configured:
        result = _offline_result(items)
    else:
        tmpl = load_prompt_file(settings, "cross_analysis.txt")
        blocks = []
        for i, it in enumerate(items, 1):
            blocks.append(f"【項目{i}：{it['title']}】\n{it['content']}")
        filled = (
            tmpl.replace("{N}", str(len(items)))
            .replace("{ITEMS}", "\n\n".join(blocks))
            .replace("{USER_PROMPT}", user_prompt or "無額外指令")
        )
        completion = await client.complete(
            system="你是跨檔案內容分析助手。只輸出合法 JSON。",
            user=filled,
            purpose="cross",
            video_id=None,
            max_tokens=4096,
        )
        try:
            parsed = _extract_json(completion.text)
            result = {
                "common_points": parsed.get("common_points") or [],
                "conflicts": parsed.get("conflicts") or [],
            }
        except Exception:
            logger.exception("Cross-analysis JSON parse failed")
            result = {
                "common_points": [{"text": completion.text[:2000], "sources": []}],
                "conflicts": [],
            }

    analysis_id = store.create_cross_analysis(
        source_summary_ids=summary_ids,
        user_prompt=user_prompt,
        result=result,
    )
    row = store.get_cross_analysis(analysis_id)
    assert row is not None
    return row
