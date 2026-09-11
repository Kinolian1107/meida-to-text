from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.db.sqlite_store import SQLiteStore
from app.pipeline.translate import (
    _chunk_by_token_budget,
    _parse_translation,
    _salvage_objects,
    estimate_tokens,
    build_srt,
    build_vtt,
    get_translation_meta,
    run_translation_job,
    translate_segments,
    update_translation_meta,
)


def _segments(*texts: str) -> list[dict]:
    return [
        {
            "id": f"s{i}",
            "video_id": "v1",
            "start": float(i) * 2,
            "end": float(i) * 2 + 1.5,
            "type": "speech",
            "text": t,
            "text_zh": None,
        }
        for i, t in enumerate(texts)
    ]


class _StubClient:
    """Replies with a caller-supplied body per chunk; raises on None."""

    def __init__(self, replies: list, configured: bool = True):
        self.configured = configured
        self._replies = list(replies)
        self.calls: list[str] = []

    async def complete(self, *, system, user, purpose, video_id=None, max_tokens=4096):
        self.calls.append(user)
        reply = self._replies.pop(0)
        if reply is None:
            raise RuntimeError("cloud llm down")

        class _R:
            text = reply if isinstance(reply, str) else json.dumps(reply)
            model = "stub-model"

        return _R()


# --- _parse_translation -----------------------------------------------------


def test_parse_translation_maps_by_index_key():
    chunk = [{"id": "a", "i": 0, "text": "one"}, {"id": "b", "i": 1, "text": "two"}]
    # Reply order shuffled and one entry missing: keyed mapping must still be right.
    raw = [{"i": 1, "zh": "二"}, {"i": 0, "zh": "一"}]
    assert _parse_translation(raw, chunk) == {"a": "一", "b": "二"}


def test_parse_translation_keeps_partial_reply():
    chunk = [{"id": "a", "i": 0, "text": "one"}, {"id": "b", "i": 1, "text": "two"}]
    assert _parse_translation([{"i": 0, "zh": "一"}], chunk) == {"a": "一"}


def test_parse_translation_falls_back_to_position_when_unkeyed():
    chunk = [{"id": "a", "i": 0, "text": "one"}, {"id": "b", "i": 1, "text": "two"}]
    assert _parse_translation(["一", "二"], chunk) == {"a": "一", "b": "二"}


def test_parse_translation_rejects_unkeyed_length_mismatch():
    chunk = [{"id": "a", "i": 0, "text": "one"}, {"id": "b", "i": 1, "text": "two"}]
    with pytest.raises(ValueError):
        _parse_translation(["一"], chunk)


def test_parse_translation_rejects_empty_result():
    chunk = [{"id": "a", "i": 0, "text": "one"}]
    with pytest.raises(ValueError):
        _parse_translation([{"i": 9, "zh": "無關"}], chunk)


def test_parse_translation_unwraps_object_envelope():
    chunk = [{"id": "a", "i": 0, "text": "one"}]
    assert _parse_translation({"segments": [{"i": 0, "zh": "一"}]}, chunk) == {"a": "一"}


# --- translate_segments -----------------------------------------------------


# Each 350-char segment costs ceil(350 / 3.5) + 12 = 112 estimated tokens, so a
# 250-token budget packs exactly two of them per request.
_CHUNK_TEST_SETTINGS = Settings(translation_chunk_tokens=250)


async def test_translate_segments_packs_two_segments_per_request(tmp_path: Path):
    segments = _segments("a" * 350, "b" * 350, "c" * 350)
    client = _StubClient(
        [[{"i": 0, "zh": "甲"}, {"i": 1, "zh": "乙"}], [{"i": 2, "zh": "丙"}]]
    )
    progress: list[tuple[int, int]] = []

    outcome = await translate_segments(
        client,
        _CHUNK_TEST_SETTINGS,
        segments,
        video_id="v1",
        on_chunk=lambda mapping, done, total: progress.append((done, total)),
    )

    assert len(client.calls) == 2
    assert outcome.translations == {"s0": "甲", "s1": "乙", "s2": "丙"}
    assert outcome.model == "stub-model"
    assert outcome.chunks_failed == 0
    assert progress == [(2, 3), (3, 3)]


async def test_translate_segments_sends_one_request_when_it_all_fits(tmp_path: Path):
    segments = _segments("a" * 350, "b" * 350, "c" * 350)
    client = _StubClient(
        [[{"i": 0, "zh": "甲"}, {"i": 1, "zh": "乙"}, {"i": 2, "zh": "丙"}]]
    )

    outcome = await translate_segments(client, Settings(), segments, video_id="v1")

    assert len(client.calls) == 1
    assert len(outcome.translations) == 3


