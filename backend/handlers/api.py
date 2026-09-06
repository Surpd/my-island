from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from datetime import date, datetime, timedelta, timezone

from backend.database import Database
from backend.services.auth import AuthError, resolve_auth
from backend.services.identity import claim_identity, review_identity_claim
from backend.services.admin import add_membership, create_group, create_identity, map_schedule_scope
from backend.services.google_live import GoogleLiveError
from backend.services.google_sync_service import refresh_classroom, refresh_schedule
from backend.config import get_settings


class SessionPayload(BaseModel):
    init_data: str | None = None
    role: str = "student"


class ClaimPayload(BaseModel):
    identity_id: str
    role: str = "student"


class ReviewPayload(BaseModel):
    status: str


class IdentityPayload(BaseModel):
    display_name: str
    class_name: str | None = None
    kind: str = "student"


class GroupPayload(BaseModel):
    name: str
    group_type: str


class MembershipPayload(BaseModel):
    group_id: str
    identity_id: str
    source: str = "admin_override"
    member_role: str = "student"


class ScheduleScopePayload(BaseModel):
    group_id: str
    audience: str
    subject: str = ""
    subject_subgroup: str = ""
    exam_track: str = ""


class RolesPayload(BaseModel):
    roles: list[str]
    primary_role: str | None = None


class StudentPreviewPayload(BaseModel):
    target_user_id: str


