import json

from app.pipeline.errors import CloudLlmFailedError
from app.pipeline.merge import C1_CHUNK_CHAR_LIMIT, _chunk_by_text_length, correct_transcript


def _seg(start: float, text: str) -> dict:
    return {"start": start, "end": start + 1.0, "text": text}


def test_chunk_by_text_length_splits_only_between_segments():
    segments = [_seg(0, "a" * 3000), _seg(1, "b" * 3000), _seg(2, "c" * 100)]
    chunks = _chunk_by_text_length(segments, max_chars=5000)

    assert len(chunks) == 2
    assert chunks[0] == [segments[0]]
    assert chunks[1] == [segments[1], segments[2]]
    for chunk in chunks:
        for seg in chunk:
            assert seg["text"] in {"a" * 3000, "b" * 3000, "c" * 100}


def test_chunk_by_text_length_keeps_oversized_segment_whole():
    segments = [_seg(0, "x" * 9000), _seg(1, "y")]
    chunks = _chunk_by_text_length(segments, max_chars=5000)

    assert len(chunks) == 2
    assert chunks[0] == [segments[0]]
    assert chunks[1] == [segments[1]]


def test_chunk_by_text_length_single_chunk_when_under_limit():
    segments = [_seg(0, "short"), _seg(1, "also short")]
    chunks = _chunk_by_text_length(segments, max_chars=5000)
    assert chunks == [segments]


class _StubClient:
    """Fake CloudLLMClient: echoes each chunk's segments back uppercased."""

    def __init__(self, fail_on_chunk: int | None = None):
        self.configured = True
        self.calls: list[str] = []
        self._fail_on_chunk = fail_on_chunk
        self._n = 0

    async def complete(self, *, system, user, purpose, video_id=None, max_tokens=4096, temperature=0.2):
        self._n += 1
        self.calls.append(user)
        if self._fail_on_chunk == self._n:
            raise CloudLlmFailedError("simulated 502")
        payload_start = user.index("[")
        payload = json.loads(user[payload_start:])
        fixed = [{"text": item["text"].upper()} for item in payload]

        class _Result:
            text = json.dumps(fixed, ensure_ascii=False)
            model = "stub-model"

        return _Result()


async def test_correct_transcript_stitches_chunks_in_order(monkeypatch):
    from app.config import Settings
    from app.pipeline import merge as merge_module

    monkeypatch.setattr(merge_module, "load_prompt_file", lambda settings, rel: "system prompt")

    segments = [_seg(0, "a" * 3000), _seg(1, "b" * 3000), _seg(2, "c" * 100)]
    client = _StubClient()

    fixed, meta = await correct_transcript(
        client,
        Settings(),
        segments,
        video_id="vid1",
        caption_source="asr",
    )

    assert len(client.calls) == 2  # matches the two chunks from the split test
    assert [s["text"] for s in fixed] == ["A" * 3000, "B" * 3000, "C" * 100]
    assert meta["status"] == "ok"
    assert meta["model"] == "stub-model"


async def test_correct_transcript_falls_back_per_chunk_on_failure(monkeypatch):
    from app.config import Settings
    from app.pipeline import merge as merge_module

    monkeypatch.setattr(merge_module, "load_prompt_file", lambda settings, rel: "system prompt")

    segments = [_seg(0, "a" * 3000), _seg(1, "b" * 3000), _seg(2, "c" * 100)]
    client = _StubClient(fail_on_chunk=1)

    fixed, meta = await correct_transcript(
        client,
        Settings(),
        segments,
        video_id="vid1",
        caption_source="asr",
    )

    # First chunk failed and keeps its original text; second chunk succeeded.
    assert fixed[0]["text"] == "a" * 3000
    assert fixed[1]["text"] == "B" * 3000
    assert fixed[2]["text"] == "C" * 100
    assert meta["status"] == "ok"  # at least one chunk succeeded
