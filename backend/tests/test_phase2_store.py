from pathlib import Path

from app.config import Settings
from app.db.sqlite_store import SQLiteStore
from app.security.credentials import decrypt_text, encrypt_text


def test_timeline_patch_and_accounts(tmp_path: Path):
    db = tmp_path / "t.db"
    store = SQLiteStore(db)
    settings = Settings(data_dir=tmp_path, sqlite_path=db)
    settings.ensure_dirs()

    vid = store.create_video(
        filename="a.wav", media_type="audio", source_type="upload_audio"
    )
    store.replace_timeline(
        vid,
        [
            {
                "id": "seg1",
                "start": 0,
                "end": 1,
                "type": "speech",
                "text": "hello",
            }
        ],
    )
    updated = store.update_timeline_segment(vid, "seg1", "hello world")
    assert updated is not None
    assert updated["text"] == "hello world"
    assert updated["edited"] == 1

    blob = encrypt_text(settings, "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t0\tA\tB\n")
    aid = store.create_account(
        account_type="youtube_cookie",
        account_label="test",
        credential_encrypted=blob,
    )
    row = store.get_account(aid, with_secret=True)
    assert row is not None
    plain = decrypt_text(settings, row["credential_encrypted"])
    assert "youtube.com" in plain

    store.upsert_prompt_template(tid="my_tpl", name="Mine", content="do stuff")
    tpls = store.list_prompt_templates()
    assert any(t["id"] == "my_tpl" for t in tpls)
