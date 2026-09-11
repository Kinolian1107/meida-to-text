from __future__ import annotations

from pathlib import Path

from app.db.sqlite_store import SQLiteStore
from fastapi.responses import Response

from app.api.videos import _subtitle_download_name
from app.pipeline.export_docs import (
    ascii_download_stem,
    build_export_bytes,
    content_disposition,
    timeline_to_markdown,
    utf8_download_stem,
)
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


def test_subtitle_download_name_keeps_dots_and_ascii_header():
    ascii_name, utf8_name = _subtitle_download_name("股癌 EP100.完整版", "zh", "srt")
    assert ascii_name == "EP100.zh.srt"
    assert utf8_name == "股癌_EP100.完整版.zh.srt"
    ascii_name2, utf8_name2 = _subtitle_download_name("20260908_社群目標討論.mp3", "zh", "srt")
    assert ascii_name2 == "20260908.zh.srt"
    assert utf8_name2 == "20260908_社群目標討論.zh.srt"
    ascii_name3, utf8_name3 = _subtitle_download_name("Mr. Robot S01", "en", "srt")
    assert ascii_name3 == "Mr._Robot_S01.en.srt"
    assert utf8_name3 == "Mr._Robot_S01.en.srt"
    header = content_disposition(ascii_name2, utf8_name2)
    Response(content="x", headers={"Content-Disposition": header})


def test_cjk_export_filename_is_latin1_safe():
    md = timeline_to_markdown(
        title="20260908_社群目標討論.mp3",
        segments=[{"start": 0, "end": 1, "type": "speech", "text": "hello"}],
    )
    data, _ctype, name = build_export_bytes("md", "20260908_社群目標討論.mp3", md)
    assert name == "20260908.md"
    assert ascii_download_stem("20260908_社群目標討論.mp3") == "20260908"
    assert utf8_download_stem("20260908_社群目標討論.mp3") == "20260908_社群目標討論"
    header = content_disposition(name, f"{utf8_download_stem('20260908_社群目標討論.mp3')}.md")
    header.encode("latin-1")
    assert "社群" not in header
    assert "filename*=UTF-8''" in header
    assert "%E7%A4%BE" in header
    Response(content=data, headers={"Content-Disposition": header})
    # Guard: CJK slipped into filename= must still be latin-1 safe.
    dirty = content_disposition("社群目標.md", "社群目標.md")
    dirty.encode("latin-1")
    assert "社群" not in dirty
