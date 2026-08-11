from __future__ import annotations

"""Fernet encrypt for YouTube cookies / Drive OAuth tokens."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def _fernet(settings: Settings) -> Fernet:
    key = settings.ensure_fernet_key()
    return Fernet(key.encode("ascii") if isinstance(key, str) else key)


def encrypt_text(settings: Settings, plaintext: str) -> bytes:
    return _fernet(settings).encrypt(plaintext.encode("utf-8"))


def decrypt_text(settings: Settings, token: bytes) -> str:
    try:
        return _fernet(settings).decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt credentials") from exc


def encrypt_blob(key: str, plaintext: bytes) -> bytes:
    return Fernet(key.encode("ascii") if isinstance(key, str) else key).encrypt(
        plaintext
    )


def decrypt_blob(key: str, token: bytes) -> bytes:
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key).decrypt(
            token
        )
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt credentials") from exc