def create_router(database: Database) -> APIRouter:
    router = APIRouter(prefix="/api")

    def authenticate(init_data: str | None, dev_auth: str | None, telegram_header: str | None = None):
        try:
            return resolve_auth(telegram_header or init_data, dev_auth)
        except AuthError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

    def authenticated_user(telegram_user: dict):
        return database.find_user(telegram_user["id"])

    def require_role(telegram_user: dict, role: str):
        user = authenticated_user(telegram_user)
        if not user or not database.user_has_role(user["id"], role):
            raise HTTPException(status_code=403, detail=f"{role.capitalize()} role required")
        return user

    def student_subject(telegram_user: dict, preview_id: str | None):
        actor = authenticated_user(telegram_user)
        if preview_id:
            if not actor or not database.user_has_role(actor["id"], "admin"):
                raise HTTPException(status_code=403, detail="Admin role required for student preview")
            preview = database.get_active_student_preview(preview_id, actor["id"])
            target = database.get_user_by_id(preview["target_user_id"]) if preview else None
            if not target or not target["identity_id"] or not database.user_has_role(target["id"], "student"):
                raise HTTPException(status_code=403, detail="Student preview is invalid or expired")
            return target
        if not actor or not actor["identity_id"] or not database.user_has_role(actor["id"], "student"):
            raise HTTPException(status_code=403, detail="Identity approval required before personal data access")
        return actor

    @router.post("/auth/session")
    def session(payload: SessionPayload, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(payload.init_data, x_dev_auth, x_telegram_init_data)
        selected_role = payload.role if payload.role in {"student", "teacher", "admin"} else "student"
        if telegram_user.get("dev") and telegram_user.get("role") in {"admin", "teacher"}:
            selected_role = telegram_user["role"]
        user = database.get_or_create_user(telegram_user["id"], selected_role)
        settings = get_settings()
        bootstrap_roles = ("teacher", "admin") if telegram_user["id"] in settings.telegram_bootstrap_user_ids else ()
        user = database.ensure_bootstrap_roles(telegram_user["id"], bootstrap_roles) or user
        roles = database.list_user_roles(user["id"])
        if selected_role not in roles:
            selected_role = "teacher" if "teacher" in roles else ("admin" if "admin" in roles else roles[0] if roles else user["role"])
        state = "approved" if "admin" in roles or user["identity_id"] else "needs_identity"
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": state, "user": {"id": user["id"], "role": selected_role, "primary_role": selected_role, "roles": roles, "identity_id": user["identity_id"]}}

    @router.post("/identity/claim")
    def create_identity_claim(payload: ClaimPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = database.get_or_create_user(telegram_user["id"], payload.role)
        claim = claim_identity(database, user["id"], payload.identity_id, payload.role)
        return {"id": claim["id"], "status": claim["status"]}

    @router.get("/student/today")
    def student_today(day: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_student_preview_id: str | None = Header(default=None, alias="X-Student-Preview-Id")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = student_subject(telegram_user, x_student_preview_id)
        requested_day = day or date.today().isoformat()
        try:
            date.fromisoformat(requested_day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="day must be ISO date") from error
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "schedule": [dict(row) for row in database.list_schedule_entries_for_user(user["id"], requested_day)], "homework": [dict(row) for row in database.list_classroom_coursework_for_user(user["id"])], "announcements": [dict(row) for row in database.list_active_announcements()]}

    @router.get("/student/grades")
    def student_grades(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_student_preview_id: str | None = Header(default=None, alias="X-Student-Preview-Id")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = student_subject(telegram_user, x_student_preview_id)
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": [dict(row) for row in database.list_official_grades_for_user(user["id"])]}

    @router.get("/student/homework")
    def student_homework(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_student_preview_id: str | None = Header(default=None, alias="X-Student-Preview-Id")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = student_subject(telegram_user, x_student_preview_id)
        return {
            "mode": "dev" if telegram_user.get("dev") else "telegram",
            "state": "approved",
            "items": [dict(row) for row in database.list_classroom_coursework_for_user(user["id"])],
            "submissions": [dict(row) for row in database.list_classroom_submissions_for_user(user["id"])],
        }

    @router.get("/student/schedule")
    def student_schedule(start_day: str, end_day: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_student_preview_id: str | None = Header(default=None, alias="X-Student-Preview-Id")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = student_subject(telegram_user, x_student_preview_id)
        try:
            date.fromisoformat(start_day)
            if end_day:
                date.fromisoformat(end_day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="start_day and end_day must be ISO dates") from error
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": [dict(row) for row in database.list_schedule_entries_for_user(user["id"], start_day, end_day)]}

    @router.get("/student/profile")
    def student_profile(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_student_preview_id: str | None = Header(default=None, alias="X-Student-Preview-Id")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = student_subject(telegram_user, x_student_preview_id)
        profile = database.get_student_profile(user["id"])
        if not profile:
            raise HTTPException(status_code=403, detail="Identity approval required before personal data access")
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "user": dict(profile["user"]), "groups": [dict(group) for group in profile["groups"]]}

    @router.get("/admin/claims")
    def pending_claims(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = database.find_user(telegram_user["id"])
        require_role(telegram_user, "admin")
        return {"items": [dict(item) for item in database.list_pending_claims()]}

    @router.get("/admin/users")
    def admin_users(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"items": database.list_users_with_groups()}

    @router.get("/admin/schedule/parses")
    def admin_schedule_parses(audience: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"items": [dict(item) for item in database.list_schedule_parse_issues(audience)]}

    @router.get("/admin/schedule/syncs")
    def admin_schedule_syncs(limit: int = 20, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"items": [dict(item) for item in database.list_schedule_syncs(max(1, min(limit, 100)))]}

    @router.post("/admin/schedule/refresh")
    def admin_schedule_refresh(week_start: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        try:
            return refresh_schedule(database, get_settings(), week_start)
        except (GoogleLiveError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @router.get("/admin/classroom/syncs")
    def admin_classroom_syncs(limit: int = 20, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"items": [dict(item) for item in database.list_classroom_sync_audit(max(1, min(limit, 100)))]}

    @router.post("/admin/classroom/refresh")
    def admin_classroom_refresh(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        try:
            return refresh_classroom(database, get_settings())
        except (GoogleLiveError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @router.post("/admin/identities")
    def create_student_identity(payload: IdentityPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        try:
            identity = create_identity(database, payload.display_name, payload.class_name, payload.kind)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": identity["id"], "display_name": identity["display_name"], "class_name": identity["class_name"]}

    @router.post("/admin/groups")
    def create_student_group(payload: GroupPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        try:
            group = create_group(database, payload.name, payload.group_type)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": group["id"], "name": group["name"], "group_type": group["group_type"]}

    @router.post("/admin/memberships")
    def create_student_membership(payload: MembershipPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        try:
            membership = add_membership(database, payload.group_id, payload.identity_id, payload.source, payload.member_role)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": membership["id"], "group_id": membership["group_id"], "identity_id": membership["identity_id"], "member_role": membership["member_role"], "active": membership["active"]}

    @router.post("/admin/schedule-scopes")
    def create_schedule_scope(payload: ScheduleScopePayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        scope = map_schedule_scope(database, payload.group_id, payload.audience, subject=payload.subject, subject_subgroup=payload.subject_subgroup, exam_track=payload.exam_track)
        return {"id": scope["id"], "group_id": scope["group_id"], "audience": scope["audience"], "subject": scope["subject"], "subject_subgroup": scope["subject_subgroup"], "exam_track": scope["exam_track"]}

    @router.post("/admin/claims/{claim_id}/review")
    def review_claim(claim_id: str, payload: ReviewPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        reviewer = require_role(telegram_user, "admin")
        try:
            claim = review_identity_claim(database, claim_id, reviewer["id"], payload.status)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")
        return {"id": claim["id"], "status": claim["status"]}

    @router.post("/admin/users/{user_id}/roles")
    def update_user_roles(user_id: str, payload: RolesPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        reviewer = require_role(actor, "admin")
        allowed = {"student", "teacher", "admin"}
        roles = sorted(set(payload.roles), key=lambda role: (role != "teacher", role != "admin", role))
        if not roles or any(role not in allowed for role in roles):
            raise HTTPException(status_code=400, detail="At least one valid role is required")
        target = database.get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if database.user_has_role(target["id"], "admin") and "admin" not in roles and database.count_users_with_role("admin") <= 1:
            raise HTTPException(status_code=400, detail="The last admin role cannot be removed")
        primary_role = payload.primary_role if payload.primary_role in roles else ("teacher" if "teacher" in roles else roles[0])
        updated = database.set_user_roles(target["id"], roles, primary_role)
        database.record_audit_event("user_roles.updated", "user", {"target_user_id": str(target["id"]), "roles": updated}, reviewer["id"])
        return {"user_id": target["id"], "roles": updated, "primary_role": primary_role}

    @router.post("/admin/student-previews")
    def start_student_preview(payload: StudentPreviewPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        reviewer = require_role(actor, "admin")
        target = database.get_user_by_id(payload.target_user_id)
        if not target or not target["identity_id"] or not database.user_has_role(target["id"], "student"):
            raise HTTPException(status_code=400, detail="Only an approved student can be previewed")
        created_at = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        preview = database.create_student_preview(reviewer["id"], target["id"], created_at, expires_at)
        profile = database.get_student_profile(target["id"])
        return {"id": preview["id"], "expires_at": preview["expires_at"], "target": {"id": target["id"], "display_name": profile["user"]["display_name"] if profile else "Ученик", "class_name": profile["user"]["class_name"] if profile else None}}

    @router.post("/admin/student-previews/{preview_id}/end")
    def end_student_preview(preview_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        reviewer = require_role(actor, "admin")
        return {"ended": database.end_student_preview(preview_id, reviewer["id"])}

    @router.get("/admin/students/{user_id}/schedule-explanations")
    def student_schedule_explanations(user_id: str, start_day: str, end_day: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(actor, "admin")
        target = database.get_user_by_id(user_id)
        if not target or not target["identity_id"] or not database.user_has_role(target["id"], "student"):
            raise HTTPException(status_code=404, detail="Approved student not found")
        try:
            date.fromisoformat(start_day)
            if end_day:
                date.fromisoformat(end_day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="start_day and end_day must be ISO dates") from error
        return {"items": database.list_schedule_explanations_for_user(target["id"], start_day, end_day)}

    @router.get("/teacher/courses")
    def teacher_courses(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = require_role(telegram_user, "teacher")
        if not user["identity_id"]:
            return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "not_configured", "items": []}
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": [dict(item) for item in database.list_teacher_courses(user["id"])]}

    @router.get("/teacher/schedule")
    def teacher_schedule(start_day: str, end_day: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = require_role(telegram_user, "teacher")
        try:
            date.fromisoformat(start_day)
            if end_day:
                date.fromisoformat(end_day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="start_day and end_day must be ISO dates") from error
        if not user["identity_id"]:
            return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "not_configured", "items": []}
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": [dict(row) for row in database.list_schedule_entries_for_user(user["id"], start_day, end_day)]}

    return router
