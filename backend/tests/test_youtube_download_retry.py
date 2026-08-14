import pytest
from app.pipeline.errors import (
    PotProviderUnavailableError,
    VideoUnavailableError,
)
from app.pipeline.youtube import _download_media_with_retry


class _FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL; pops a scripted outcome per session.

    A new instance is constructed per attempt, mirroring how the retry loop
    rebuilds the session, so the shared `outcomes` list tracks call count.
    """

    def __init__(self, outcomes: list[Exception | None]):
        self._outcomes = outcomes

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        outcome = self._outcomes.pop(0)
        if outcome is not None:
            raise outcome


def _patch_ytdlp(monkeypatch, outcomes: list[Exception | None]) -> dict:
    calls = {"n": 0}

    def _make(opts):
        calls["n"] += 1
        return _FakeYoutubeDL(outcomes)

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _make)
    monkeypatch.setattr("app.pipeline.youtube.time.sleep", lambda _s: None)
    return calls


def test_retries_403_then_succeeds(monkeypatch):
    outcomes = [Exception("HTTP Error 403: Forbidden"), None]
    calls = _patch_ytdlp(monkeypatch, outcomes)

    _download_media_with_retry("https://youtu.be/x", {})

    assert calls["n"] == 2
    assert outcomes == []


def test_gives_up_after_max_attempts(monkeypatch):
    outcomes = [Exception("HTTP Error 403: Forbidden")] * 4
    calls = _patch_ytdlp(monkeypatch, outcomes)

    with pytest.raises(PotProviderUnavailableError):
        _download_media_with_retry("https://youtu.be/x", {})

    assert calls["n"] == 4


def test_does_not_retry_non_transient_error(monkeypatch):
    outcomes = [Exception("Video unavailable")]
    calls = _patch_ytdlp(monkeypatch, outcomes)

    with pytest.raises(VideoUnavailableError):
        _download_media_with_retry("https://youtu.be/x", {})

    assert calls["n"] == 1


def test_succeeds_first_try_without_sleeping(monkeypatch):
    outcomes = [None]
    calls = _patch_ytdlp(monkeypatch, outcomes)

    _download_media_with_retry("https://youtu.be/x", {})

    assert calls["n"] == 1
