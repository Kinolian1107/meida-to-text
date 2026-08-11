from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import accounts, cross_analysis, prompts, tags, videos, ws, youtube_api
from app.config import get_settings
from app.db.lancedb_store import LanceDBStore
from app.db.sqlite_store import SQLiteStore
from app.models.schemas import HealthResponse
from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.youtube import ytdlp_version
from app.workers.job_worker import JobWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("media2text")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    store = SQLiteStore(settings.sqlite_path)
    lance = LanceDBStore(settings.lancedb_uri, embed_dim=settings.embed_dim)
    orchestrator = PipelineOrchestrator(settings, store, lance)
    worker = JobWorker(orchestrator)

    app.state.settings = settings
    app.state.store = store
    app.state.lance = lance
    app.state.orchestrator = orchestrator
    app.state.worker = worker

    ver = ytdlp_version()
    logger.info(
        "Starting media2text; yt-dlp=%s host=%s:%s queue=%s",
        ver,
        settings.api_host,
        settings.api_port,
        settings.job_queue_backend,
    )
    await worker.start()
    yield
    await worker.stop()


app = FastAPI(title="media2text", version="0.3.0", lifespan=lifespan)

settings = get_settings()
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
# Allow any origin in local WSL2/dev so HOST machine browsers work
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|[\d.]+)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(videos.router)
app.include_router(prompts.router)
app.include_router(youtube_api.router)
app.include_router(accounts.router)
app.include_router(cross_analysis.router)
app.include_router(tags.router)
app.include_router(ws.router)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", ytdlp_version=ytdlp_version())
