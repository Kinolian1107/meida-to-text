from __future__ import annotations

from pathlib import Path

from app.db.sqlite_store import SQLiteStore


def _store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "t.db")


def _video(store: SQLiteStore) -> str:
    return store.create_video(filename="a.mp4", media_type="video", source_type="upload_video")


def test_add_and_list_video_tags(tmp_path: Path):
    store = _store(tmp_path)
    vid = _video(store)
    store.add_video_tag(vid, "股癌", "show", source="ai")
    store.add_video_tag(vid, "李永年", "speaker", source="ai")

    tags = store.get_tags_for_videos([vid])[vid]
    names = {t["name"] for t in tags}
    assert names == {"股癌", "李永年"}


def test_upsert_tag_is_idempotent_per_name_and_kind(tmp_path: Path):
    store = _store(tmp_path)
    tid1 = store.upsert_tag("股癌", "show")
    tid2 = store.upsert_tag("股癌", "show")
    tid3 = store.upsert_tag("股癌", "topic")  # different kind -> different tag
    assert tid1 == tid2
    assert tid1 != tid3


def test_replace_ai_tags_does_not_wipe_manual_tags(tmp_path: Path):
    store = _store(tmp_path)
    vid = _video(store)
    store.add_video_tag(vid, "手動標籤", "topic", source="manual")
    store.add_video_tag(vid, "舊AI標籤", "topic", source="ai")

    store.replace_ai_tags(vid, [{"name": "新AI標籤", "kind": "topic"}])

    tags = store.get_tags_for_videos([vid])[vid]
    names = {t["name"] for t in tags}
    assert names == {"手動標籤", "新AI標籤"}


def test_remove_video_tag(tmp_path: Path):
    store = _store(tmp_path)
    vid = _video(store)
    tag = store.add_video_tag(vid, "x", "topic", source="manual")
    store.remove_video_tag(vid, tag["id"])
    assert store.get_tags_for_videos([vid])[vid] == []


def test_list_tags_counts_usage(tmp_path: Path):
    store = _store(tmp_path)
    v1 = _video(store)
    v2 = _video(store)
    store.add_video_tag(v1, "股癌", "show", source="ai")
    store.add_video_tag(v2, "股癌", "show", source="ai")

    tags = store.list_tags()
    row = next(t for t in tags if t["name"] == "股癌")
    assert row["count"] == 2


def test_delete_video_cleans_up_video_tags(tmp_path: Path):
    store = _store(tmp_path)
    vid = _video(store)
    store.add_video_tag(vid, "x", "topic", source="manual")
    store.delete_video(vid)
    assert store.get_tags_for_videos([vid]) == {vid: []}
