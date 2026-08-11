from __future__ import annotations

import json

from app.config import Settings
from app.pipeline.tagging import _normalize_tags, generate_tags


def test_normalize_tags_dedupes_caps_and_falls_back_kind():
    raw = [
        {"name": "股癌", "kind": "show"},
        {"name": "股癌", "kind": "show"},  # duplicate
        {"name": "李永年", "kind": "speaker"},
        {"name": "美股", "kind": "nonsense"},  # invalid kind -> topic
        {"name": "", "kind": "topic"},  # empty name dropped
        {"name": "x" * 21, "kind": "topic"},  # too long, dropped
    ] + [{"name": f"t{i}", "kind": "topic"} for i in range(10)]  # exceed cap

    tags = _normalize_tags(raw)
    names = [t["name"] for t in tags]
    assert names.count("股癌") == 1
    assert {"name": "李永年", "kind": "speaker"} in tags
    assert {"name": "美股", "kind": "topic"} in tags
    assert len(tags) <= 8


def test_normalize_tags_rejects_non_list():
    assert _normalize_tags({"name": "x"}) == []
    assert _normalize_tags(None) == []


class _StubClient:
    def __init__(self, text: str, configured: bool = True):
        self.configured = configured
        self._text = text

    async def complete(self, *, system, user, purpose, video_id=None, max_tokens=512):
        class _R:
            text = self._text
            model = "stub"

        return _R()


async def test_generate_tags_happy_path(monkeypatch):
    from app.pipeline import tagging as tagging_module

    monkeypatch.setattr(tagging_module, "load_prompt_file", lambda settings, rel: "sys")
    client = _StubClient(json.dumps([{"name": "股癌", "kind": "show"}]))

    tags = await generate_tags(
        client, Settings(), video_id="v1", title="t", summary_text="重點內容"
    )
    assert tags == [{"name": "股癌", "kind": "show"}]


async def test_generate_tags_returns_empty_on_llm_failure(monkeypatch):
    from app.pipeline import tagging as tagging_module

    monkeypatch.setattr(tagging_module, "load_prompt_file", lambda settings, rel: "sys")

    class _BoomClient:
        configured = True

        async def complete(self, **kwargs):
            raise RuntimeError("cloud llm down")

    tags = await generate_tags(
        _BoomClient(), Settings(), video_id="v1", title="t", summary_text="重點內容"
    )
    assert tags == []


async def test_generate_tags_skips_when_not_configured():
    client = _StubClient("[]", configured=False)
    tags = await generate_tags(
        client, Settings(), video_id="v1", title="t", summary_text="重點內容"
    )
    assert tags == []


async def test_generate_tags_skips_on_empty_summary():
    client = _StubClient(json.dumps([{"name": "x", "kind": "topic"}]))
    tags = await generate_tags(
        client, Settings(), video_id="v1", title="t", summary_text="   "
    )
    assert tags == []
