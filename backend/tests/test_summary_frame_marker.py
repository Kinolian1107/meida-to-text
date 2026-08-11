from app.pipeline.summarize import _frame_lookup, inject_frame_images


def test_frame_lookup_only_includes_frame_segments_with_path():
    segments = [
        {"type": "frame", "start": 12.34, "frame_path": "frames/f1.jpg"},
        {"type": "frame", "start": 20.0, "frame_path": None},
        {"type": "speech", "start": 5.0, "text": "hello"},
    ]
    lookup = _frame_lookup(segments)
    assert lookup == {"12.3": "frames/f1.jpg"}


def test_inject_frame_images_replaces_known_marker():
    content = "- 重點一 {{frame:12.3}}\n- 重點二"
    out = inject_frame_images(content, "vid1", {"12.3": "frames/f1.jpg"})
    assert "![關鍵畫面 12.3s](/api/videos/vid1/frames/f1.jpg)" in out
    assert "{{frame:12.3}}" not in out
    assert "重點二" in out


def test_inject_frame_images_strips_unknown_marker():
    content = "- 重點一 {{frame:99.9}}"
    out = inject_frame_images(content, "vid1", {"12.3": "frames/f1.jpg"})
    assert "{{frame:99.9}}" not in out
    assert "/api/videos/" not in out
