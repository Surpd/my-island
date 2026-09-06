from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from datetime import date

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


def create_router(database: Database) -> APIRouter:
    router = APIRouter(prefix="/api")

    def authenticate(init_data: str | None, dev_auth: str | None):
        try:
            return resolve_auth(init_data, dev_auth)
        except AuthError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

    @router.post("/auth/session")
    def session(payload: SessionPayload, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(payload.init_data, x_dev_auth)
        selected_role = payload.role if payload.role in {"student", "teacher"} else "student"
        if telegram_user.get("dev") and telegram_user.get("role") in {"admin", "teacher"}:
            selected_role = telegram_user["role"]
        user = database.get_or_create_user(telegram_user["id"], selected_role)
        state = "approved" if user["identity_id"] else "needs_identity"
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": state, "user": {"id": user["id"], "role": user["role"], "identity_id": user["identity_id"]}}

    @router.post("/identity/claim")
    def create_identity_claim(payload: ClaimPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        user = database.get_or_create_user(telegram_user["id"], payload.role)
        claim = claim_identity(database, user["id"], payload.identity_id, payload.role)
        return {"id": claim["id"], "status": claim["status"]}

    @router.get("/student/today")
    def student_today(day: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        user = database.find_user(telegram_user["id"])
        if not user or user["role"] != "student" or not user["identity_id"]:
            raise HTTPException(status_code=403, detail="Identity approval required before personal data access")
        requested_day = day or date.today().isoformat()
        try:
            date.fromisoformat(requested_day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="day must be ISO date") from error
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "schedule": [dict(row) for row in database.list_schedule_entries_for_user(user["id"], requested_day)], "homework": [dict(row) for row in database.list_classroom_coursework_for_user(user["id"])], "announcements": [dict(row) for row in database.list_active_announcements()]}

    @router.get("/student/grades")
    def student_grades(init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        user = database.find_user(telegram_user["id"])
        if not user or user["role"] != "student" or not user["identity_id"]:
            raise HTTPException(status_code=403, detail="Identity approval required before personal data access")
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": [dict(row) for row in database.list_official_grades_for_user(user["id"])]}

    @router.get("/student/homework")
    def student_homework(init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        user = database.find_user(telegram_user["id"])
        if not user or user["role"] != "student" or not user["identity_id"]:
            raise HTTPException(status_code=403, detail="Identity approval required before personal data access")
        return {
            "mode": "dev" if telegram_user.get("dev") else "telegram",
            "state": "approved",
            "items": [dict(row) for row in database.list_classroom_coursework_for_user(user["id"])],
            "submissions": [dict(row) for row in database.list_classroom_submissions_for_user(user["id"])],
        }

    @router.get("/student/schedule")
    def student_schedule(start_day: str, end_day: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        user = database.find_user(telegram_user["id"])
        if not user or user["role"] != "student" or not user["identity_id"]:
            raise HTTPException(status_code=403, detail="Identity approval required before personal data access")
        try:
            date.fromisoformat(start_day)
            if end_day:
                date.fromisoformat(end_day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="start_day and end_day must be ISO dates") from error
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": [dict(row) for row in database.list_schedule_entries_for_user(user["id"], start_day, end_day)]}

    @router.get("/student/profile")
    def student_profile(init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        user = database.find_user(telegram_user["id"])
        if not user or user["role"] != "student" or not user["identity_id"]:
            raise HTTPException(status_code=403, detail="Identity approval required before personal data access")
        profile = database.get_student_profile(user["id"])
        if not profile:
            raise HTTPException(status_code=403, detail="Identity approval required before personal data access")
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "user": dict(profile["user"]), "groups": [dict(group) for group in profile["groups"]]}

    @router.get("/admin/claims")
    def pending_claims(init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        user = database.find_user(telegram_user["id"])
        if not user or user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        return {"items": [dict(item) for item in database.list_pending_claims()]}

    @router.get("/admin/users")
    def admin_users(init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        return {"items": database.list_users_with_groups()}

    @router.get("/admin/schedule/parses")
    def admin_schedule_parses(audience: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        return {"items": [dict(item) for item in database.list_schedule_parse_issues(audience)]}

    @router.get("/admin/schedule/syncs")
    def admin_schedule_syncs(limit: int = 20, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        return {"items": [dict(item) for item in database.list_schedule_syncs(max(1, min(limit, 100)))]}

    @router.post("/admin/schedule/refresh")
    def admin_schedule_refresh(week_start: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        try:
            return refresh_schedule(database, get_settings(), week_start)
        except (GoogleLiveError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @router.get("/admin/classroom/syncs")
    def admin_classroom_syncs(limit: int = 20, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        return {"items": [dict(item) for item in database.list_classroom_sync_audit(max(1, min(limit, 100)))]}

    @router.post("/admin/classroom/refresh")
    def admin_classroom_refresh(init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        try:
            return refresh_classroom(database, get_settings())
        except (GoogleLiveError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @router.post("/admin/identities")
    def create_student_identity(payload: IdentityPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        try:
            identity = create_identity(database, payload.display_name, payload.class_name, payload.kind)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": identity["id"], "display_name": identity["display_name"], "class_name": identity["class_name"]}

    @router.post("/admin/groups")
    def create_student_group(payload: GroupPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        try:
            group = create_group(database, payload.name, payload.group_type)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": group["id"], "name": group["name"], "group_type": group["group_type"]}

    @router.post("/admin/memberships")
    def create_student_membership(payload: MembershipPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        try:
            membership = add_membership(database, payload.group_id, payload.identity_id, payload.source, payload.member_role)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": membership["id"], "group_id": membership["group_id"], "identity_id": membership["identity_id"], "member_role": membership["member_role"], "active": membership["active"]}

    @router.post("/admin/schedule-scopes")
    def create_schedule_scope(payload: ScheduleScopePayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        scope = map_schedule_scope(database, payload.group_id, payload.audience, subject=payload.subject, subject_subgroup=payload.subject_subgroup, exam_track=payload.exam_track)
        return {"id": scope["id"], "group_id": scope["group_id"], "audience": scope["audience"], "subject": scope["subject"], "subject_subgroup": scope["subject_subgroup"], "exam_track": scope["exam_track"]}

    @router.post("/admin/claims/{claim_id}/review")
    def review_claim(claim_id: str, payload: ReviewPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        reviewer = database.find_user(telegram_user["id"])
        if not reviewer or reviewer["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        try:
            claim = review_identity_claim(database, claim_id, reviewer["id"], payload.status)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")
        return {"id": claim["id"], "status": claim["status"]}

    @router.get("/teacher/courses")
    def teacher_courses(init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        user = database.find_user(telegram_user["id"])
        if not user or user["role"] != "teacher" or not user["identity_id"]:
            raise HTTPException(status_code=403, detail="Teacher approval required")
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": [dict(item) for item in database.list_teacher_courses(user["id"])]}

    @router.get("/teacher/schedule")
    def teacher_schedule(start_day: str, end_day: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None)):
        telegram_user = authenticate(init_data, x_dev_auth)
        user = database.find_user(telegram_user["id"])
        if not user or user["role"] != "teacher" or not user["identity_id"]:
            raise HTTPException(status_code=403, detail="Teacher approval required")
        try:
            date.fromisoformat(start_day)
            if end_day:
                date.fromisoformat(end_day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="start_day and end_day must be ISO dates") from error
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": [dict(row) for row in database.list_schedule_entries_for_user(user["id"], start_day, end_day)]}

    return router
