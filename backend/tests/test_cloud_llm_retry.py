import httpx
import pytest

from app.config import Settings
from app.pipeline.cloud_llm import CloudLLMClient
from app.pipeline.errors import CloudLlmFailedError


def _request():
    return httpx.Request("POST", "http://127.0.0.1:18790/v1/chat/completions")


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {
            "choices": [{"message": {"content": "ok"}}],
            "model": "test-model",
            "usage": {},
        }

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"error {self.status_code}",
                request=_request(),
                response=httpx.Response(self.status_code, request=_request()),
            )

    def json(self) -> dict:
        return self._json


class _FakeAsyncClient:
    def __init__(self, responses):
        # Share the same list across retry attempts (a new httpx.AsyncClient
        # is constructed per attempt) so pops persist between calls.
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        return self._responses.pop(0)


def _settings() -> Settings:
    return Settings(
        cloud_llm_base_url="http://127.0.0.1:18790/v1",
        cloud_llm_api_key="",
        cloud_llm_model="test-model",
    )


async def _fake_sleep(seconds: float) -> None:
    return None


async def test_complete_retries_on_502_then_succeeds(monkeypatch):
    responses = [_FakeResponse(502), _FakeResponse(200)]
    monkeypatch.setattr(
        "app.pipeline.cloud_llm.httpx.AsyncClient",
        lambda timeout=None: _FakeAsyncClient(responses),
    )
    sleeps: list[float] = []

    async def _tracking_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.pipeline.cloud_llm.asyncio.sleep", _tracking_sleep)

    client = CloudLLMClient(_settings())
    result = await client.complete(system="sys", user="usr", purpose="c1")

    assert result.text == "ok"
    assert len(sleeps) == 1
    assert responses == []


async def test_complete_gives_up_after_max_retries(monkeypatch):
    responses = [_FakeResponse(502), _FakeResponse(502), _FakeResponse(502)]
    monkeypatch.setattr(
        "app.pipeline.cloud_llm.httpx.AsyncClient",
        lambda timeout=None: _FakeAsyncClient(responses),
    )
    monkeypatch.setattr("app.pipeline.cloud_llm.asyncio.sleep", _fake_sleep)

    client = CloudLLMClient(_settings())
    with pytest.raises(CloudLlmFailedError):
        await client.complete(system="sys", user="usr", purpose="c1")
    assert responses == []


async def test_complete_does_not_retry_on_4xx(monkeypatch):
    responses = [_FakeResponse(400)]
    calls = {"n": 0}

    def _make_client(timeout=None):
        calls["n"] += 1
        return _FakeAsyncClient(responses)

    monkeypatch.setattr("app.pipeline.cloud_llm.httpx.AsyncClient", _make_client)

    client = CloudLLMClient(_settings())
    with pytest.raises(CloudLlmFailedError):
        await client.complete(system="sys", user="usr", purpose="c1")
    assert calls["n"] == 1
