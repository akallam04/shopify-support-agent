"""Signed, client-held session state for the public sandbox: pending confirmation and sandbox changes."""

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

TOKEN_VERSION = 1
SESSION_TTL = timedelta(minutes=30)
MAX_EXECUTED = 10


@dataclass
class Session:
    sid: str = field(default_factory=lambda: secrets.token_hex(8))
    mutations: list[dict[str, Any]] = field(default_factory=list)
    pending_action: dict[str, Any] | None = None
    executed_actions: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(key: bytes, body: str) -> str:
    return _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def issue(session: Session, key: bytes, now: datetime) -> str:
    payload = {
        "v": TOKEN_VERSION,
        "sid": session.sid,
        "exp": int((now + SESSION_TTL).timestamp()),
        "nonce": secrets.token_hex(8),
        "mutations": session.mutations,
        "pending": session.pending_action,
        "executed": session.executed_actions[-MAX_EXECUTED:],
        "tokens": session.tokens_used,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(key, body)}"


def verify(token: str, key: bytes, now: datetime) -> Session | None:
    body, _, signature = token.partition(".")
    if not body or not signature or not hmac.compare_digest(signature, _sign(key, body)):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError):
        return None
    if payload.get("v") != TOKEN_VERSION or payload.get("exp", 0) <= now.timestamp():
        return None
    return Session(
        sid=payload["sid"],
        mutations=payload.get("mutations") or [],
        pending_action=payload.get("pending"),
        executed_actions=payload.get("executed") or [],
        tokens_used=int(payload.get("tokens") or 0),
    )


def session_label(session: Session) -> str:
    return hashlib.sha256(session.sid.encode()).hexdigest()[:10]
