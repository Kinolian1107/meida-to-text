from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.pipeline.errors import VlFailedError
from app.pipeline.gpu_lock import gpu_lock, llama_server_is_up

logger = logging.getLogger(__name__)


def _load_prompt(settings: Settings) -> str:
    path = settings.prompts_dir / "frame_description.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "你正在分析一支影片中的關鍵畫格。這是時間 {timestamp} 的畫面。\n"
        "請描述：畫面中的主要內容、可辨識的文字（若有）、場景類型。\n"
        "若與前一個畫格（{prev_summary}）內容相似，請簡短標註「延續前一畫面」並只描述變化之處。\n"
        "輸出限制在 80 字以內。"
    )


def _encode_image(path: Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")


async def describe_frames(
    *,
    settings: Settings,
    work_dir: Path,
    frames_index: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if frames_index is None:
        idx_path = work_dir / "frames_index.json"
        if not idx_path.exists():
            return []
        frames_index = json.loads(idx_path.read_text(encoding="utf-8"))

    if not frames_index:
        return []

    if not await llama_server_is_up(settings):
        raise VlFailedError(
            "llama-server 未啟動。請執行 scripts/start_llama_server.sh"
        )

    prompt_tmpl = _load_prompt(settings)
    results: list[dict[str, Any]] = []
    prev_summary = "（無）"
    frames_dir = work_dir / "frames"

    async with gpu_lock.acquire("qwen3-vl"):
        async with httpx.AsyncClient(timeout=120.0) as client:
            for frame in frames_index:
                img_path = frames_dir / frame["path"]
                if not img_path.exists():
                    continue
                timestamp = frame.get("timestamp", 0)
                prompt = prompt_tmpl.format(
                    timestamp=f"{timestamp:.1f}s",
                    prev_summary=prev_summary,
                )
                b64 = _encode_image(img_path)
                payload = {
                    "model": settings.llama_server_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{b64}"
                                    },
                                },
                            ],
                        }
                    ],
                    "temperature": 0.2,
                    "max_tokens": 256,
                }
                url = settings.llama_server_url.rstrip("/") + "/chat/completions"
                try:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"].strip()
                except Exception as exc:
                    raise VlFailedError(str(exc)) from exc

                item = {
                    "timestamp": timestamp,
                    "timestamp_ms": frame.get("timestamp_ms"),
                    "start": frame.get("start"),
                    "end": frame.get("end"),
                    "frame_path": f"frames/{frame['path']}",
                    "text": text,
                }
                results.append(item)
                prev_summary = text[:80]

    out = work_dir / "frame_descriptions.json"
    out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return results


def load_frame_descriptions(work_dir: Path) -> list[dict[str, Any]]:
    path = work_dir / "frame_descriptions.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
