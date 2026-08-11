from __future__ import annotations

import httpx

from app.config import Settings
from app.pipeline.embeddings import cosine, embed_text_remote


def _settings() -> Settings:
    return Settings(
        ollama_base_url="http://127.0.0.1:11434",
        ollama_embedding_model="bge-m3",
        embed_dim=4,
    )


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://127.0.0.1:11434/api/embed")
            raise httpx.HTTPStatusError(
                "error", request=request, response=httpx.Response(self.status_code, request=request)
            )

    def json(self) -> dict:
        return self._json


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        return self._response


async def test_embed_text_remote_returns_normalized_vector(monkeypatch):
    response = _FakeResponse(200, {"embeddings": [[3.0, 4.0, 0.0, 0.0]]})
    monkeypatch.setattr(
        "app.pipeline.embeddings.httpx.AsyncClient",
        lambda timeout=None: _FakeAsyncClient(response),
    )
    vec = await embed_text_remote("股癌 李永年", _settings())
    assert len(vec) == 4
    assert abs(cosine(vec, vec) - 1.0) < 1e-6


async def test_embed_text_remote_falls_back_to_zero_vector_on_failure(monkeypatch):
    response = _FakeResponse(500)
    monkeypatch.setattr(
        "app.pipeline.embeddings.httpx.AsyncClient",
        lambda timeout=None: _FakeAsyncClient(response),
    )
    vec = await embed_text_remote("測試", _settings())
    assert vec == [0.0, 0.0, 0.0, 0.0]


async def test_embed_text_remote_empty_text_short_circuits(monkeypatch):
    def _boom(timeout=None):
        raise AssertionError("should not call Ollama for empty text")

    monkeypatch.setattr("app.pipeline.embeddings.httpx.AsyncClient", _boom)
    vec = await embed_text_remote("   ", _settings())
    assert vec == [0.0, 0.0, 0.0, 0.0]
