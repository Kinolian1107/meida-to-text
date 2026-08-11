from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.db.sqlite_store import SQLiteStore
from app.pipeline.errors import CloudLlmFailedError

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
MAX_RETRIES = 3
RETRY_BACKOFF_BASE_SECONDS = 2.0


@dataclass
class CloudLLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict[str, Any] | None = None


class CloudLLMClient:
    def __init__(self, settings: Settings, store: SQLiteStore | None = None) -> None:
        self.settings = settings
        self.store = store

    @property
    def configured(self) -> bool:
        return bool(self.settings.cloud_llm_base_url)

    async def complete(
        self,
        *,
        system: str,
        user: str,
        purpose: str,
        video_id: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> CloudLLMResult:
        if not self.configured:
            raise CloudLlmFailedError(
                "CLOUD_LLM_BASE_URL 未設定。請在 .env 填入本機雲端 LLM proxy。"
            )

        base = self.settings.cloud_llm_base_url.rstrip("/")
        url = base + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.settings.cloud_llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.cloud_llm_api_key}"

        payload: dict[str, Any] = {
            "model": self.settings.cloud_llm_model or "default",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # bridge-cursor-cli: ask + json is faster/stabler for headless校正/摘要
        if "18790" in base or "bridge" in base.lower():
            payload["metadata"] = {
                "cursor_mode": "ask",
                "cursor_force_output_format": "json",
            }

        data: dict[str, Any] | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(
                    "Cloud LLM request purpose=%s model=%s url=%s chars=%s attempt=%s/%s",
                    purpose,
                    payload["model"],
                    url,
                    len(system) + len(user),
                    attempt,
                    MAX_RETRIES,
                )
                # Cursor CLI via bridge can be slow on cold start / binary download
                async with httpx.AsyncClient(timeout=600.0) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                logger.info(
                    "Cloud LLM done purpose=%s status=%s", purpose, resp.status_code
                )
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status not in RETRYABLE_STATUS_CODES or attempt == MAX_RETRIES:
                    raise CloudLlmFailedError(str(exc)) from exc
                wait = RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Cloud LLM purpose=%s got %s (attempt %s/%s); retrying in %.1fs",
                    purpose,
                    status,
                    attempt,
                    MAX_RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES:
                    raise CloudLlmFailedError(str(exc)) from exc
                wait = RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Cloud LLM purpose=%s connection error (attempt %s/%s): %s; "
                    "retrying in %.1fs",
                    purpose,
                    attempt,
                    MAX_RETRIES,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
            except Exception as exc:
                raise CloudLlmFailedError(str(exc)) from exc

        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        result = CloudLLMResult(
            text=text,
            model=data.get("model") or self.settings.cloud_llm_model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            raw=data,
        )
        if self.store:
            self.store.log_llm_usage(
                video_id=video_id,
                purpose=purpose,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        return result


def load_prompt_file(settings: Settings, relative: str) -> str:
    path = settings.prompts_dir / relative
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")
