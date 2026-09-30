from pathlib import Path

from app.db.sqlite_store import SQLiteStore
from app.pipeline.source_identity import (
    LookupQuery,
    collect_matches,
    extract_youtube_video_id,
    identity_for_video,
    normalize_http_url,
)


def test_extract_youtube_watch_and_share_urls():
    vid = "dQw4w9WgXcQ"
    assert extract_youtube_video_id(f"https://www.youtube.com/watch?v={vid}&t=12s") == vid
    assert extract_youtube_video_id(f"https://youtu.be/{vid}?si=abc") == vid
    assert extract_youtube_video_id(f"https://m.youtube.com/watch?v={vid}") == vid
    assert extract_youtube_video_id(f"https://music.youtube.com/watch?v={vid}&list=RD") == vid
    assert extract_youtube_video_id(f"https://www.youtube.com/shorts/{vid}") == vid
    assert extract_youtube_video_id(f"https://www.youtube.com/embed/{vid}") == vid
    assert extract_youtube_video_id(f"https://www.youtube.com/live/{vid}") == vid
    assert extract_youtube_video_id(f"www.youtube.com/watch?v={vid}") == vid
    assert extract_youtube_video_id(vid) == vid


def test_extract_youtube_ignores_non_video_urls():
    assert extract_youtube_video_id("https://www.youtube.com/playlist?list=PLabc") is None
    assert extract_youtube_video_id("https://www.youtube.com/@channel") is None
    assert extract_youtube_video_id("https://example.com/watch?v=dQw4w9WgXcQ") is None
    assert extract_youtube_video_id("") is None


def test_normalize_http_url_collapses_scheme_and_slash():
    assert normalize_http_url("HTTP://CDN.Example.com/a/b.mp4/") == (
        "https://cdn.example.com/a/b.mp4"
    )
    assert normalize_http_url("https://cdn.example.com/a/b.mp4?token=1") == (
        "https://cdn.example.com/a/b.mp4?token=1"
    )


def test_youtube_identity_falls_back_to_probe_meta():
    video = {
        "source_type": "youtube",
        "source_url": "https://www.youtube.com/playlist?list=PLabc",
        "meta_json": '{"id": "dQw4w9WgXcQ"}',
    }
    assert identity_for_video(video) == ("youtube_id", "dQw4w9WgXcQ")


def test_collect_matches_youtube_across_url_shapes(tmp_path: Path):
    store = SQLiteStore(tmp_path / "t.db")
    first = store.create_video(
        filename="old title",
        media_type="video",
        source_type="youtube",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    newest = store.create_video(
        filename="new title",
        media_type="video",
        source_type="youtube",
        source_url="https://youtu.be/dQw4w9WgXcQ?si=zzz",
    )
    store.create_video(
        filename="other",
        media_type="video",
        source_type="youtube",
        source_url="https://youtu.be/aaaaaaaaaaa",
    )
    hits = collect_matches(
        store.list_videos(),
        [LookupQuery(source_type="youtube", url="https://www.youtube.com/shorts/dQw4w9WgXcQ")],
    )
    assert [h.video["id"] for h in hits] == [newest, first]
    assert hits[0].match_reason == "youtube_id"


def test_collect_matches_drive_and_direct_url(tmp_path: Path):
    store = SQLiteStore(tmp_path / "t.db")
    drive_id = "1AbCdEfGhIjKlMnOpQrsTuvWxYz"
    drive = store.create_video(
        filename="drive",
        media_type="video",
        source_type="google_drive",
        source_url=f"https://drive.google.com/file/d/{drive_id}/view",
    )
    direct = store.create_video(
        filename="clip.mp4",
        media_type="video",
        source_type="direct_url",
        source_url="http://cdn.example.com/clip.mp4/",
    )
    hits = collect_matches(
        store.list_videos(),
        [
            LookupQuery(
                source_type="google_drive",
                url=f"https://drive.google.com/open?id={drive_id}",
            ),
            LookupQuery(
                source_type="direct_url",
                url="https://cdn.example.com/clip.mp4",
            ),
        ],
    )
    assert {(h.item_index, h.video["id"], h.match_reason) for h in hits} == {
        (0, drive, "drive_id"),
        (1, direct, "url"),
    }


def test_collect_matches_upload_requires_size_when_both_known(tmp_path: Path):
    store = SQLiteStore(tmp_path / "t.db")
    same = store.create_video(
        filename="talk.mp4", media_type="video", source_type="upload_video"
    )
    other = store.create_video(
        filename="talk.mp4", media_type="video", source_type="upload_video"
    )
    sizes = {same: 1000, other: 2000}
    hits = collect_matches(
        store.list_videos(),
        [LookupQuery(source_type="upload", filename="talk.mp4", size=1000)],
        upload_sizes=sizes,
    )
    assert [h.video["id"] for h in hits] == [same]
    assert hits[0].size_matched is True

    no_size = collect_matches(
        store.list_videos(),
        [LookupQuery(source_type="upload", filename="talk.mp4")],
        upload_sizes=sizes,
    )
    assert {h.video["id"] for h in no_size} == {same, other}


def test_collect_matches_upload_skips_when_existing_size_unknown(tmp_path: Path):
    store = SQLiteStore(tmp_path / "t.db")
    store.create_video(
        filename="talk.mp4", media_type="video", source_type="upload_video"
    )
    hits = collect_matches(
        store.list_videos(),
        [LookupQuery(source_type="upload", filename="talk.mp4", size=1000)],
        upload_sizes={},
    )
    assert hits == []


def test_collect_matches_youtube_falls_back_to_normalized_url(tmp_path: Path):
    store = SQLiteStore(tmp_path / "t.db")
    playlist = "https://www.youtube.com/playlist?list=PLabc123456"
    vid = store.create_video(
        filename="playlist",
        media_type="video",
        source_type="youtube",
        source_url=playlist,
    )
    hits = collect_matches(
        store.list_videos(),
        [LookupQuery(source_type="youtube", url="http://www.youtube.com/playlist?list=PLabc123456")],
    )
    assert [h.video["id"] for h in hits] == [vid]
    assert hits[0].match_reason == "url"
