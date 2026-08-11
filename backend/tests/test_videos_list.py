from __future__ import annotations

from pathlib import Path

from app.db.sqlite_store import SQLiteStore


def _store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "t.db")


def test_list_videos_filters_by_status(tmp_path: Path):
    store = _store(tmp_path)
    v1 = store.create_video(filename="a.mp4", media_type="video", source_type="upload_video")
    v2 = store.create_video(filename="b.mp4", media_type="video", source_type="upload_video")
    store.update_video(v2, status="ready")

    ready = store.list_videos(status="ready")
    assert [r["id"] for r in ready] == [v2]
    assert len(store.list_videos()) == 2
    assert v1  # keep reference used


def test_list_videos_keyword_matches_filename_or_summary(tmp_path: Path):
    store = _store(tmp_path)
    v1 = store.create_video(filename="股癌 EP100.mp4", media_type="video", source_type="upload_video")
    v2 = store.create_video(filename="other.mp4", media_type="video", source_type="upload_video")
    store.add_summary(video_id=v2, prompt_template="bullet_points", content="今天談股癌相關話題")

    by_filename = store.list_videos(keyword="股癌")
    ids = {r["id"] for r in by_filename}
    assert ids == {v1, v2}

    by_miss = store.list_videos(keyword="不存在的關鍵字")
    assert by_miss == []


def test_list_videos_tag_filter_requires_all_tags(tmp_path: Path):
    store = _store(tmp_path)
    v1 = store.create_video(filename="a.mp4", media_type="video", source_type="upload_video")
    v2 = store.create_video(filename="b.mp4", media_type="video", source_type="upload_video")
    show_tag = store.add_video_tag(v1, "股癌", "show", source="ai")
    speaker_tag = store.add_video_tag(v1, "李永年", "speaker", source="ai")
    store.add_video_tag(v2, "股癌", "show", source="ai")

    only_show = store.list_videos(tag_ids=[show_tag["id"]])
    assert {r["id"] for r in only_show} == {v1, v2}

    both = store.list_videos(tag_ids=[show_tag["id"], speaker_tag["id"]])
    assert {r["id"] for r in both} == {v1}


def test_list_videos_video_ids_restricts_and_orders(tmp_path: Path):
    store = _store(tmp_path)
    v1 = store.create_video(filename="a.mp4", media_type="video", source_type="upload_video")
    v2 = store.create_video(filename="b.mp4", media_type="video", source_type="upload_video")
    v3 = store.create_video(filename="c.mp4", media_type="video", source_type="upload_video")

    ordered = store.list_videos(video_ids=[v3, v1])
    assert [r["id"] for r in ordered] == [v3, v1]
    assert v2 not in [r["id"] for r in ordered]


def test_list_videos_empty_video_ids_short_circuits(tmp_path: Path):
    store = _store(tmp_path)
    store.create_video(filename="a.mp4", media_type="video", source_type="upload_video")
    assert store.list_videos(video_ids=[]) == []
