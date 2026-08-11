from __future__ import annotations

import logging
import math
from typing import Sequence

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


async def embed_text_remote(text: str, settings: Settings) -> list[float]:
    """Embed text via the local Ollama server (shared with LazyBun).

    Returns a zero vector on any failure (Ollama down, model missing, etc.)
    so callers can keep storing a row without crashing — that row just won't
    surface in semantic search until the embedding is recomputed later.
    """
    dim = settings.embed_dim
    stripped = (text or "").strip()
    if not stripped:
        return [0.0] * dim
    url = f"{settings.ollama_base_url.rstrip('/')}/api/embed"
    payload = {"model": settings.ollama_embedding_model, "input": stripped}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        vec = data["embeddings"][0]
    except Exception:
        logger.exception("embed_text_remote failed (model=%s url=%s)", settings.ollama_embedding_model, url)
        return [0.0] * dim
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))
