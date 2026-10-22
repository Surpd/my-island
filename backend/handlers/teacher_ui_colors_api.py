"""Admin API for the school-wide teacher UI color registry."""
from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.database import Database
from backend.services.teacher_ui_colors import (
    TeacherColorConflict, TeacherColorError, confirm_settings_migration,
    get_color, initialize_missing, list_colors, preview_settings_migration,
    reset_manual_color, seed_approved, set_manual_color,
)


class ManualColorPayload(BaseModel):
    color: str
    expected_revision: int = Field(ge=0)


class RevisionPayload(BaseModel):
    expected_revision: int = Field(ge=0)


class SettingsPayload(BaseModel):
    settings: dict[str, Any]
    expected_revisions: dict[str, int] = Field(default_factory=dict)


def create_school_teacher_ui_colors_router(
    database_factory: Callable[[], Database],
    require_school_admin: Callable[[str | None], Any],
) -> APIRouter:
    """Mount after the school admin adapter; the caller owns trusted DB selection/auth."""
    router = APIRouter(prefix="/api/admin/school/teacher-ui-colors")

    def enabled() -> None:
        if os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") != "1":
            raise HTTPException(status_code=404, detail="Shared teacher colors are disabled")

    def admin(token: str | None) -> tuple[Database, str, str]:
        enabled()
        try:
            authenticated = require_school_admin(token)
            if isinstance(authenticated, tuple) and len(authenticated) >= 2 and isinstance(authenticated[0], Database):
                database, actor_id = authenticated[:2]
                tenant = str(authenticated[2]) if len(authenticated) >= 3 else str(getattr(database, "school_id", None) or "school")
                return database, str(actor_id), tenant
            actor_id = str(authenticated)
            database = database_factory()
            return database, actor_id, str(getattr(database, "school_id", None) or "school")
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(status_code=401, detail="School administrator session is invalid or expired") from error

    def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except TeacherColorConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except TeacherColorError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("")
    def colors(x_school_admin_session: str | None = Header(default=None)):
        database, _, tenant = admin(x_school_admin_session)
        return {"colors": list_colors(database, tenant)}

    @router.get("/{teacher_id}")
    def color(teacher_id: str, x_school_admin_session: str | None = Header(default=None)):
        database, _, tenant = admin(x_school_admin_session)
        return call(get_color, database, tenant, teacher_id)

    @router.put("/{teacher_id}")
    def update_color(teacher_id: str, payload: ManualColorPayload,
                     x_school_admin_session: str | None = Header(default=None)):
        database, actor_id, tenant = admin(x_school_admin_session)
        return call(set_manual_color, database, tenant, teacher_id, payload.color,
                    payload.expected_revision, actor_id)

    @router.post("/{teacher_id}/reset")
    def reset_color(teacher_id: str, payload: RevisionPayload,
                    x_school_admin_session: str | None = Header(default=None)):
        database, actor_id, tenant = admin(x_school_admin_session)
        return call(reset_manual_color, database, tenant, teacher_id,
                    payload.expected_revision, actor_id)

    @router.post("/initialize")
    def initialize(x_school_admin_session: str | None = Header(default=None)):
        database, actor_id, tenant = admin(x_school_admin_session)
        return call(initialize_missing, database, tenant, actor_id)

    @router.post("/seed-approved")
    def seed(x_school_admin_session: str | None = Header(default=None)):
        database, actor_id, tenant = admin(x_school_admin_session)
        return call(seed_approved, database, tenant, actor_id)

    @router.post("/settings/preview")
    def preview(payload: SettingsPayload, x_school_admin_session: str | None = Header(default=None)):
        database, _, tenant = admin(x_school_admin_session)
        return call(preview_settings_migration, database, tenant, payload.settings)

    @router.post("/settings/confirm")
    def confirm(payload: SettingsPayload, x_school_admin_session: str | None = Header(default=None)):
        database, actor_id, tenant = admin(x_school_admin_session)
        return call(confirm_settings_migration, database, tenant, payload.settings,
                    payload.expected_revisions, actor_id)

    return router
