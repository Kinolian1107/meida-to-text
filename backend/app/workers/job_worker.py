from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.pipeline.orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)


class JobWorker:
    """Job broker: asyncio.Queue by default; optional ARQ when REDIS_URL set."""

    def __init__(self, orchestrator: PipelineOrchestrator) -> None:
        self.orchestrator = orchestrator
        self.settings = orchestrator.settings
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._use_arq = (
            self.settings.job_queue_backend == "arq" and bool(self.settings.redis_url)
        )

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        pending = self.orchestrator.store.list_pending_jobs()
        for job in pending:
            vid = job["video_id"]
            status = job["status"]
            if status != "pending":
                logger.info("Re-queue interrupted job %s (%s)", vid, status)
                self.orchestrator.store.update_job(
                    vid,
                    status="pending",
                    stage_label="排隊中",
                    progress=0,
                )
            await self.enqueue(vid)

        if self._use_arq:
            logger.info(
                "JobWorker ARQ mode (REDIS_URL set); "
                "run scripts/start_arq_worker.sh for external consumers. "
                "In-process consumer also active for local GPU safety."
            )
        self._task = asyncio.create_task(self._loop(), name="job-worker")
        logger.info("JobWorker started (backend=%s)", "arq" if self._use_arq else "asyncio")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("JobWorker stopped")

    async def enqueue(self, video_id: str) -> None:
        if self._use_arq:
            try:
                await self._enqueue_arq(video_id)
            except Exception:
                logger.exception("ARQ enqueue failed; falling back to asyncio queue")
                await self.queue.put(video_id)
        else:
            await self.queue.put(video_id)
        logger.info("Enqueued video %s (qsize=%s)", video_id, self.queue.qsize())

    async def _enqueue_arq(self, video_id: str) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        redis = await create_pool(RedisSettings.from_dsn(self.settings.redis_url))
        try:
            await redis.enqueue_job("run_pipeline_job", video_id)
        finally:
            await redis.close()

    async def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                video_id = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                await self.orchestrator.run(video_id)
            except Exception:
                logger.exception("Worker crashed on %s", video_id)
            finally:
                self.queue.task_done()
