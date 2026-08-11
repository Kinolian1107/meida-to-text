from app.pipeline.merge import merge_timeline_local


def test_merge_sorts_by_time():
    speech = [{"start": 10.0, "end": 12.0, "text": "hi"}]
    frames = [{"timestamp": 5.0, "text": "frame", "frame_path": "frames/5000.jpg"}]
    tl = merge_timeline_local(speech, frames)
    assert tl[0]["type"] == "frame"
    assert tl[1]["type"] == "speech"
    assert tl[1]["text"] == "hi"
