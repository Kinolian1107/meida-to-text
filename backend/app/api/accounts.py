from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.models.schemas import AccountCreateYoutube, AccountItem
from app.pipeline.errors import PipelineError
from app.pipeline.youtube import probe_youtube
from app.security.account_store import (
    materialize_youtube_cookies,
    refresh_google_token,
)
from app.security.credentials import decrypt_text, encrypt_text

# re-export for callers that imported from this module
__all__ = [
    "router",
    "materialize_youtube_cookies",
    "refresh_google_token",
]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


def _public(row: dict) -> AccountItem:
    return AccountItem(
        id=row["id"],
        account_type=row["account_type"],
        account_label=row["account_label"],
        last_verified_at=row.get("last_verified_at"),
        status=row.get("status") or "active",
    )


@router.get("", response_model=list[AccountItem])
async def list_accounts(request: Request, account_type: str | None = None):
    store = request.app.state.store
    return [_public(r) for r in store.list_accounts(account_type)]


@router.post("/youtube", response_model=AccountItem)
async def create_youtube_account(request: Request, body: AccountCreateYoutube):
    settings = request.app.state.settings
    store = request.app.state.store
    text = body.cookies_text.strip()
    if not text or "# Netscape HTTP Cookie File" not in text and "\t" not in text:
        # still allow non-header netscape-ish TSV
        if not text or len(text) < 20:
            raise HTTPException(400, "cookies_text 看起來不是有效的 cookies.txt")
    blob = encrypt_text(settings, text)
    account_id = store.create_account(
        account_type="youtube_cookie",
        account_label=body.account_label.strip() or "YouTube",
        credential_encrypted=blob,
    )
    row = store.get_account(account_id)
    return _public(row)  # type: ignore[arg-type]


@router.post("/{account_id}/verify", response_model=AccountItem)
async def verify_account(request: Request, account_id: str):
    settings = request.app.state.settings
    store = request.app.state.store
    row = store.get_account(account_id, with_secret=True)
    if not row:
        raise HTTPException(404, "Account not found")

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    if row["account_type"] == "youtube_cookie":
        cookies = decrypt_text(settings, row["credential_encrypted"])
        tmp = settings.data_dir / "sqlite" / f"cookies-{account_id}.txt"
        tmp.write_text(cookies, encoding="utf-8")
        # temporarily point settings
        old = settings.youtube_cookies_file
        settings.youtube_cookies_file = str(tmp)
        try:
            probe_youtube("https://www.youtube.com/watch?v=jNQXAC9IVRw", settings)
            store.update_account(account_id, status="active", last_verified_at=now)
        except PipelineError as exc:
            store.update_account(account_id, status="invalid", last_verified_at=now)
            raise HTTPException(400, detail={"code": exc.code, "message": exc.message})
        except Exception as exc:
            store.update_account(account_id, status="invalid", last_verified_at=now)
            raise HTTPException(400, str(exc)) from exc
        finally:
            settings.youtube_cookies_file = old
            tmp.unlink(missing_ok=True)
    elif row["account_type"] == "google_drive_oauth":
        try:
            await refresh_google_token(settings, store, account_id, row)
            store.update_account(account_id, status="active", last_verified_at=now)
        except Exception as exc:
            store.update_account(account_id, status="invalid", last_verified_at=now)
            raise HTTPException(400, str(exc)) from exc
    else:
        raise HTTPException(400, "Unknown account type")

    return _public(store.get_account(account_id))  # type: ignore[arg-type]


@router.delete("/{account_id}")
async def delete_account(request: Request, account_id: str):
    store = request.app.state.store
    if not store.delete_account(account_id):
        raise HTTPException(404, "Account not found")
    return {"ok": True}


@router.get("/google/auth-url")
async def google_auth_url(request: Request, label: str = Query("Google Drive")):
    settings = request.app.state.settings
    if not settings.google_client_id:
        raise HTTPException(
            400,
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 未設定",
        )
    state = urlencode({"label": label})
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_DRIVE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return {"url": f"{GOOGLE_AUTH}?{urlencode(params)}"}


@router.get("/google/callback")
async def google_callback(
    request: Request, code: str = Query(...), state: str = Query("")
):
    settings = request.app.state.settings
    store = request.app.state.store
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(400, "Google OAuth not configured")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code >= 400:
        raise HTTPException(400, f"Token exchange failed: {resp.text[:500]}")
    data = resp.json()
    refresh = data.get("refresh_token")
    if not refresh:
        raise HTTPException(
            400,
            "未取得 refresh_token（請撤銷舊授權後重試，並確保 prompt=consent）",
        )
    label = "Google Drive"
    if state.startswith("label="):
        from urllib.parse import parse_qs

        label = parse_qs(state).get("label", ["Google Drive"])[0]

    payload = json.dumps(
        {
            "refresh_token": refresh,
            "token_type": data.get("token_type", "Bearer"),
            "scope": data.get("scope", GOOGLE_DRIVE_SCOPE),
        },
        ensure_ascii=False,
    )
    account_id = store.create_account(
        account_type="google_drive_oauth",
        account_label=label,
        credential_encrypted=encrypt_text(settings, payload),
    )
    from datetime import datetime, timezone

    store.update_account(
        account_id,
        status="active",
        last_verified_at=datetime.now(timezone.utc).isoformat(),
    )
    dest = settings.frontend_origin.rstrip("/") + "/accounts?google=ok"
    return RedirectResponse(dest)
