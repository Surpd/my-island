from __future__ import annotations

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from pydantic import BaseModel
from datetime import date, datetime, timedelta, timezone
import json
import hmac

from backend.database import Database
from backend.services.auth import AuthError, resolve_auth
from backend.services.identity import claim_identity, review_identity_claim
from backend.services.admin import (
    add_membership, assign_homeroom, assign_teacher, create_group, create_identity,
    map_schedule_scope, override_membership, publish_information,
)
from backend.services.google_live import GoogleLiveError, GoogleTokenStore
from backend.services.google_sync_service import refresh_classroom, refresh_journal, refresh_schedule_v1
from backend.services.schedule_pipeline import reconcile_current_schedule, schedule_reconciliation_needs_refresh, schedule_reconciliation_view
from backend.services.teacher import journal_view
from backend.config import get_settings
from backend.services.student_membership_reconciliation import run_live_dry_run, GoogleLiveError as ReconciliationGoogleLiveError
from backend.services.student_membership_apply import StudentMembershipApplyBlocked, apply_reviewed_plan, _readback
from backend.services.student_membership_manual_resolutions import load_manual_resolutions
from backend.services.student_membership_reconciliation import fetch_authoritative_sources, load_production_snapshot
from backend.services.telegram_admin_bot import handle_admin_code_update


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
    source_ref: str = ""
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


class MembershipOverridePayload(BaseModel):
    identity_id: str
    group_id: str
    member_role: str = "student"
    action: str
    reason: str = ""


class TeacherAssignmentPayload(BaseModel):
    teacher_identity_id: str
    group_id: str
    subject: str = ""
    active: bool = True
    base_class_name: str | None = None
    subject_subgroup: str | None = None
    classroom_course_id: str | None = None
    exam_track: str | None = None


class HomeroomAssignmentPayload(BaseModel):
    teacher_identity_id: str
    group_id: str
    active: bool = True


class InformationPayload(BaseModel):
    title: str
    content: str
    audience_kind: str = "all"
    audience_ref: str | None = None
    publish_at: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    pinned: bool = False
    status: str = "draft"


class JournalMappingPayload(BaseModel):
    source_id: str
    group_marker: str
    group_id: str | None = None
    base_class_name: str | None = None
    subject_subgroup: str | None = None
    classroom_course_id: str | None = None
    exam_track: str | None = None


class BrowserCodePayload(BaseModel):
    code: str


class ReconciliationReviewPayload(BaseModel):
    approved: bool = True


class ScheduleMappingPayload(BaseModel):
    mapping_type: str
    external_key: str
    target_id: str | None = None
    canonical_value: str | None = None


