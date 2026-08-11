from __future__ import annotations

"""Optional ARQ worker entrypoint.

Usage:
  REDIS_URL=redis://127.0.0.1:6379 JOB_QUEUE_BACKEND=arq \
    arq app.workers.arq_worker.WorkerSettings
"""

from arq.connections import RedisSettings

from app.config import get_settings
from app.db.lancedb_store import LanceDBStore
from app.db.sqlite_store import SQLiteStore
from app.pipeline.orchestrator import PipelineOrchestrator


async def run_pipeline_job(ctx, video_id: str) -> None:
    orch: PipelineOrchestrator = ctx["orchestrator"]
    await orch.run(video_id)


async def startup(ctx) -> None:
    settings = get_settings()
    store = SQLiteStore(settings.sqlite_path)
    lance = LanceDBStore(settings.lancedb_uri, embed_dim=settings.embed_dim)
    ctx["orchestrator"] = PipelineOrchestrator(settings, store, lance)


class WorkerSettings:
    functions = [run_pipeline_job]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(
        get_settings().redis_url or "redis://127.0.0.1:6379"
    )
    max_jobs = 1  # GPU safety
