"""Trusted database/session adapters for opt-in consolidated scheduling."""
from __future__ import annotations

import os

from fastapi import HTTPException

from backend.config import get_settings
from backend.database import Database


def isolated_consolidation_enabled() -> bool:
    if os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") != "1":
        return False
    settings = get_settings()
    return (settings.local_test_mode and settings.app_env == "development"
            and os.getenv("MY_ISLAND_TEST_PROFILE") == "consolidation"
            and os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") == "1")


def require_consolidated_admin(database: Database, token: str | None) -> tuple[Database, str]:
    if os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") != "1":
        raise HTTPException(404, "Consolidated scheduling is disabled")
    # Never select a database from a request or fall back to a source connection.
    if not isolated_consolidation_enabled() and (get_settings().local_test_mode or not database.database_url
                                                or os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1"):
        raise HTTPException(409, "A single approved writable school database is required")
    session = database.get_admin_browser_session(token) if token else None
    if not session or not database.user_has_role(session["user_id"], "admin"):
        raise HTTPException(401, "School administrator session is invalid or expired")
    return database, str(session["user_id"])
