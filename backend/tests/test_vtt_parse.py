from app.pipeline.youtube import parse_srt_to_segments, parse_vtt_to_segments


def test_parse_vtt_strips_tags():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
<c>你好</c> <00:00:02.000><c>世界</c>

00:00:03.500 --> 00:00:05.000
第二句
"""
    segs = parse_vtt_to_segments(vtt)
    assert len(segs) >= 2
    assert "你好" in segs[0]["text"]
    assert "<" not in segs[0]["text"]


def test_parse_srt():
    srt = """1
00:00:01,000 --> 00:00:02,500
Hello

2
00:00:03,000 --> 00:00:04,000
World
"""
    segs = parse_srt_to_segments(srt)
    assert len(segs) == 2
    assert segs[0]["text"] == "Hello"
    assert segs[0]["start"] == 1.0
