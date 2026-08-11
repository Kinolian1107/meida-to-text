from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class GpuLock:
    """Serialize WhisperX vs llama-server on 16GB VRAM."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._holder: str | None = None

    @property
    def holder(self) -> str | None:
        return self._holder

    @asynccontextmanager
    async def acquire(self, name: str) -> AsyncIterator[None]:
        async with self._lock:
            self._holder = name
            logger.info("GPU lock acquired by %s", name)
            try:
                yield
            finally:
                logger.info("GPU lock released by %s", name)
                self._holder = None


gpu_lock = GpuLock()


async def llama_server_is_up(settings: Settings) -> bool:
    """True only if OpenAI-compatible /models returns JSON (not a random HTML server)."""
    url = settings.llama_server_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return False
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                text = resp.text.lstrip()[:32].lower()
                if text.startswith("<!doctype") or text.startswith("<html"):
                    return False
            data = resp.json()
            return isinstance(data, dict) and (
                "data" in data or "models" in data or "object" in data
            )
    except Exception:
        return False


async def ensure_llama_server_stopped_for_asr(settings: Settings) -> None:
    """Stop llama-server before ASR when auto-manage is on."""
    if not await llama_server_is_up(settings):
        return
    if settings.llama_auto_manage:
        from app.pipeline.llama_manager import stop_llama_server

        ok = await stop_llama_server(settings)
        if ok:
            logger.info("llama-server stopped for ASR")
        else:
            logger.warning("Failed to fully stop llama-server before ASR")
    else:
        logger.warning(
            "llama-server appears online during ASR window; "
            "stop it to avoid VRAM OOM (or set LLAMA_AUTO_MANAGE=true)"
        )
