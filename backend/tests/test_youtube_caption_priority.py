from app.pipeline.youtube import pick_caption


def test_manual_beats_auto():
    info = {
        "subtitles": {"zh-Hant": [{"ext": "vtt"}], "en": [{"ext": "vtt"}]},
        "automatic_captions": {"zh": [{"ext": "vtt"}]},
    }
    lang, kind, source = pick_caption(info)
    assert source == "manual"
    assert kind == "manual"
    assert lang == "zh-Hant"


def test_auto_when_no_manual():
    info = {
        "subtitles": {},
        "automatic_captions": {"zh-Hans": [{"ext": "vtt"}], "en": [{"ext": "vtt"}]},
    }
    lang, kind, source = pick_caption(info)
    assert source == "auto"
    assert kind == "auto"
    assert lang == "zh-Hans"


def test_none_when_empty():
    lang, kind, source = pick_caption({"subtitles": {}, "automatic_captions": {}})
    assert source == "none"
    assert lang is None
    assert kind is None


def test_zh_prefix_fallback():
    info = {
        "subtitles": {"zh-HK": [{"ext": "vtt"}]},
        "automatic_captions": {},
    }
    lang, kind, source = pick_caption(info)
    assert source == "manual"
    assert lang == "zh-HK"