async def test_translate_segments_retries_a_failed_batch_in_smaller_pieces(
    tmp_path: Path,
):
    segments = _segments("a" * 350, "b" * 350, "c" * 350)
    # First request (segments 0+1) dies; second succeeds; the retry pass then
    # picks up the two segments the dead batch left behind.
    client = _StubClient(
        [None, [{"i": 2, "zh": "丙"}], [{"i": 0, "zh": "甲"}], [{"i": 1, "zh": "乙"}]]
    )

    outcome = await translate_segments(
        client, _CHUNK_TEST_SETTINGS, segments, video_id="v1"
    )

    assert outcome.translations == {"s0": "甲", "s1": "乙", "s2": "丙"}
    assert (outcome.chunks_ok, outcome.chunks_failed) == (3, 1)


async def test_translate_segments_retries_lines_the_model_dropped(tmp_path: Path):
    segments = _segments("a" * 350, "b" * 350)
    # The batch "succeeds" but silently omits segment 1.
    client = _StubClient([[{"i": 0, "zh": "甲"}], [{"i": 1, "zh": "乙"}]])

    outcome = await translate_segments(
        client, _CHUNK_TEST_SETTINGS, segments, video_id="v1"
    )

    assert outcome.translations == {"s0": "甲", "s1": "乙"}
    assert len(client.calls) == 2


async def test_translate_segments_gives_up_after_the_retry_pass(tmp_path: Path):
    segments = _segments("a" * 350)
    client = _StubClient([None, None])

    outcome = await translate_segments(
        client, _CHUNK_TEST_SETTINGS, segments, video_id="v1"
    )

    assert outcome.translations == {}
    assert (outcome.chunks_ok, outcome.chunks_failed) == (0, 2)


async def test_translate_segments_salvages_a_truncated_reply(tmp_path: Path):
    segments = _segments("a" * 350, "b" * 350)
    truncated = '[{"i": 0, "zh": "甲"}, {"i": 1, "zh": "乙'
    client = _StubClient([truncated, [{"i": 1, "zh": "乙"}]])

    outcome = await translate_segments(
        client, _CHUNK_TEST_SETTINGS, segments, video_id="v1"
    )

    # The complete object survived the truncation; the cut-off one came back
    # through the retry pass rather than being lost with the whole batch.
    assert outcome.translations == {"s0": "甲", "s1": "乙"}
    assert outcome.chunks_failed == 0


async def test_translate_segments_ignores_frames_and_blanks(tmp_path: Path):
    settings = Settings()
    segments = _segments("hello", "   ")
    segments.append(
        {
            "id": "f0",
            "video_id": "v1",
            "start": 1.0,
            "end": 1.0,
            "type": "frame",
            "text": "a screenshot",
            "text_zh": None,
        }
    )
    client = _StubClient([[{"i": 0, "zh": "哈囉"}]])

    outcome = await translate_segments(client, settings, segments, video_id="v1")

    assert outcome.translations == {"s0": "哈囉"}


async def test_translate_segments_no_speech_makes_no_llm_call(tmp_path: Path):
    settings = Settings()
    client = _StubClient([])
    outcome = await translate_segments(client, settings, [], video_id="v1")
    assert outcome.translations == {}
    assert client.calls == []


# --- run_translation_job ----------------------------------------------------


def _ready_video(store: SQLiteStore) -> str:
    video_id = store.create_video(
        filename="talk.mp4", media_type="video", source_type="upload_video"
    )
    store.replace_timeline(
        video_id,
        [
            {"id": "s0", "start": 0.0, "end": 2.0, "type": "speech", "text": "one"},
            {"id": "s1", "start": 2.0, "end": 4.0, "type": "speech", "text": "two"},
        ],
    )
    return video_id


async def test_run_translation_job_persists_text_and_marks_done(tmp_path: Path):
    settings = Settings()
    store = SQLiteStore(tmp_path / "t.db")
    video_id = _ready_video(store)
    client = _StubClient([[{"i": 0, "zh": "一"}, {"i": 1, "zh": "二"}]])

    await run_translation_job(
        client=client, settings=settings, store=store, video_id=video_id
    )

    rows = {r["id"]: r["text_zh"] for r in store.get_timeline(video_id)}
    assert rows == {"s0": "一", "s1": "二"}
    meta = get_translation_meta(store, video_id)
    assert meta["status"] == "done"
    assert meta["model"] == "stub-model"
    assert meta["error"] is None
    assert store.count_translatable_segments(video_id) == (2, 2)


