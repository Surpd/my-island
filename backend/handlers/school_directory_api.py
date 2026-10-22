"""Local school directory API backed by the approved Supabase project.

The isolated demo API and schedule drafts keep using SQLite. Every request
here requires a browser session issued for a real school administrator.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.config import get_settings
from backend.database import Database
from backend.handlers.api import (
    GroupPayload, IdentityPayload, MembershipTransferPayload,
    TeacherAssignmentPayload,
)
from backend.services.admin import assign_teacher, create_group, create_identity
from backend.services.school_experiment_workspace import school_editor_directory, school_source, school_teacher_schedule


class SchoolCodePayload(BaseModel):
    code: str


class ArchiveGroupPayload(BaseModel):
    confirm_used: bool = False


def create_school_directory_router(local_database: Database | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/admin/school")
    from backend.services.consolidated_workspace import isolated_consolidation_enabled

    def production_consolidation_enabled() -> bool:
        settings = get_settings()
        return (os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") == "1"
                and settings.app_env == "production"
                and bool(settings.database_url)
                and not settings.local_test_mode
                and os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") != "1"
                and os.getenv("MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE") != "1"
                and local_database is not None
                and bool(local_database.database_url)
                and local_database.database_url == settings.database_url)

    def configured_source():
        if production_consolidation_enabled() or (isolated_consolidation_enabled() and local_database is not None):
            return local_database
        return school_source()

    def directory_source():
        return configured_source()

    def school_database() -> Database:
        settings = get_settings()
        if production_consolidation_enabled() or (isolated_consolidation_enabled() and local_database is not None):
            return local_database
        if os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") != "1" or os.getenv("MY_ISLAND_TEST_PROFILE") == "e2e":
            raise HTTPException(status_code=404, detail="School workspace is disabled")
        try:
            return Database(database_url=school_source().database_url)
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    def require_school_admin(token: str | None) -> tuple[Database, str]:
        if not token:
            raise HTTPException(status_code=401, detail="Real school administrator login is required")
        database = school_database()
        session = database.get_admin_browser_session(token)
        if not session or not database.user_has_role(session["user_id"], "admin"):
            raise HTTPException(status_code=401, detail="School administrator session is invalid or expired")
        return database, str(session["user_id"])

    def editor_schema_ready(database: Database) -> bool:
        if not database.database_url:
            return False  # This fixture exercises schedule APIs, not directory editing.
        with database.connection() as connection:
            columns = database.execute(connection, """SELECT table_name, column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND ((table_name = 'groups' AND column_name = 'admin_note')
                OR (table_name = 'identities' AND column_name = 'admin_note'))""").fetchall()
            tables = database.execute(connection, """SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'teacher_subjects'""").fetchall()
        return len(columns) == 2 and len(tables) == 1

    def grade_assignment_schema_ready(database: Database) -> bool:
        return database.teacher_grade_assignment_schema_ready()

    def archive_schema_ready(database: Database) -> bool:
        with database.connection() as connection:
            if database.database_url:
                row = database.execute(connection, "SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='groups' AND column_name='archived_at'").fetchone()
                return bool(row)
            return any(row[1] == "archived_at" for row in connection.execute("PRAGMA table_info(groups)").fetchall())

    def require_editor_schema(database: Database) -> None:
        if not editor_schema_ready(database):
            raise HTTPException(status_code=409, detail="School editor schema is pending migrations 034 and 035")

    @router.post("/auth/exchange")
    def exchange_code(payload: SchoolCodePayload):
        database = school_database()
        if not payload.code.strip():
            raise HTTPException(status_code=400, detail="Administrator code is required")
        consumed = database.consume_admin_login_challenge(payload.code.strip())
        if not consumed:
            raise HTTPException(status_code=401, detail="Administrator code is invalid or expired")
        token, user_id = consumed
        if not database.user_has_role(user_id, "admin"):
            database.revoke_admin_browser_session(token)
            raise HTTPException(status_code=403, detail="Administrator role required")
        database.record_audit_event("admin_auth.login", "admin_browser_session", {"transport": "local_school_editor"}, user_id)
        return {"browser_session_token": token}

    @router.get("/auth/session")
    def session(x_school_admin_session: str | None = Header(default=None)):
        _, user_id = require_school_admin(x_school_admin_session)
        return {"state": "approved", "user_id": user_id}

    @router.post("/auth/logout")
    def logout(x_school_admin_session: str | None = Header(default=None)):
        database, _ = require_school_admin(x_school_admin_session)
        database.revoke_admin_browser_session(x_school_admin_session)
        return {"state": "logged_out"}

    @router.get("/directory")
    def directory(x_school_admin_session: str | None = Header(default=None)):
        require_school_admin(x_school_admin_session)
        source = directory_source()
        if not source.database_url:
            from backend.services.school_experiment_workspace import school_directory
            data = school_directory(source)
            return {**data, "people": [], "read_only": True, "write_ready": False,
                    "grade_assignment_ready": False, "archive_ready": False}
        read_only = os.getenv("MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE") == "1"
        return {**school_editor_directory(source), "read_only": read_only,
                "write_ready": editor_schema_ready(source) and not read_only,
                "grade_assignment_ready": grade_assignment_schema_ready(source),
                "archive_ready": archive_schema_ready(source)}

    @router.get("/groups/{group_id}/archive-preview")
    def archive_preview(group_id: str, x_school_admin_session: str | None = Header(default=None)):
        require_school_admin(x_school_admin_session)
        source = configured_source()
        ready = archive_schema_ready(source)
        preview = source.group_archive_preview(group_id)
        if not preview:
            raise HTTPException(status_code=404, detail="Group was not found")
        return {**preview, "archive_ready": ready}

    @router.post("/groups/{group_id}/archive")
    def archive_group(group_id: str, payload: ArchiveGroupPayload,
                      x_school_admin_session: str | None = Header(default=None)):
        database, actor_id = require_school_admin(x_school_admin_session)
        if os.getenv("MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE") == "1":
            raise HTTPException(status_code=409, detail="School workspace is read-only")
        if not archive_schema_ready(database):
            raise HTTPException(status_code=409, detail="Group archive requires migration 037")
        try:
            group = database.archive_admin_group(group_id, actor_id, confirm_used=payload.confirm_used)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not group:
            raise HTTPException(status_code=404, detail="Group was not found")
        return {"id": str(group["id"]), "archived": True}

    @router.post("/groups/{group_id}/restore")
    def restore_group(group_id: str, x_school_admin_session: str | None = Header(default=None)):
        database, actor_id = require_school_admin(x_school_admin_session)
        if os.getenv("MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE") == "1":
            raise HTTPException(status_code=409, detail="School workspace is read-only")
        if not archive_schema_ready(database):
            raise HTTPException(status_code=409, detail="Group archive requires migration 037")
        group = database.restore_admin_group(group_id, actor_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group was not found")
        return {"id": str(group["id"]), "archived": False}

    @router.get("/teachers/{teacher_id}/schedule")
    def teacher_schedule(teacher_id: str, week_start: str | None = None,
                         x_school_admin_session: str | None = Header(default=None)):
        require_school_admin(x_school_admin_session)
        source = configured_source()
        return school_teacher_schedule(source, teacher_id, week_start)

    @router.post("/groups")
    def add_group(payload: GroupPayload, x_school_admin_session: str | None = Header(default=None)):
        database, actor_id = require_school_admin(x_school_admin_session)
        require_editor_schema(database)
        try:
            group = create_group(database, payload.name, payload.group_type,
                display_name=payload.display_name, subject=payload.subject,
                base_class_name=payload.base_class_name, subject_subgroup=payload.subject_subgroup,
                exam_track=payload.exam_track, default_teacher_identity_id=payload.default_teacher_identity_id,
                comment=payload.comment, actor_user_id=actor_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": str(group["id"])}

    @router.patch("/groups/{group_id}")
    def edit_group(group_id: str, payload: dict, x_school_admin_session: str | None = Header(default=None)):
        database, actor_id = require_school_admin(x_school_admin_session)
        require_editor_schema(database)
        allowed = {"name", "display_name", "group_type", "subject", "base_class_name", "subject_subgroup", "exam_track", "default_teacher_identity_id", "comment"}
        if set(payload) - allowed:
            raise HTTPException(status_code=400, detail="Unsupported group fields were supplied")
        try:
            group = database.update_admin_group(group_id, payload, actor_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not group:
            raise HTTPException(status_code=404, detail="Group was not found")
        return {"id": str(group["id"])}

    @router.post("/identities")
    def add_identity(payload: IdentityPayload, x_school_admin_session: str | None = Header(default=None)):
        database, actor_id = require_school_admin(x_school_admin_session)
        require_editor_schema(database)
        try:
            identity = create_identity(database, payload.display_name, payload.class_name,
                payload.kind, actor_id, payload.subjects, payload.comment, payload.status)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": str(identity["id"])}

    @router.patch("/people/{identity_id}")
    def edit_identity(identity_id: str, payload: dict, x_school_admin_session: str | None = Header(default=None)):
        database, actor_id = require_school_admin(x_school_admin_session)
        require_editor_schema(database)
        if set(payload) - {"display_name", "class_name", "status", "subjects", "comment"}:
            raise HTTPException(status_code=400, detail="Unsupported person fields were supplied")
        try:
            identity = database.update_admin_identity(identity_id, payload, actor_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not identity:
            raise HTTPException(status_code=404, detail="Person was not found")
        return {"id": str(identity["id"])}

    @router.post("/memberships/transfer")
    def transfer_membership(payload: MembershipTransferPayload, x_school_admin_session: str | None = Header(default=None)):
        database, actor_id = require_school_admin(x_school_admin_session)
        require_editor_schema(database)
        try:
            return database.update_student_memberships(payload.identity_id, payload.from_group_id,
                payload.to_group_id, actor_id, payload.reason)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/teacher-assignments")
    def edit_assignment(payload: TeacherAssignmentPayload, x_school_admin_session: str | None = Header(default=None)):
        database, actor_id = require_school_admin(x_school_admin_session)
        require_editor_schema(database)
        if payload.grade_level and not grade_assignment_schema_ready(database):
            raise HTTPException(status_code=409, detail="Grade-wide teacher assignments require migration 036")
        try:
            if payload.assignment_id:
                if payload.active and payload.group_id and not payload.grade_level:
                    dimensions = {name: getattr(payload, name) for name in ("base_class_name", "subject_subgroup", "classroom_course_id", "exam_track") if name in payload.model_fields_set}
                    item = database.replace_teacher_assignment(payload.assignment_id, payload.teacher_identity_id,
                        payload.group_id, payload.subject, actor_id, **dimensions)
                elif payload.active and payload.grade_level and not payload.group_id:
                    dimensions = {name: getattr(payload, name) for name in ("exam_track", "classroom_course_id") if name in payload.model_fields_set}
                    item = database.replace_teacher_grade_assignment(payload.assignment_id, payload.teacher_identity_id,
                        payload.grade_level, payload.subject, actor_id, **dimensions)
                elif not payload.active and not payload.group_id and not payload.grade_level:
                    item = database.deactivate_teacher_assignment(payload.assignment_id, payload.teacher_identity_id, actor_id)
                else:
                    raise ValueError("Assignment update requires assignment_id with either a canonical group or active=false")
                if not item:
                    raise HTTPException(status_code=404, detail="Teacher assignment was not found")
                return {"id": str(item["id"]), "active": bool(item["active"])}
            if bool(payload.group_id) == bool(payload.grade_level):
                raise ValueError("Provide exactly one of group_id or grade_level")
            if payload.grade_level:
                dimensions = {name: getattr(payload, name) for name in ("exam_track", "classroom_course_id") if name in payload.model_fields_set}
                items = database.set_teacher_grade_assignments(payload.teacher_identity_id, payload.grade_level,
                    payload.subject, payload.active, actor_id, **dimensions)
                return {"items": [{"id": str(item["id"]), "group_id": None, "grade_level": item["grade_level"]} for item in items]}
            item = assign_teacher(database, payload.teacher_identity_id, payload.group_id, payload.subject,
                payload.active, actor_id, base_class_name=payload.base_class_name,
                subject_subgroup=payload.subject_subgroup, classroom_course_id=payload.classroom_course_id,
                exam_track=payload.exam_track)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": str(item["id"]), "active": bool(item["active"])}

    return router