def create_router(database: Database) -> APIRouter:
    router = APIRouter(prefix="/api")

    def authenticate(init_data: str | None, dev_auth: str | None, telegram_header: str | None = None, browser_session: str | None = None):
        browser_token = browser_session or (dev_auth.removeprefix("browser:") if dev_auth and dev_auth.startswith("browser:") else None)
        if browser_token:
            session = database.get_admin_browser_session(browser_token)
            if not session:
                raise HTTPException(status_code=401, detail="Admin browser session is invalid or expired")
            return {"id": session["telegram_user_id"], "user_id": session["user_id"], "role": "admin", "browser_session": True}
        try:
            return resolve_auth(telegram_header or init_data, dev_auth)
        except AuthError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

    def authenticated_user(telegram_user: dict):
        if telegram_user.get("browser_session"):
            return database.get_user_by_id(telegram_user["user_id"])
        return database.find_user(telegram_user["id"])

    def require_role(telegram_user: dict, role: str):
        user = authenticated_user(telegram_user)
        if not user or not database.user_has_role(user["id"], role):
            raise HTTPException(status_code=403, detail=f"{role.capitalize()} role required")
        return user

    @router.post("/admin/auth/challenge")
    def create_admin_browser_challenge(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        code, expires_at = database.create_admin_login_challenge(actor["id"])
        database.record_audit_event("admin_auth.challenge_created", "admin_browser_session", {"expires_at": expires_at}, actor["id"])
        return {"code": code, "expires_at": expires_at, "single_use": True}

    @router.post("/telegram/webhook")
    async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token")):
        settings = get_settings()
        if not settings.telegram_webhook_secret or not x_telegram_bot_api_secret_token or not hmac.compare_digest(
            x_telegram_bot_api_secret_token, settings.telegram_webhook_secret
        ):
            raise HTTPException(status_code=403, detail="Telegram webhook is not authorized")
        try:
            update = await request.json()
        except Exception as error:
            raise HTTPException(status_code=400, detail="Telegram update must be valid JSON") from error
        handle_admin_code_update(database, update, settings.telegram_bot_token)
        return {"ok": True}

    @router.post("/admin/auth/exchange")
    def exchange_admin_browser_code(payload: BrowserCodePayload, response: Response):
        consumed = database.consume_admin_login_challenge(payload.code.strip())
        if not consumed:
            raise HTTPException(status_code=401, detail="Admin login code is invalid, expired, or already used")
        session_token, user_id = consumed
        user = database.get_user_by_id(user_id)
        if not user or not database.user_has_role(user_id, "admin"):
            database.revoke_admin_browser_session(session_token)
            raise HTTPException(status_code=403, detail="Admin role required")
        settings = get_settings()
        response.set_cookie(
            "my_island_admin_session",
            session_token,
            max_age=86400 * 7,
            httponly=True,
            secure=settings.app_env == "production",
            samesite="none" if settings.app_env == "production" else "lax",
            path="/",
        )
        # ChatGPT-hosted Sites call the Render API cross-site. Partitioned cookies
        # keep the HttpOnly session available in browsers that block unpartitioned
        # third-party cookies. Starlette only exposes this flag on Python 3.14+;
        # append the attribute explicitly so the current Render runtime remains
        # supported.
        if settings.app_env == "production" and response.raw_headers and response.raw_headers[-1][0] == b"set-cookie":
            name, value = response.raw_headers[-1]
            response.raw_headers[-1] = (name, value + b"; Partitioned")
        database.record_audit_event("admin_auth.login", "admin_browser_session", {"transport": "browser"}, user_id)
        return {
            "state": "approved",
            "user": {"id": user["id"], "role": "admin", "identity_id": user["identity_id"]},
            # The HttpOnly cookie remains the primary transport. This browser-origin
            # fallback is needed for hosted sites that block
            # cross-site cookies; the frontend keeps it in sessionStorage only.
            "browser_session_token": session_token,
        }

    @router.get("/admin/auth/session")
    def admin_browser_session(x_dev_auth: str | None = Header(default=None), my_island_admin_session: str | None = Cookie(default=None)):
        telegram_user = authenticate(None, x_dev_auth, browser_session=my_island_admin_session)
        user = require_role(telegram_user, "admin")
        return {"state": "approved", "mode": "browser" if my_island_admin_session else "dev", "user": {"id": user["id"], "role": "admin", "identity_id": user["identity_id"]}}

    @router.post("/admin/auth/logout")
    def admin_browser_logout(response: Response, my_island_admin_session: str | None = Cookie(default=None)):
        if my_island_admin_session:
            session = database.get_admin_browser_session(my_island_admin_session)
            if session:
                database.revoke_admin_browser_session(my_island_admin_session)
                database.record_audit_event("admin_auth.logout", "admin_browser_session", {"transport": "browser"}, session["user_id"])
        response.delete_cookie("my_island_admin_session", path="/")
        return {"state": "logged_out"}

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
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "schedule": [dict(row) for row in database.list_schedule_entries_for_user(user["id"], requested_day)], "homework": [dict(row) for row in database.list_classroom_coursework_for_user(user["id"])], "announcements": [dict(row) for row in database.list_active_announcements(user["id"], "student")]}

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

    @router.get("/admin/people")
    def admin_people(kind: str | None = None, q: str | None = None, class_name: str | None = None, group_id: str | None = None, has_issues: bool | None = None, protected: bool | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        if kind not in {None, "student", "teacher"}:
            raise HTTPException(status_code=400, detail="kind must be student or teacher")
        return {"items": database.list_people_library_summary(kind=kind, query=q, class_name=class_name, group_id=group_id, has_issues=has_issues, protected=protected)}

    @router.get("/admin/people/filters")
    def admin_people_filters(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return database.list_people_filter_options()

    @router.get("/admin/people/{identity_id}")
    def admin_person(identity_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        person = database.get_people_library_person(identity_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person was not found")
        return person

    @router.get("/admin/overview")
    def admin_overview(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return database.admin_overview()

    @router.get("/admin/system")
    def admin_system(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        settings = get_settings()
        google_configured = all((settings.google_oauth_client_id, settings.google_oauth_client_secret, settings.google_oauth_redirect_uri))
        google_token_present = bool(settings.google_oauth_refresh_token)
        google_status = "connected" if google_configured and google_token_present else "reauthorization_required" if google_configured else "configuration_missing"
        return {"health": database.system_health(), "settings": {"environment": settings.app_env, "database_configured": bool(settings.database_url), "dev_auth_enabled": settings.dev_auth_enabled, "google": {"status": google_status, "configuration_present": google_configured, "refresh_token_present": google_token_present, "message": "Google-соединение готово для синхронизации" if google_status == "connected" else "Необходимо повторно авторизовать Google-соединение" if google_status == "reauthorization_required" else "Google OAuth ещё не настроен"}}}

    @router.get("/admin/sources")
    def admin_sources(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"items": database.list_school_sources_health()}

    @router.get("/admin/issues")
    def admin_issues(status: str | None = None, issue_type: str | None = None, source_id: str | None = None, limit: int = 200, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        if status not in {None, "open", "resolved", "ignored"}:
            raise HTTPException(status_code=400, detail="status must be open, resolved or ignored")
        return {"items": database.list_resolution_issues(status=status, issue_type=issue_type, source_id=source_id, limit=limit)}

    @router.get("/admin/candidates")
    def admin_candidates(status: str | None = None, entity_type: str | None = None, source_id: str | None = None, sync_run_id: str | None = None, limit: int = 200, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        if status not in {None, "pending", "approved", "rejected", "applied", "unresolved", "conflict"}:
            raise HTTPException(status_code=400, detail="Unsupported candidate change status")
        return {"items": database.list_candidate_changes(status=status, entity_type=entity_type, source_id=source_id, sync_run_id=sync_run_id, limit=limit)}

    @router.get("/admin/sources/{source_id}")
    def admin_source_detail(source_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        detail = database.get_school_source_detail(source_id)
        if not detail:
            raise HTTPException(status_code=404, detail="Source was not found")
        return detail

    @router.get("/admin/reconciliation/student-memberships/latest")
    def latest_student_membership_reconciliation(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return database.get_latest_reconciliation_run() or {"status": "not_run", "payload": None}

    @router.get("/admin/reconciliation/student-memberships/runs/{run_id}")
    def student_membership_reconciliation_run(run_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        run = database.get_reconciliation_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Reconciliation run was not found")
        return run

    @router.post("/admin/reconciliation/student-memberships/dry-run")
    def student_membership_reconciliation_dry_run(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            payload = run_live_dry_run(database, get_settings())
        except (ReconciliationGoogleLiveError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        run_id = database.create_reconciliation_run(actor["id"], payload)
        database.record_audit_event("student_membership_reconciliation.dry_run", "reconciliation_run", {"run_id": run_id, "mode": "read_only", "summary": payload.get("summary", {})}, actor["id"])
        return {"id": run_id, "status": "ready_for_review", "payload": payload}

    @router.post("/admin/reconciliation/student-memberships/runs/{run_id}/review")
    def review_student_membership_reconciliation(run_id: str, payload: ReconciliationReviewPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        if not payload.approved:
            raise HTTPException(status_code=400, detail="Use a new dry-run after rejecting a plan")
        run = database.mark_reconciliation_reviewed(run_id, actor["id"])
        if not run:
            raise HTTPException(status_code=409, detail="Run is missing or is no longer reviewable")
        database.record_audit_event("student_membership_reconciliation.reviewed", "reconciliation_run", {"run_id": run_id}, actor["id"])
        return run

    @router.post("/admin/reconciliation/student-memberships/apply")
    def apply_student_membership_reconciliation(run_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        run = database.get_reconciliation_run(run_id)
        if not run or run.get("status") != "approved":
            raise HTTPException(status_code=409, detail="A reviewed reconciliation run is required")
        try:
            settings = get_settings()
            source_rows, source_meta = fetch_authoritative_sources(settings)
            snapshot = load_production_snapshot(database)
            manual = load_manual_resolutions()
            current_payload = run_live_dry_run(database, settings)
            if json.dumps(current_payload.get("summary", {}), sort_keys=True) != json.dumps(run["payload"].get("summary", {}), sort_keys=True):
                raise StudentMembershipApplyBlocked("Reviewed run is stale; run a new dry-run")
            result = apply_reviewed_plan(database, settings, run["payload"], source_rows, source_meta, snapshot, current_payload, manual)
            result["readback"] = _readback(database, result["teacher_before"])
        except (ReconciliationGoogleLiveError, StudentMembershipApplyBlocked, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        stored = database.finish_reconciliation_run(run_id, result)
        database.record_audit_event("student_membership_reconciliation.applied", "reconciliation_run", {"run_id": run_id, "readback": result.get("readback")}, actor["id"])
        return stored

    @router.get("/admin/groups")
    def admin_groups(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"items": database.list_groups_admin_summary()}

    @router.get("/admin/groups/{group_id}")
    def admin_group_detail(group_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        group = database.get_group_admin(group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group was not found")
        return group

    @router.get("/admin/audit")
    def admin_audit(limit: int = 50, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"items": database.list_audit_events(max(1, min(limit, 100)))}

    @router.get("/admin/information")
    def admin_information(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"items": database.list_announcements_admin()}

    @router.post("/admin/information")
    def create_information(payload: InformationPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            item = publish_information(database, payload.model_dump(), actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        database.record_audit_event("information.created", "announcement", {"announcement_id": str(item["id"]), "status": item["status"]}, actor["id"])
        return {"id": item["id"], "status": item["status"]}

    @router.get("/admin/journals")
    def admin_journals(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"items": database.journal_status()}

    @router.post("/admin/journals/refresh")
    def admin_journal_refresh(grade: int = 9, subject: str = "Математика", init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        try:
            return refresh_journal(database, get_settings(), grade, subject)
        except (GoogleLiveError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @router.post("/admin/journals/group-mappings")
    def admin_journal_mapping(payload: JournalMappingPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        marker = payload.group_marker.strip()
        if not marker or not database.journal_group_marker_exists(payload.source_id, marker):
            raise HTTPException(status_code=400, detail="Journal group marker was not found in this source")
        dimensions = {
            "base_class_name": (payload.base_class_name or "").strip() or None,
            "subject_subgroup": (payload.subject_subgroup or "").strip() or None,
            "classroom_course_id": (payload.classroom_course_id or "").strip() or None,
            "exam_track": (payload.exam_track or "").strip() or None,
        }
        if not payload.group_id and not any(dimensions.values()):
            raise HTTPException(status_code=400, detail="Journal mapping needs an internal group or at least one explicit dimension")
        if payload.group_id and not database.get_group(payload.group_id):
            raise HTTPException(status_code=400, detail="Journal target group was not found")
        if dimensions["classroom_course_id"] and not database.get_classroom_course(dimensions["classroom_course_id"]):
            raise HTTPException(status_code=400, detail="Classroom course was not found")
        item = database.map_journal_group(payload.source_id, marker, payload.group_id, actor["id"], **dimensions)
        database.record_audit_event("journal_group_mapping.updated", "journal_group_mapping", {"source_id": payload.source_id, "group_marker": payload.group_marker, "group_id": payload.group_id, **dimensions}, actor["id"])
        return {"id": item["id"]}

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
            return refresh_schedule_v1(database, get_settings())
        except (GoogleLiveError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @router.get("/admin/schedule/overview")
    def admin_schedule_overview(week_start: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        if schedule_reconciliation_needs_refresh(database):
            reconcile_current_schedule(database)
        result = database.schedule_v1_overview(week_start=week_start)
        settings = get_settings()
        if not settings.google_sheets_spreadsheet_id:
            result["auth_state"] = "missing_spreadsheet_id"
        elif not GoogleTokenStore().has_refresh_token():
            result["auth_state"] = "missing_google_token"
        return result

    @router.get("/admin/schedule/lessons")
    def admin_schedule_lessons(week_start: str | None = None, status: str | None = None, teacher_id: str | None = None, group_id: str | None = None, lesson_type: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return {"items": database.schedule_v1_lessons(status=status, week_start=week_start, teacher_id=teacher_id, group_id=group_id, lesson_type=lesson_type)}

    @router.get("/admin/schedule/issues")
    def admin_schedule_issues(week_start: str | None = None, status: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return {"items": database.schedule_v1_issues(week_start=week_start, status=status)}

    @router.get("/admin/schedule/reconciliation")
    def admin_schedule_reconciliation(week_start: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return schedule_reconciliation_view(database, week_start=week_start)

    @router.post("/admin/schedule/mappings")
    def admin_schedule_save_mapping(payload: ScheduleMappingPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        actor = authenticated_user(telegram_user)
        try:
            mapping = database.save_schedule_v1_mapping(payload.mapping_type, payload.external_key, target_id=payload.target_id,
                                                        canonical_value=payload.canonical_value, actor_user_id=actor["id"] if actor else None)
            reconcile_current_schedule(database)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        database.record_audit_event("schedule_mapping.confirmed", "school_source_mapping", {"mapping_id": mapping["id"], "mapping_type": payload.mapping_type, "external_key": payload.external_key}, actor["id"] if actor else None)
        return {"mapping": mapping, "reconciliation": schedule_reconciliation_view(database)}

    @router.delete("/admin/schedule/mappings/{mapping_id}")
    def admin_schedule_retire_mapping(mapping_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        actor = authenticated_user(telegram_user)
        if not database.retire_schedule_v1_mapping(mapping_id):
            raise HTTPException(status_code=404, detail="Schedule mapping was not found")
        reconcile_current_schedule(database)
        database.record_audit_event("schedule_mapping.retired", "school_source_mapping", {"mapping_id": mapping_id}, actor["id"] if actor else None)
        return {"ok": True, "reconciliation": schedule_reconciliation_view(database)}

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
        actor = require_role(telegram_user, "admin")
        try:
            membership = add_membership(database, payload.group_id, payload.identity_id, payload.source, payload.member_role, payload.source_ref, actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": membership["id"], "group_id": membership["group_id"], "identity_id": membership["identity_id"], "member_role": membership["member_role"], "active": membership["active"]}

    @router.post("/admin/membership-overrides")
    def create_membership_override(payload: MembershipOverridePayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            item = override_membership(database, payload.identity_id, payload.group_id, payload.member_role, payload.action, actor["id"], payload.reason)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        database.record_audit_event("membership_override.created", "membership_override", {"identity_id": payload.identity_id, "group_id": payload.group_id, "action": payload.action, "reason": payload.reason}, actor["id"])
        return {"id": item["id"], "action": item["action"], "active": item["active"]}

    @router.post("/admin/teacher-assignments")
    def create_teacher_assignment(payload: TeacherAssignmentPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            item = assign_teacher(
                database,
                payload.teacher_identity_id,
                payload.group_id,
                payload.subject,
                payload.active,
                actor["id"],
                base_class_name=payload.base_class_name,
                subject_subgroup=payload.subject_subgroup,
                classroom_course_id=payload.classroom_course_id,
                exam_track=payload.exam_track,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        database.record_audit_event("teacher_assignment.updated", "teacher_assignment", {"teacher_identity_id": payload.teacher_identity_id, "group_id": payload.group_id, "subject": payload.subject, "active": payload.active, "base_class_name": payload.base_class_name, "subject_subgroup": payload.subject_subgroup, "classroom_course_id": payload.classroom_course_id, "exam_track": payload.exam_track}, actor["id"])
        return {"id": item["id"], "active": item["active"]}

    @router.post("/admin/homeroom-assignments")
    def create_homeroom_assignment(payload: HomeroomAssignmentPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            item = assign_homeroom(database, payload.teacher_identity_id, payload.group_id, payload.active, actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        database.record_audit_event("homeroom_assignment.updated", "homeroom_assignment", {"teacher_identity_id": payload.teacher_identity_id, "group_id": payload.group_id, "active": payload.active}, actor["id"])
        return {"id": item["id"], "active": item["active"]}

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

    @router.get("/teacher/home")
    def teacher_home(day: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = require_role(telegram_user, "teacher")
        requested_day = day or date.today().isoformat()
        try:
            date.fromisoformat(requested_day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="day must be ISO date") from error
        if not user["identity_id"]:
            return {"state": "not_configured", "schedule": [], "groups": [], "information": []}
        return {
            "state": "approved",
            "schedule": [dict(row) for row in database.list_teacher_schedule(user["id"], requested_day)],
            "groups": database.list_teacher_groups(user["id"]),
            "information": [dict(row) for row in database.list_active_announcements(user["id"], "teacher")],
        }

    @router.get("/teacher/groups")
    def teacher_groups(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = require_role(telegram_user, "teacher")
        return {"items": database.list_teacher_groups(user["id"]) if user["identity_id"] else []}

    @router.get("/teacher/profile")
    def teacher_profile(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = require_role(telegram_user, "teacher")
        profile = database.get_teacher_profile(user["id"])
        if not profile:
            return {"state": "not_configured", "user": None, "groups": []}
        return {"state": "approved", **profile}

    @router.get("/teacher/groups/{group_id}")
    def teacher_group(group_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = require_role(telegram_user, "teacher")
        if not database.teacher_can_access_group(user["id"], group_id):
            raise HTTPException(status_code=403, detail="Teacher is not assigned to this group")
        journal = journal_view(database.list_group_journal(group_id))
        grade_histories = journal.pop("students")
        students = database.list_group_students(group_id)
        if not students:
            students = [
                {
                    "id": item.get("journal_student_id") or f"journal:{item['name']}",
                    "display_name": item["name"],
                    "class_name": None,
                    "source": "journal_snapshot",
                    "identity_id": item.get("identity_id"),
                }
                for item in grade_histories
            ]
        return {"students": students, "grade_histories": grade_histories, **journal}

    @router.get("/teacher/journals/{source_id}/{group_marker}")
    def teacher_journal_marker(source_id: str, group_marker: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = require_role(telegram_user, "teacher")
        marker = group_marker.strip()
        if not database.teacher_can_access_journal_marker(user["id"], source_id, marker):
            raise HTTPException(status_code=403, detail="Teacher is not assigned to this journal subgroup")
        return {"source_id": source_id, "group_marker": marker, **journal_view(database.list_journal_marker(source_id, marker))}

    @router.get("/teacher/information")
    def teacher_information(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = require_role(telegram_user, "teacher")
        return {"items": [dict(row) for row in database.list_active_announcements(user["id"], "teacher")]}

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
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": [dict(row) for row in database.list_teacher_schedule(user["id"], start_day, end_day)]}

    return router