async def test_run_translation_job_marks_failed_when_every_chunk_fails(tmp_path: Path):
    settings = Settings()
    store = SQLiteStore(tmp_path / "t.db")
    video_id = _ready_video(store)
    client = _StubClient([None])

    await run_translation_job(
        client=client, settings=settings, store=store, video_id=video_id
    )

    assert get_translation_meta(store, video_id)["status"] == "failed"
    assert store.count_translatable_segments(video_id) == (0, 2)


async def test_run_translation_job_never_raises_when_setup_explodes(
    tmp_path: Path, monkeypatch
):
    store = SQLiteStore(tmp_path / "t.db")
    video_id = _ready_video(store)

    def _boom(*args, **kwargs):
        raise FileNotFoundError("prompt missing")

    monkeypatch.setattr("app.pipeline.translate.load_prompt_file", _boom)

    await run_translation_job(
        client=_StubClient([]), settings=Settings(), store=store, video_id=video_id
    )

    meta = get_translation_meta(store, video_id)
    assert meta["status"] == "failed"
    assert meta["error"]


def test_update_translation_meta_preserves_other_meta_keys(tmp_path: Path):
    store = SQLiteStore(tmp_path / "t.db")
    video_id = store.create_video(
        filename="a.mp4",
        media_type="video",
        source_type="youtube",
        meta={"channel": "Sebastian"},
    )

    update_translation_meta(store, video_id, status="running")
    update_translation_meta(store, video_id, status="done", model="m")

    video = store.get_video(video_id)
    assert json.loads(video["meta_json"])["channel"] == "Sebastian"
    meta = get_translation_meta(store, video_id)
    assert (meta["status"], meta["model"]) == ("done", "m")
    assert meta["updated_at"]


# --- store bookkeeping ------------------------------------------------------


def test_rebuilding_timeline_drops_translations(tmp_path: Path):
    store = SQLiteStore(tmp_path / "t.db")
    video_id = _ready_video(store)
    store.set_segment_translations(video_id, {"s0": "一", "s1": "二"})
    assert store.count_translatable_segments(video_id) == (2, 2)

    store.replace_timeline(
        video_id,
        [{"id": "s0", "start": 0.0, "end": 2.0, "type": "speech", "text": "one again"}],
    )

    assert store.count_translatable_segments(video_id) == (0, 1)


def test_count_translatable_segments_ignores_frames(tmp_path: Path):
    store = SQLiteStore(tmp_path / "t.db")
    video_id = store.create_video(
        filename="a.mp4", media_type="video", source_type="upload_video"
    )
    store.replace_timeline(
        video_id,
        [
            {"id": "s0", "start": 0.0, "end": 1.0, "type": "speech", "text": "hi"},
            {
                "id": "f0",
                "start": 0.5,
                "end": 0.5,
                "type": "frame",
                "text": "slide",
                "frame_path": "f.jpg",
            },
        ],
    )
    assert store.count_translatable_segments(video_id) == (0, 1)


# --- WebVTT -----------------------------------------------------------------


def test_build_vtt_zh_uses_translation_and_falls_back_to_original():
    segments = [
        {"id": "a", "start": 0.0, "end": 6.6, "type": "speech", "text": "Hi", "text_zh": "嗨"},
        {"id": "b", "start": 6.6, "end": 11.4, "type": "speech", "text": "Bye", "text_zh": ""},
    ]
    vtt = build_vtt(segments, "zh")
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:06.600" in vtt
    assert "嗨" in vtt
    assert "Bye" in vtt  # untranslated line still shows, in the original language


def test_build_vtt_both_stacks_translation_over_original():
    segments = [
        {"id": "a", "start": 0.0, "end": 2.0, "type": "speech", "text": "Hi", "text_zh": "嗨"}
    ]
    assert "嗨\n<c.orig>Hi</c.orig>" in build_vtt(segments, "both")


def test_build_vtt_escapes_markup_characters():
    segments = [
        {
            "id": "a",
            "start": 0.0,
            "end": 2.0,
            "type": "speech",
            "text": "a < b && c",
            "text_zh": "",
        }
    ]
    body = build_vtt(segments, "en")
    assert "a &lt; b &amp;&amp; c" in body


def test_build_vtt_skips_frames_and_empty_cues():
    segments = [
        {"id": "f", "start": 1.0, "end": 1.0, "type": "frame", "text": "slide"},
        {"id": "a", "start": 0.0, "end": 2.0, "type": "speech", "text": "", "text_zh": ""},
    ]
    assert build_vtt(segments, "zh").strip() == "WEBVTT"


