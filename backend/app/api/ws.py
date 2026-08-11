from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.events import progress_hub
from app.models.schemas import STAGE_LABELS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


@router.websocket("/ws/videos/{video_id}/progress")
async def video_progress_ws(websocket: WebSocket, video_id: str):
    await websocket.accept()
    store = websocket.app.state.store
    q = await progress_hub.subscribe(video_id)
    try:
        # Send current snapshot immediately
        video = store.get_video(video_id)
        job = store.get_job(video_id) or {}
        if video:
            await websocket.send_json(
                {
                    "id": video_id,
                    "status": video.get("status"),
                    "stage_label": job.get("stage_label")
                    or STAGE_LABELS.get(video.get("status", ""), video.get("status")),
                    "progress": job.get("progress") or 0,
                    "error_code": video.get("error_code"),
                    "error_message": video.get("error_message"),
                }
            )
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=25.0)
                await websocket.send_json(payload)
                if payload.get("status") in ("ready", "failed"):
                    break
            except asyncio.TimeoutError:
                # keepalive ping
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error for %s", video_id)
    finally:
        await progress_hub.unsubscribe(video_id, q)
