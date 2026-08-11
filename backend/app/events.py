from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ProgressHub:
    """In-process pub/sub for WebSocket progress updates."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, video_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subs.setdefault(video_id, set()).add(q)
        return q

    async def unsubscribe(self, video_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            subs = self._subs.get(video_id)
            if not subs:
                return
            subs.discard(q)
            if not subs:
                self._subs.pop(video_id, None)

    async def publish(self, video_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            subs = list(self._subs.get(video_id, set()))
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass


progress_hub = ProgressHub()
