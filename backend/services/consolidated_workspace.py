"""Trusted database/session adapters for opt-in consolidated scheduling."""
from __future__ import annotations

import os

from fastapi import HTTPException, Request

from backend.config import get_settings
from backend.database import Database
from backend.services.auth import AuthError, resolve_auth


def isolated_consolidation_enabled() -> bool:
    if os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") != "1":
        return False
    settings = get_settings()
    return (settings.local_test_mode and settings.app_env == "development"
            and os.getenv("MY_ISLAND_TEST_PROFILE") == "consolidation"
            and os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") == "1")


def _require_consolidated_store(database: Database) -> None:
    if os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") != "1":
        raise HTTPException(404, "Consolidated scheduling is disabled")
    # Never select a database from a request or fall back to a source connection.
    if not isolated_consolidation_enabled() and (get_settings().local_test_mode or not database.database_url
                                                or os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1"):
        raise HTTPException(409, "A single approved writable school database is required")


def require_consolidated_admin(database: Database, token: str | None) -> tuple[Database, str]:
    _require_consolidated_store(database)
    session = database.get_admin_browser_session(token) if token else None
    if not session or not database.user_has_role(session["user_id"], "admin"):
        raise HTTPException(401, "School administrator session is invalid or expired")
    return database, str(session["user_id"])

def require_consolidated_preview_admin(database: Database, token: str | None, request: Request) -> tuple[Database, str]:
    """Read-only previews accept ordinary human admin auth, never preview/diagnostic auth."""
    if any(request.headers.get(header) for header in ("Authorization", "X-Preview-Id", "X-Student-Preview-Id")):
        raise HTTPException(403, "School constructor requires a human administrator session")
    _require_consolidated_store(database)
    browser_auth = request.headers.get("X-Dev-Auth") or ""
    browser_token = browser_auth.removeprefix("browser:") if browser_auth.startswith("browser:") else None
    # Every token candidate is an opaque existing app session, including the
    # normal browser header from Sites or the server cookie adapter in main.
    for candidate in dict.fromkeys((token, browser_token)):
        if not candidate:
            continue
        session = database.get_admin_browser_session(candidate, touch=False)
        if session and database.user_has_role(session["user_id"], "admin"):
            return database, str(session["user_id"])
    signed = request.headers.get("X-Telegram-Init-Data")
    if signed:
        try:
            principal = resolve_auth(signed, None)
        except (AuthError, ValueError, TypeError) as error:
            raise HTTPException(401, "School administrator session is invalid or expired") from error
        # Do not trust a signed payload's display role or caller-declared role.
        user = database.find_user(principal["id"])
        if not user or not database.user_has_role(user["id"], "admin"):
            raise HTTPException(403, "Admin role required")
        return database, str(user["id"])
    raise HTTPException(401, "School administrator session is invalid or expired")
