"""Dedicated, admin-independent, revocable diagnostic sessions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import os
import re
from secrets import token_urlsafe
from uuid import uuid4

from backend.database import Database

SESSION_PREFIX = "mi_diag_"
READ_SCOPE = "diagnostic.read"
TTL = timedelta(minutes=15)


_KEY_FORMAT = re.compile(r"[A-Za-z0-9_-]{43,128}")
_DIGEST_FORMAT = re.compile(r"[a-f0-9]{64}")


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _configured_verifier() -> str | None:
    if os.getenv("MY_ISLAND_CODEX_DIAGNOSTICS") != "1":
        return None
    provided = os.getenv("MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256", "")
    if provided and not _DIGEST_FORMAT.fullmatch(provided):
        return None
    raw = os.getenv("MY_ISLAND_CODEX_DIAGNOSTIC_KEY")
    if raw is None:
        return provided or None
    # Use generated random text, not a memorable password. Format, diversity
    # and repetition checks catch accidental blanks and obvious placeholders;
    # they are not a substitute for cryptographic random generation.
    if not _KEY_FORMAT.fullmatch(raw) or len(set(raw)) < 20:
        return None
    if any(raw[index:] == raw[:-index] for index in range(1, len(raw) // 2 + 1)):
        return None
    derived = _hash(raw)
    if provided and not hmac.compare_digest(derived, provided):
        return None
    return derived


def configured_epoch() -> str | None:
    digest = _configured_verifier()
    return _hash("diagnostics epoch:" + digest) if digest else None


def bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    return token if scheme.lower() == "bearer" and re.fullmatch(r"mi_diag_[A-Za-z0-9_-]{43}", token) else None


def _audit(database: Database, connection, action: str, session_id: str, view: str | None = None) -> None:
    # Controlled enums/opaque session ID only. Never audit the credential,
    # bearer token, request query/body, path arguments, or diagnostic results.
    import json
    database.execute(connection, "INSERT INTO audit_log(action, entity_type, details) VALUES (?, ?, ?)",
                     (action, "codex_diagnostic_session", json.dumps({"session_id": session_id, **({"view": view} if view else {})})))


def exchange(database: Database, key: str | None) -> dict | None:
    digest = _configured_verifier()
    if not digest or not key or not _KEY_FORMAT.fullmatch(key):
        return None
    if not hmac.compare_digest(_hash(key), digest):
        return None
    epoch = _hash("diagnostics epoch:" + digest)
    token = SESSION_PREFIX + token_urlsafe(32)
    session_id, expires = uuid4().hex, datetime.now(timezone.utc) + TTL
    with database.connection() as connection:
        database.execute(connection, """INSERT INTO codex_diagnostic_sessions(id,token_hash,credential_epoch,scopes,expires_at)
            VALUES(?,?,?,?,?)""", (session_id, _hash(token), epoch, READ_SCOPE, expires.isoformat()))
        _audit(database, connection, "codex_diagnostics.login", session_id)
    return {"token": token, "expires_at": expires.isoformat(), "scope": READ_SCOPE}


def authorize(database: Database, token: str | None, *, scope: str = READ_SCOPE, view: str | None = None) -> str | None:
    epoch = configured_epoch()
    if not epoch or not token:
        return None
    now = datetime.now(timezone.utc)
    with database.connection() as connection:
        row = database.execute(connection, """SELECT id,scopes,expires_at FROM codex_diagnostic_sessions
            WHERE token_hash=? AND credential_epoch=? AND revoked_at IS NULL""", (_hash(token), epoch)).fetchone()
        if not row or scope not in row["scopes"].split():
            return None
        expires = row["expires_at"]
        if not isinstance(expires, datetime):
            expires = datetime.fromisoformat(expires)
        if expires <= now:
            return None
        session_id = str(row["id"])
        if view:
            _audit(database, connection, "codex_diagnostics.read", session_id, view)
        return session_id


def revoke(database: Database, session_id: str) -> bool:
    """Used by logout or an operator with authorized database access."""
    with database.connection() as connection:
        row = database.execute(connection, """UPDATE codex_diagnostic_sessions SET revoked_at=?
            WHERE id=? AND revoked_at IS NULL RETURNING id""", (datetime.now(timezone.utc).isoformat(), session_id)).fetchone()
        if row:
            _audit(database, connection, "codex_diagnostics.revoked", session_id)
        return bool(row)
