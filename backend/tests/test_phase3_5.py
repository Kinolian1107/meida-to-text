from __future__ import annotations

from pathlib import Path

from app.db.sqlite_store import SQLiteStore
from app.pipeline.export_docs import build_export_bytes, timeline_to_markdown
from app.pipeline.merge import build_correction_diff


def test_correction_diff():
    before = [{"start": 0, "end": 1, "text": "你好 世界", "type": "speech"}]
    after = [{"start": 0, "end": 1, "text": "你好，世界", "type": "speech"}]
    diff = build_correction_diff(before, after)
    assert len(diff) == 1
    assert diff[0]["before"] == "你好 世界"
    assert diff[0]["after"] == "你好，世界"


def test_cross_analyses_store(tmp_path: Path):
    store = SQLiteStore(tmp_path / "t.db")
    vid = store.create_video(
        filename="a.mp4", media_type="video", source_type="upload_video"
    )
    sid = store.add_summary(video_id=vid, prompt_template="bullet_points", content="重點A")
    cid = store.create_cross_analysis(
        source_summary_ids=[sid, sid],
        user_prompt="test",
        result={"common_points": [{"text": "x", "sources": ["a"]}], "conflicts": []},
    )
    row = store.get_cross_analysis(cid)
    assert row is not None
    assert row["result"]["common_points"][0]["text"] == "x"
    assert len(store.list_cross_analyses()) == 1


def test_export_md_and_docx():
    md = timeline_to_markdown(
        title="demo",
        segments=[{"start": 0, "end": 1, "type": "speech", "text": "hello"}],
        summary="- point",
    )
    data, ctype, name = build_export_bytes("md", "demo", md)
    assert b"hello" in data
    assert name.endswith(".md")
    data2, ctype2, name2 = build_export_bytes("docx", "demo", md)
    assert len(data2) > 100
    assert name2.endswith(".docx")
    assert "wordprocessingml" in ctype2