def test_build_vtt_clamps_overlapping_and_zero_length_cues():
    segments = [
        {"id": "a", "start": 0.0, "end": 5.0, "type": "speech", "text": "one"},
        {"id": "b", "start": 3.0, "end": 3.0, "type": "speech", "text": "two"},
    ]
    lines = build_vtt(segments, "en").splitlines()
    assert "00:00:00.000 --> 00:00:03.000" in lines  # trimmed to the next cue
    assert "00:00:03.000 --> 00:00:03.400" in lines  # zero-length cue gets min duration


def test_build_vtt_rejects_unknown_lang():
    with pytest.raises(ValueError):
        build_vtt([], "fr")


def test_build_srt_uses_comma_timestamps_and_plain_text():
    segments = [
        {"id": "a", "start": 0.0, "end": 6.6, "type": "speech", "text": "Hi", "text_zh": "嗨"},
        {"id": "b", "start": 6.6, "end": 11.4, "type": "speech", "text": "Bye", "text_zh": ""},
        {"id": "f", "start": 1.0, "end": 1.0, "type": "frame", "text": "slide"},
    ]
    srt = build_srt(segments, "zh")
    assert srt.startswith("1\n00:00:00,000 --> 00:00:06,600\n嗨\n")
    assert "Bye" in srt
    assert "<c.orig>" not in srt
    assert "WEBVTT" not in srt


def test_build_srt_both_stacks_without_markup():
    segments = [
        {"id": "a", "start": 0.0, "end": 2.0, "type": "speech", "text": "a < b", "text_zh": "嗨"}
    ]
    body = build_srt(segments, "both")
    assert "嗨\na < b" in body
    assert "&lt;" not in body


def test_build_srt_collapses_blank_lines_inside_a_cue():
    segments = [
        {
            "id": "a",
            "start": 0.0,
            "end": 2.0,
            "type": "speech",
            "text": "one\n\n\ntwo",
            "text_zh": "",
        }
    ]
    srt = build_srt(segments, "en")
    assert "one\ntwo" in srt
    assert "one\n\ntwo" not in srt
    assert srt.strip().count("\n\n") == 0


def test_build_srt_clamps_overlap_and_rejects_unknown_lang():
    segments = [
        {"id": "a", "start": 0.0, "end": 5.0, "type": "speech", "text": "one"},
        {"id": "b", "start": 3.0, "end": 3.0, "type": "speech", "text": "two"},
    ]
    lines = build_srt(segments, "en").splitlines()
    assert "00:00:00,000 --> 00:00:03,000" in lines
    assert "00:00:03,000 --> 00:00:03,400" in lines
    with pytest.raises(ValueError):
        build_srt([], "fr")


# --- token budgeting --------------------------------------------------------


def test_estimate_tokens_overshoots_real_ratios():
    ascii_text = "a" * 350
    assert estimate_tokens(ascii_text) == 100
    # CJK is charged more per character than ASCII, and never under 1 each.
    assert estimate_tokens("注意力機制") >= 5


def test_chunk_by_token_budget_packs_up_to_the_limit():
    items = [{"id": f"s{i}", "i": i, "text": "a" * 350} for i in range(5)]
    chunks = _chunk_by_token_budget(items, 250)
    assert [len(c) for c in chunks] == [2, 2, 1]


def test_chunk_by_token_budget_never_splits_an_oversized_segment():
    items = [
        {"id": "s0", "i": 0, "text": "a" * 100},
        {"id": "s1", "i": 1, "text": "b" * 10_000},
        {"id": "s2", "i": 2, "text": "c" * 100},
    ]
    chunks = _chunk_by_token_budget(items, 100)
    assert [[i["id"] for i in c] for c in chunks] == [["s0"], ["s1"], ["s2"]]


def test_default_settings_use_a_5000_token_budget():
    assert Settings().translation_chunk_tokens == 5000


def test_salvage_objects_keeps_complete_entries_only():
    text = '[{"i": 0, "zh": "一"}, {"i": 1, "zh": "有\\"引號"}, {"i": 2, "zh": "被截斷'
    assert _salvage_objects(text) == [
        {"i": 0, "zh": "一"},
        {"i": 1, "zh": '有"引號'},
    ]


def test_salvage_objects_returns_nothing_for_prose():
    assert _salvage_objects("抱歉，我無法翻譯這段內容。") == []
