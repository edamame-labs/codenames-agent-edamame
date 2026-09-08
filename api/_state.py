"""Encrypted, stateless game tokens.

The full game state (including the hidden key) is encrypted with a server-only
secret and handed to the browser as an opaque token. The client returns it on
each request; the server decrypts, advances the game, and re-issues a token.
This keeps the board key off the client (so a human guesser can't peek) without
requiring a database.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.fernet import Fernet, InvalidToken

INSECURE_DEFAULT_SECRET = "dev-insecure-secret-change-me"


def _app_secret() -> str:
    secret = (os.environ.get("CLUECAST_APP_SECRET") or "").strip()
    production = os.environ.get("VERCEL_ENV") == "production"
    if production and (not secret or secret == INSECURE_DEFAULT_SECRET):
        raise RuntimeError(
            "CLUECAST_APP_SECRET must be set to a unique value in production"
        )
    return secret or INSECURE_DEFAULT_SECRET


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_app_secret().encode("utf-8")).digest())
    return Fernet(key)


def encode(state: dict) -> str:
    raw = json.dumps(state, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decode(token: str) -> dict:
    try:
        raw = _fernet().decrypt(token.encode("ascii"))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("invalid or tampered game token") from exc
    return json.loads(raw.decode("utf-8"))
