from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.config import Settings
from app.db.sqlite_store import SQLiteStore
from app.security.credentials import decrypt_text

GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


def materialize_youtube_cookies(
    settings: Settings, store: SQLiteStore, account_id: str | None
) -> Path | None:
    """Write decrypted cookies to a temp file for yt-dlp; return path."""
    if not account_id:
        if settings.youtube_cookies_file:
            p = Path(settings.youtube_cookies_file)
            return p if p.exists() else None
        return None
    row = store.get_account(account_id, with_secret=True)
    if not row or row["account_type"] != "youtube_cookie":
        return None
    cookies = decrypt_text(settings, row["credential_encrypted"])
    tmp = settings.data_dir / "sqlite" / f"cookies-run-{account_id}.txt"
    tmp.write_text(cookies, encoding="utf-8")
    return tmp


async def refresh_google_token(
    settings: Settings, store: SQLiteStore, account_id: str, row: dict
) -> str:
    creds = json.loads(decrypt_text(settings, row["credential_encrypted"]))
    refresh = creds.get("refresh_token")
    if not refresh:
        raise ValueError("missing refresh_token")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code >= 400:
        raise ValueError(f"refresh failed: {resp.text[:300]}")
    access = resp.json().get("access_token")
    if not access:
        raise ValueError("no access_token")
    return access
