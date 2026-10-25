from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Cookie, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from datetime import date, datetime, timedelta, timezone
import json
import hmac
import re
import hashlib
import secrets
import time
import os
import sqlite3
import logging
from html import escape
from urllib.parse import urlparse
from psycopg import OperationalError as PostgresOperationalError

from backend.database import Database
from backend.services.auth import AuthError, resolve_auth
from backend.services.identity import claim_identity, review_identity_claim
from backend.services.admin import (
    add_membership, assign_homeroom, assign_teacher, create_group, create_identity,
    publish_information,
)
from backend.services.google_live import GoogleApiError, GoogleLiveClient, GoogleLiveError, GoogleTokenStore, exchange_code
from backend.services.google_oauth import build_google_authorization_url, google_config
from backend.services.google_contract import CLASSROOM_READ_SCOPES, SHEETS_READ_SCOPE, SHEETS_WRITE_SCOPE
from backend.services.schedule_sheet_publishing import export_saved_week
from backend.services.school_template_selection import open_active_school_template
from backend.services.schedule_activities import ActivityConflict, ActivityNotFound, list_activities, save_activity, seed_activities
from backend.services.google_sync_service import CURRENT_JOURNAL_SPREADSHEETS, refresh_classroom, refresh_journal, refresh_grade_journal
from backend.services.canonical_schedule import effective_blocks, effective_status, project_student_range, project_teacher_range, publication_head, schedule_admin_observability
from backend.services.canonical_weekly_refresh import preview_current_template, preview_current_week, refresh_current_week
from backend.services.schedule_drafts import (
    audience_preview, get_draft, preview_changes,
)
from backend.services.schedule_experiments import (
    ExperimentConflict, ExperimentNotFound, create_experiment, delete_experiment,
    duplicate_experiment, experiment_audience_preview, get_experiment, list_experiments, save_experiment, create_effective_week_experiment,
)
from backend.services.school_experiment_workspace import (
    cached_audience_snapshot, import_school_template, school_directory, school_drafts, school_source,
)
from backend.services.student_schedule_validation import validate_student_projections
from backend.services.teacher import journal_view
from backend.config import get_settings
from backend.services.student_membership_reconciliation import run_live_dry_run, GoogleLiveError as ReconciliationGoogleLiveError
from backend.services.student_membership_apply import StudentMembershipApplyBlocked, apply_reviewed_plan, _readback
from backend.services.student_membership_manual_resolutions import load_manual_resolutions
from backend.services.student_membership_reconciliation import fetch_authoritative_sources, load_production_snapshot
from backend.services.telegram_admin_bot import handle_admin_code_update

logger = logging.getLogger(__name__)


class SessionPayload(BaseModel):
    init_data: str | None = None
    role: str = "student"


class ClaimPayload(BaseModel):
    identity_id: str
    role: str = "student"


def _student_grade(profile: dict | None) -> str:
    user = (profile or {}).get("user") or {}
    class_name = user.get("class_name") if hasattr(user, "get") else None
    match = re.match(r"\s*(\d{1,2})", str(class_name or ""))
    return match.group(1) if match else ""


class ReviewPayload(BaseModel):
    status: str


class IdentityPayload(BaseModel):
    display_name: str
    class_name: str | None = None
    kind: str = "student"
    subjects: list[str] = Field(default_factory=list)
    status: str = "active"
    comment: str | None = None


class GroupPayload(BaseModel):
    name: str
    group_type: str
    display_name: str | None = None
    subject: str | None = None
    base_class_name: str | None = None
    subject_subgroup: str | None = None
    exam_track: str | None = None
    default_teacher_identity_id: str | int | None = None
    comment: str | None = None


class MembershipPayload(BaseModel):
    group_id: str
    identity_id: str
    source: str = "admin_override"
    source_ref: str = ""
    member_role: str = "student"


class MembershipTransferPayload(BaseModel):
    identity_id: str
    from_group_id: str | None = None
    to_group_id: str | None = None
    reason: str = ""


class ArchiveGroupPayload(BaseModel):
    confirm_used: bool = False


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
    group_id: str | None = None
    assignment_id: str | None = None
    subject: str = ""
    active: bool = True
    grade_level: str | None = None
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


class JournalConnectPayload(BaseModel):
    spreadsheet_url: str
    grade: int = Field(default=9, ge=5, le=11)
    auto_sync_enabled: bool = True


class JournalAutoSyncPayload(BaseModel):
    spreadsheet_id: str
    enabled: bool


class JournalPeriodPayload(BaseModel):
    term_number: int = Field(ge=1, le=3)
    make_current: bool = False


class JournalCurrentTermPayload(BaseModel):
    spreadsheet_id: str = Field(min_length=1, max_length=200)
    grade: int = Field(ge=5, le=11)
    academic_year: str = Field(min_length=4, max_length=9)
    term_number: int = Field(ge=1, le=3)


class JournalStudentMatchPayload(BaseModel):
    identity_id: str
    preview_token: str | None = None


class JournalMatchPreviewPayload(BaseModel):
    spreadsheet_id: str = Field(min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=300)
    identity_id: str = Field(min_length=1, max_length=100)
    replace_existing: bool = False


class JournalMatchConfirmPayload(JournalMatchPreviewPayload):
    preview_token: str = Field(min_length=64, max_length=64)


class JournalTeamMembersPayload(BaseModel):
    identity_ids: list[str] = Field(max_length=30)


class BrowserCodePayload(BaseModel):
    code: str


class ReconciliationReviewPayload(BaseModel):
    approved: bool = True


class ScheduleDraftPayload(BaseModel):
    scope_kind: str
    scope_key: str
    expected_revision: int
    payload: dict
    base_version_id: str | None = None
    base_effective_week_id: str | None = None
    source_context: dict = Field(default_factory=dict)


class SchedulePublishPayload(BaseModel):
    scope_kind: str
    scope_key: str
    expected_revision: int
    comment: str = ""


class ScheduleExperimentCreatePayload(BaseModel):
    name: str
    source: str = "empty"
    payload: dict | None = None


class ScheduleExperimentSavePayload(BaseModel):
    expected_revision: int
    payload: dict
    name: str | None = None


class ScheduleExperimentAudiencePreviewPayload(BaseModel):
    payload: dict


class ScheduleExportPayload(BaseModel):
    expected_revision: int = Field(ge=1)


class ScheduleWeekPayload(ScheduleExportPayload):
    week_start: str


class ScheduleAudiencePreviewPayload(BaseModel):
    audience: dict
    occupied_audiences: list[dict] = Field(default_factory=list)


class ScheduleImportDraftPayload(BaseModel):
    week_start: str
    expected_revision: int
    proposed_changes: list[dict]
    source_context: dict
    scope_kind: str = "week"
    scope_key: str | None = None


class ScheduleConfirmTemplatePayload(BaseModel):
    expected_version_id: str


def _refresh_grade_journal_background(database: Database, settings: Any, grade: int, spreadsheet_id: str, run_id: Any, actor_id: Any = None, trigger_kind: str = "manual") -> None:
    try:
        result = refresh_grade_journal(database, settings, grade, spreadsheet_id=spreadsheet_id, trigger_kind=trigger_kind, run_id=run_id)
        tabs = result.get("tabs", [])
        success_count = sum(item.get("status") == "success" for item in tabs)
        failed_count = sum(item.get("status") == "error" for item in tabs)
        run_status = "success" if result.get("status") == "success" else "partial"
        database.finish_journal_sync(run_id, status=run_status, counts={"completed_tabs": len(tabs), "total_tabs": len(tabs), "succeeded": success_count, "failed": failed_count})
        if trigger_kind == "manual" and actor_id is not None:
            try:
                database.record_audit_event("journal_sync.completed", "journal_sync", {"spreadsheet_id": spreadsheet_id, "grade": grade, "status": run_status}, actor_id)
            except Exception:
                logger.exception("Failed to record completed journal sync audit event run_id=%s", run_id)
    except Exception as error:
        diagnostic = getattr(error, "diag", None)
        safe_message = getattr(diagnostic, "message_primary", None) if diagnostic is not None else None
        logger.error("Background journal refresh failed spreadsheet_id=%s grade=%s run_id=%s error_type=%s error=%s", spreadsheet_id, grade, run_id, type(error).__name__, safe_message or ("database error" if diagnostic is not None else type(error).__name__))
        message = str(error) if isinstance(error, (GoogleLiveError, ValueError, RuntimeError)) else f"Unexpected journal refresh failure: {type(error).__name__}"
        try:
            database.finish_journal_sync(run_id, status="error", error=message[:1000])
        except Exception:
            logger.exception("Failed to finalize journal sync run_id=%s after refresh failure", run_id)


def create_router(database: Database) -> APIRouter:
    router = APIRouter(prefix="/api")
    from backend.services.consolidated_workspace import isolated_consolidation_enabled, require_consolidated_admin

    def authenticate(init_data: str | None, dev_auth: str | None, telegram_header: str | None = None, browser_session: str | None = None):
        browser_token = browser_session or (dev_auth.removeprefix("browser:") if dev_auth and dev_auth.startswith("browser:") else None)
        if browser_token:
            session = database.get_admin_browser_session(browser_token)
            if not session:
                raise HTTPException(status_code=401, detail="Admin browser session is invalid or expired")
            return {"id": session["telegram_user_id"], "user_id": session["user_id"], "role": "admin", "browser_session": True, "browser_session_token": browser_token}
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

    def canonical_backend_enabled() -> bool:
        return get_settings().schedule_backend == "canonical"

    def canonical_admin_backend_enabled() -> bool:
        """Allow read-only admin shadow work without enabling user projections."""
        return get_settings().schedule_backend in {"canonical_shadow", "canonical"}

    def google_reconnect_ready(settings: Any) -> bool:
        if not (settings.google_oauth_client_id and settings.google_oauth_client_secret and settings.google_oauth_redirect_uri and settings.google_oauth_token_encryption_key):
            return False
        redirect = urlparse(settings.google_oauth_redirect_uri)
        if redirect.path != "/api/integrations/google/callback":
            return False
        frontend = urlparse(settings.frontend_public_url or "")
        if not frontend.netloc or frontend.scheme not in {"http", "https"}:
            return False
        frontend_origin = f"{frontend.scheme}://{frontend.netloc}"
        allowed_origins = set()
        for configured_origin in settings.cors_origins:
            parsed_origin = urlparse(configured_origin)
            if parsed_origin.scheme and parsed_origin.netloc:
                allowed_origins.add(f"{parsed_origin.scheme}://{parsed_origin.netloc}")
        if frontend_origin not in allowed_origins:
            return False
        if settings.app_env == "production":
            backend = urlparse(settings.backend_public_url or "")
            return redirect.scheme == "https" and bool(backend.netloc) and redirect.netloc == backend.netloc and frontend.scheme == "https"
        return redirect.scheme == "http" and redirect.hostname in {"localhost", "127.0.0.1"} and frontend.scheme == "http" and frontend.hostname in {"localhost", "127.0.0.1"}

    def google_callback_page(title: str, message: str, status_code: int = 200, *, oauth_result: str | None = None) -> HTMLResponse:
        settings = get_settings()
        return_url = (settings.frontend_public_url or "").rstrip("/") + "/admin/settings"
        return_link = f'<p><a href="{escape(return_url, quote=True)}">Вернуться в My Island</a></p>' if return_url else "<p>Можно закрыть эту вкладку и вернуться в админку.</p>"
        frontend_origin = ""
        try:
            candidate = urlparse(settings.frontend_public_url or "")
            if candidate.scheme in {"http", "https"} and candidate.netloc:
                frontend_origin = candidate.scheme + "://" + candidate.netloc
        except ValueError:
            pass
        notification = ""
        if oauth_result and frontend_origin:
            notification = (
                "<script>if(window.opener){window.opener.postMessage("
                + json.dumps({"type": "my-island-google-oauth", "status": oauth_result}).replace("<", "\\u003c")
                + ","
                + json.dumps(frontend_origin).replace("<", "\\u003c")
                + ");setTimeout(()=>window.close(),150);}</script>"
            )
        body = f'<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>{escape(title)}</title><main style="font:16px system-ui;max-width:620px;margin:12vh auto;padding:24px;color:#164b50"><h1>{escape(title)}</h1><p>{escape(message)}</p>{return_link}</main>{notification}'
        return HTMLResponse(body, status_code=status_code, headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        })

    def verify_google_status(settings: Any) -> dict[str, Any]:
        config = google_config(settings)
        reconnect_available = google_reconnect_ready(settings)
        if not config.oauth_ready or not settings.google_oauth_token_encryption_key:
            return {"status": "configuration_missing", "account_email": None, "reconnect_available": False, "message": "На сервере не настроены Google OAuth или шифрование токена."}
        store = GoogleTokenStore(database=database, encryption_key=settings.google_oauth_token_encryption_key)
        try:
            token = store.load()
        except GoogleLiveError:
            return {"status": "reauthorization_required", "account_email": None, "reconnect_available": reconnect_available, "message": "Сохранённые данные Google не читаются. Переподключите аккаунт."}
        if not token or not token.get("refresh_token"):
            return {"status": "reauthorization_required", "account_email": None, "reconnect_available": reconnect_available, "message": "Серверный Google аккаунт не подключён."}
        client = GoogleLiveClient(config, token, store)
        try:
            profile = client.refresh_access_token(settings.google_oauth_allowed_email)
            account = str(profile.get("email") or "").strip().casefold()
        except GoogleApiError as error:
            if error.error_code == "invalid_grant":
                return {"status": "reauthorization_required", "account_email": None, "reconnect_available": reconnect_available, "message": "Google отклонил долгосрочный доступ. Переподключите аккаунт."}
            if error.status == 403 and error.reason == "insufficientPermissions":
                return {"status": "insufficient_permissions", "account_email": None, "reconnect_available": reconnect_available, "message": "Google не выдал необходимые разрешения."}
            return {"status": "temporary_error", "account_email": None, "reconnect_available": reconnect_available, "message": "Google временно не ответил. Повторите проверку позже."}
        except GoogleLiveError as error:
            message = str(error)
            is_wrong_account = "Подключите аккаунт" in message
            return {"status": "reauthorization_required" if is_wrong_account else "temporary_error", "account_email": None, "reconnect_available": reconnect_available, "message": message if is_wrong_account else "Не удалось проверить Google. Повторите попытку позже."}

        granted = set(str(client.token.get("scope") or "").split())
        if not granted.intersection({SHEETS_READ_SCOPE, SHEETS_WRITE_SCOPE}):
            return {"status": "insufficient_permissions", "account_email": account, "reconnect_available": reconnect_available, "message": "Аккаунту не разрешено читать Google Таблицы."}
        journal_check = {"status": "checked", "title": None}
        try:
            metadata = client.spreadsheet(CURRENT_JOURNAL_SPREADSHEETS[9])
            journal_check["title"] = str((metadata.get("properties") or {}).get("title") or "") or None
        except GoogleApiError as error:
            if error.status == 403 and error.reason == "insufficientPermissions":
                return {"status": "insufficient_permissions", "account_email": account, "reconnect_available": reconnect_available, "message": "У Google аккаунта нет разрешения читать журнал девятого класса.", "journal": {"status": "insufficient_permissions"}}
            if error.status in {403, 404}:
                return {"status": "spreadsheet_access_denied", "account_email": account, "reconnect_available": reconnect_available, "message": "Токен действителен, но у выбранного Google аккаунта нет доступа к журналу девятого класса.", "journal": {"status": "access_denied"}}
            return {"status": "temporary_error", "account_email": account, "reconnect_available": reconnect_available, "message": "Google Таблицы временно не ответили."}
        except GoogleLiveError:
            return {"status": "temporary_error", "account_email": account, "reconnect_available": reconnect_available, "message": "Не удалось проверить журнал. Повторите попытку позже."}

        schedule_check = None
        schedule_id = settings.google_sheets_spreadsheet_id
        if schedule_id and schedule_id != CURRENT_JOURNAL_SPREADSHEETS[9]:
            try:
                schedule_metadata = client.spreadsheet(schedule_id)
                schedule_check = {"status": "checked", "title": str((schedule_metadata.get("properties") or {}).get("title") or "") or None}
            except GoogleApiError as error:
                schedule_check = {"status": "access_denied" if error.status in {403, 404} else "temporary_error"}
            except GoogleLiveError:
                schedule_check = {"status": "temporary_error"}
        return {
            "status": "connected",
            "account_email": account,
            "reconnect_available": reconnect_available,
            "message": f"Google проверен: доступ к журналу подтверждён для {account}.",
            "journal": journal_check,
            "schedule_source": schedule_check,
            "capabilities": {
                "sheets_read": bool(granted.intersection({SHEETS_READ_SCOPE, SHEETS_WRITE_SCOPE})),
                "sheets_write": SHEETS_WRITE_SCOPE in granted,
                "classroom_read": all(scope in granted for scope in CLASSROOM_READ_SCOPES),
            },
        }

    @router.post("/admin/auth/challenge")
    def create_admin_browser_challenge(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        code, expires_at = database.create_admin_login_challenge(actor["id"])
        database.record_audit_event("admin_auth.challenge_created", "admin_browser_session", {"expires_at": expires_at}, actor["id"])
        return {"code": code, "expires_at": expires_at, "single_use": True}

    @router.post("/admin/google/reconnect")
    def google_reconnect_start(schedule_export: bool = False, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        settings = get_settings()
        if not google_reconnect_ready(settings):
            raise HTTPException(status_code=503, detail="Самостоятельное переподключение Google ещё не настроено на сервере")
        state = secrets.token_urlsafe(32)
        database.create_google_oauth_state(hashlib.sha256(state.encode("utf-8")).hexdigest(), actor["id"], int(time.time()) + 600)
        authorization_url = build_google_authorization_url(settings, state, prompt="consent select_account", schedule_export=schedule_export)
        database.record_audit_event("google_oauth.reconnect_started", "google_oauth", {"expires_in_seconds": 600}, actor["id"])
        return {"authorization_url": authorization_url, "expires_in_seconds": 600}

    @router.get("/integrations/google/callback", response_class=HTMLResponse)
    def google_reconnect_callback(state: str = "", code: str | None = None, error: str | None = None):
        actor_id = database.consume_google_oauth_state(hashlib.sha256(state.encode("utf-8")).hexdigest()) if state else None
        if actor_id is None:
            return google_callback_page("Не удалось переподключить Google", "Ссылка устарела или уже использована. Вернитесь в админку и начните подключение заново.", 400, oauth_result="error")
        if error:
            database.record_audit_event("google_oauth.reconnect_cancelled", "google_oauth", {"provider_error": error[:80]}, actor_id)
            return google_callback_page("Подключение не завершено", "Доступ Google не был подтверждён. Текущее подключение не изменено.", 400, oauth_result="cancelled")
        if not code:
            return google_callback_page("Не удалось переподключить Google", "Google не вернул код авторизации. Текущее подключение не изменено.", 400, oauth_result="error")
        settings = get_settings()
        if not google_reconnect_ready(settings):
            return google_callback_page("Подключение не настроено", "Администратору сервера нужно завершить одноразовую настройку OAuth.", 503, oauth_result="error")
        try:
            token = exchange_code(google_config(settings), code)
            if not token.get("refresh_token"):
                raise GoogleLiveError("Google did not issue a refresh token")
            scopes = set(str(token.get("scope") or "").split())
            if not scopes.intersection({SHEETS_READ_SCOPE, SHEETS_WRITE_SCOPE}):
                raise GoogleLiveError("Google не предоставил доступ на чтение Google Таблиц")
            store = GoogleTokenStore(database=database, encryption_key=settings.google_oauth_token_encryption_key)
            client = GoogleLiveClient(google_config(settings), token, store)
            account = str(client.userinfo().get("email") or "").strip().casefold()
            if not account or account != settings.google_oauth_allowed_email:
                raise GoogleLiveError(f"Подключите аккаунт {settings.google_oauth_allowed_email}")
            client.spreadsheet(CURRENT_JOURNAL_SPREADSHEETS[9])
            store.save(token, updated_by=actor_id, account_email=account)
        except GoogleApiError as exc:
            if exc.status == 403 and exc.reason == "insufficientPermissions":
                message = "Google не выдал право чтения таблиц. Запустите переподключение и подтвердите доступ к Google Таблицам."
            elif exc.status in {403, 404}:
                message = f"У аккаунта {settings.google_oauth_allowed_email} нет доступа к журналу девятого класса. Откройте таблицу и предоставьте этому аккаунту доступ."
            else:
                message = "Google временно не ответил при проверке журнала. Попробуйте позже."
            database.record_audit_event("google_oauth.reconnect_failed", "google_oauth", {"reason": str(exc)[:180]}, actor_id)
            return google_callback_page("Подключение не завершено", message, 400, oauth_result="error")
        except GoogleLiveError as exc:
            database.record_audit_event("google_oauth.reconnect_failed", "google_oauth", {"reason": str(exc)[:180]}, actor_id)
            return google_callback_page("Подключение не завершено", str(exc), 400, oauth_result="error")
        database.record_audit_event("google_oauth.reconnected", "google_oauth", {"account_email": account, "granted_scopes": sorted(scopes)}, actor_id)
        return google_callback_page("Google подключён", f"Доступ к журналу подтверждён для {account}. Токен сохранён защищённо.", oauth_result="success")

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
            legacy_target = database.get_user_by_id(preview["target_user_id"]) if preview and not preview["target_identity_id"] else None
            target = database.get_student_identity(preview["target_identity_id"] if preview and preview["target_identity_id"] else (legacy_target["identity_id"] if legacy_target else ""))
            if not target or not target["identity_id"] or target["kind"] != "student":
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
        if not canonical_backend_enabled():
            return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "canonical_backend_disabled", "schedule": [], "day_block": None, "schedule_issues": [], "homework": [dict(row) for row in database.list_classroom_coursework_for_user(user["id"])], "announcements": [dict(row) for row in database.list_active_announcements(user["id"], "student")]}
        projection = project_student_range(database, user["identity_id"], requested_day)
        schedule = [item for item in projection.get("items", []) if item.get("date") == requested_day]
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": projection.get("status"), "schedule": schedule, "day_block": None, "schedule_issues": projection.get("issues", []), "homework": [dict(row) for row in database.list_classroom_coursework_for_user(user["id"])], "announcements": [dict(row) for row in database.list_active_announcements(user["id"], "student")]}

    @router.get("/student/grades")
    def student_grades(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_student_preview_id: str | None = Header(default=None, alias="X-Student-Preview-Id")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = student_subject(telegram_user, x_student_preview_id)
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": [dict(row) for row in database.list_official_grades_for_user(user["id"])]}

    @router.get("/student/journals")
    def student_journals(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_student_preview_id: str | None = Header(default=None, alias="X-Student-Preview-Id")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = student_subject(telegram_user, x_student_preview_id)
        if not user["identity_id"]:
            raise HTTPException(status_code=403, detail="Identity approval required before personal data access")
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "approved", "items": database.student_journals(user["identity_id"])}

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
        if not canonical_backend_enabled():
            return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "canonical_backend_disabled", "items": [], "issues": []}
        projection = project_student_range(database, user["identity_id"], start_day, end_day)
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": projection.get("status"), **projection}

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
    def admin_people(kind: str | None = None, q: str | None = None, class_name: str | None = None, group_id: str | None = None, has_issues: bool | None = None, protected: bool | None = None, include_inactive: bool = False, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        if kind not in {None, "student", "teacher"}:
            raise HTTPException(status_code=400, detail="kind must be student or teacher")
        if include_inactive and kind != "teacher":
            raise HTTPException(status_code=400, detail="include_inactive is available only for teachers")
        return {"items": database.list_people_library_summary(kind=kind, query=q, class_name=class_name, group_id=group_id, has_issues=has_issues, protected=protected, include_inactive=include_inactive)}

    @router.get("/admin/people/filters")
    def admin_people_filters(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return database.list_people_filter_options()

    @router.get("/admin/people/{identity_id}/schedule")
    def admin_person_schedule(identity_id: str, start_day: str, end_day: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        person = database.get_people_library_person(identity_id)
        if not person or person.get("kind") != "student":
            raise HTTPException(status_code=404, detail="Active student identity was not found")
        try:
            date.fromisoformat(start_day)
            if end_day:
                date.fromisoformat(end_day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="start_day and end_day must be ISO dates") from error
        projection = project_student_range(database, identity_id, start_day, end_day)
        return {"student": {"id": person["id"], "display_name": person["display_name"], "class_name": person.get("class_name"), "status": person.get("status")}, **projection}

    @router.get("/admin/people/{identity_id}")
    def admin_person(identity_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        person = database.get_people_library_person(identity_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person was not found")
        return person

    @router.patch("/admin/people/{identity_id}")
    def update_admin_person(identity_id: str, payload: dict, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            allowed = {"display_name", "class_name", "status", "subjects", "comment"}
            if set(payload) - allowed:
                raise ValueError("Unsupported person fields were supplied")
            person = database.update_admin_identity(identity_id, payload, actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not person:
            raise HTTPException(status_code=404, detail="Person was not found")
        person_data = dict(person)
        return {"id": person_data["id"], "kind": person_data["kind"], "display_name": person_data["display_name"], "class_name": person_data["class_name"], "status": person_data["status"], "subjects": database.list_teacher_subjects(identity_id) if person_data["kind"] == "teacher" else [], "comment": person_data.get("admin_note")}

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
        reconnect_available = google_reconnect_ready(settings)
        reconnect_message = "" if reconnect_available else "Для переподключения из админки нужна настройка OAuth callback и ключа шифрования на сервере."
        return {"health": database.system_health(), "settings": {"environment": settings.app_env, "database_configured": bool(settings.database_url), "dev_auth_enabled": settings.dev_auth_enabled, "google": {"configuration_present": google_configured, "reconnect_available": reconnect_available, "reconnect_message": reconnect_message}}}

    @router.get("/admin/google/status")
    def admin_google_status(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return verify_google_status(get_settings())

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
    def admin_groups(include_archived: bool = False, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"items": database.list_groups_admin_summary(include_archived=include_archived)}

    @router.get("/admin/groups/{group_id}/archive-preview")
    def preview_group_archive(group_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        preview = database.group_archive_preview(group_id)
        if not preview:
            raise HTTPException(status_code=404, detail="Group was not found")
        return {**preview, "archive_ready": True}

    @router.post("/admin/groups/{group_id}/archive")
    def archive_group(group_id: str, payload: ArchiveGroupPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            group = database.archive_admin_group(group_id, actor["id"], confirm_used=payload.confirm_used)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not group:
            raise HTTPException(status_code=404, detail="Group was not found")
        return {"id": str(group["id"]), "archived": True}

    @router.post("/admin/groups/{group_id}/restore")
    def restore_group(group_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        group = database.restore_admin_group(group_id, actor["id"])
        if not group:
            raise HTTPException(status_code=404, detail="Group was not found")
        return {"id": str(group["id"]), "archived": False}

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
        items = database.journal_status()
        connected = {item['spreadsheet_id'] for item in items}
        return {"items": items, "scheduler_configured": database.journal_scheduler_configured(),
                "known_sources": [{"grade": grade, "spreadsheet_id": key, "connected": key in connected}
                                  for grade, key in CURRENT_JOURNAL_SPREADSHEETS.items()]}

    @router.post("/admin/journals/connect")
    def admin_journal_connect(payload: JournalConnectPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        raw = payload.spreadsheet_url.strip()
        match = re.search(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)", raw)
        spreadsheet_id = match.group(1) if match else raw
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", spreadsheet_id):
            raise HTTPException(status_code=400, detail="Укажите ссылку на Google Таблицу или её идентификатор")
        run_id = database.start_journal_sync(spreadsheet_id, payload.grade, None, "manual")
        if run_id is None:
            raise HTTPException(status_code=409, detail="Для этой таблицы уже выполняется синхронизация")
        try:
            result = refresh_grade_journal(database, get_settings(), payload.grade, spreadsheet_id=spreadsheet_id)
            success = result.get("status") == "success"
            scheduler_configured = database.journal_scheduler_configured()
            automation_enabled = success and payload.auto_sync_enabled and scheduler_configured
            if automation_enabled:
                database.set_journal_auto_sync(spreadsheet_id, True)
            database.finish_journal_sync(run_id, status="success" if success else "partial", counts={"tabs": len(result.get("tabs", []))})
            database.record_audit_event("journal_source.connected", "journal_source", {"spreadsheet_id": spreadsheet_id, "grade": payload.grade, "auto_sync_enabled": automation_enabled}, actor["id"])
            return {**result, "auto_sync_enabled": automation_enabled, "scheduler_configured": scheduler_configured}
        except (GoogleLiveError, ValueError, RuntimeError) as error:
            database.finish_journal_sync(run_id, status="error", error=str(error))
            raise HTTPException(status_code=502, detail=str(error)) from error
        except Exception as error:
            logger.exception("Unexpected failure while connecting journal spreadsheet_id=%s grade=%s", spreadsheet_id, payload.grade)
            try:
                database.finish_journal_sync(run_id, status="error", error=f"Unexpected journal connection failure: {type(error).__name__}")
            except Exception:
                logger.exception("Failed to finalize journal sync run_id=%s after refresh failure", run_id)
            raise HTTPException(status_code=500, detail="Не удалось обновить журнал. Попробуйте ещё раз позже.") from error

    @router.post("/admin/journals/refresh-grade", status_code=202)
    def admin_journal_refresh_grade(background_tasks: BackgroundTasks, grade: int = 9, spreadsheet_id: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        target_id = spreadsheet_id or CURRENT_JOURNAL_SPREADSHEETS.get(grade)
        if not target_id:
            raise HTTPException(status_code=400, detail="Для этого класса ещё не подключена таблица")
        run_id = database.start_journal_sync(target_id, grade, None, "manual")
        if run_id is None:
            raise HTTPException(status_code=409, detail="Для этой таблицы уже выполняется синхронизация")
        background_tasks.add_task(_refresh_grade_journal_background, database, get_settings(), grade, target_id, run_id, actor["id"])
        return {"run_id": str(run_id), "spreadsheet_id": target_id, "grade": grade, "status": "running"}

    @router.get("/admin/journals/sync-runs/{run_id}")
    def admin_journal_sync_run(run_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        run = database.get_journal_sync_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Синхронизация журнала не найдена")
        return run

    @router.post("/admin/journals/auto-sync")
    def admin_journal_auto_sync(payload: JournalAutoSyncPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        if payload.enabled and not database.journal_scheduler_configured():
            raise HTTPException(status_code=409, detail="Почасовое расписание ещё не настроено")
        changed = database.set_journal_auto_sync(payload.spreadsheet_id, payload.enabled)
        if not changed:
            raise HTTPException(status_code=404, detail="Источник журнала не найден")
        database.record_audit_event("journal_sync.automation_changed", "journal_source", {"spreadsheet_id": payload.spreadsheet_id, "enabled": payload.enabled}, actor["id"])
        return {"spreadsheet_id": payload.spreadsheet_id, "auto_sync_enabled": payload.enabled}

    @router.put("/admin/journals/current-term")
    def admin_journal_current_term(payload: JournalCurrentTermPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        if not database.set_journal_current_term(payload.spreadsheet_id, payload.grade, payload.academic_year, payload.term_number):
            raise HTTPException(status_code=404, detail="Источник журнала не найден")
        database.record_audit_event("journal_period.current_changed", "journal_source", {"spreadsheet_id": payload.spreadsheet_id, "grade": payload.grade, "academic_year": payload.academic_year, "term_number": payload.term_number}, actor["id"])
        return {"spreadsheet_id": payload.spreadsheet_id, "academic_year": payload.academic_year, "current_term": payload.term_number}

    @router.put("/admin/journals/{source_id}/period")
    def admin_journal_source_period(source_id: str, payload: JournalPeriodPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        if not database.set_journal_source_term(source_id, payload.term_number, payload.make_current):
            raise HTTPException(status_code=409, detail="Период журнала не удалось сохранить: проверьте предмет и уже привязанные вкладки")
        database.record_audit_event("journal_period.assigned", "journal_source", {"source_id": source_id, "term_number": payload.term_number, "make_current": payload.make_current}, actor["id"])
        return {"source_id": source_id, "term_number": payload.term_number, "period_status": "current" if payload.make_current else "saved"}

    @router.get("/admin/journals/matching")
    def admin_journal_matching(spreadsheet_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return database.journal_matching_overview(spreadsheet_id)

    @router.post("/admin/journals/matching/preview")
    def admin_journal_matching_preview(payload: JournalMatchPreviewPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return database.preview_journal_match(payload.spreadsheet_id,payload.source_name,payload.identity_id,payload.replace_existing)
        except ValueError as error:
            raise HTTPException(status_code=409,detail=str(error)) from error

    @router.post("/admin/journals/matching/confirm")
    def admin_journal_matching_confirm(payload: JournalMatchConfirmPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return database.confirm_journal_match(payload.spreadsheet_id,payload.source_name,payload.identity_id,payload.preview_token,actor['id'],payload.replace_existing)
        except ValueError as error:
            raise HTTPException(status_code=409,detail=str(error)) from error

    @router.put("/admin/journals/teams/{team_id}/members")
    def admin_journal_team_members(team_id: str, payload: JournalTeamMembersPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return database.set_journal_team_members(team_id,payload.identity_ids,actor['id'])
        except ValueError as error:
            raise HTTPException(status_code=409,detail=str(error)) from error

    @router.get("/admin/journals/{source_id}/detail")
    def admin_journal_detail(source_id: str, group_marker: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        detail = database.journal_detail(source_id, group_marker)
        if not detail:
            raise HTTPException(status_code=404, detail="Журнал не найден")
        return detail

    @router.get("/admin/journals/{source_id}/sync-history")
    def admin_journal_sync_history(source_id: str, limit: int = 20, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        source = next((item for item in database.journal_status() if str(item["id"]) == source_id), None)
        if not source:
            raise HTTPException(status_code=404, detail="Журнал не найден")
        return {"items": database.journal_sync_history(source["spreadsheet_id"], limit)}

    @router.post("/admin/journals/{source_id}/students/{student_id}/match")
    def admin_journal_student_match(source_id: str, student_id: str, payload: JournalStudentMatchPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        if not payload.preview_token:
            raise HTTPException(status_code=409,detail="Подтвердите связь через предпросмотр в панели «Сопоставление».")
        detail = database.journal_detail(source_id)
        student = next((item for item in (detail or {}).get('students',[]) if str(item['id']) == student_id),None)
        if not student:
            raise HTTPException(status_code=404,detail="Запись журнала не найдена")
        try:
            return database.confirm_journal_match(detail['source']['spreadsheet_id'],student['display_name'],payload.identity_id,payload.preview_token,actor['id'])
        except ValueError as error:
            raise HTTPException(status_code=409,detail=str(error)) from error

    @router.post("/admin/journals/cron", status_code=202)
    def admin_journal_cron(background_tasks: BackgroundTasks, x_journal_cron_secret: str | None = Header(default=None, alias="X-Journal-Cron-Secret")):
        settings = get_settings()
        expected = settings.journal_cron_secret
        if not expected or not x_journal_cron_secret or not hmac.compare_digest(expected, x_journal_cron_secret):
            raise HTTPException(status_code=401, detail="Scheduled journal authorization failed")
        outcomes = []
        for source in database.journal_auto_sync_sources():
            run_id = database.start_journal_sync(source["spreadsheet_id"], int(source["grade"]), None, "scheduled")
            if run_id is None:
                outcomes.append({"spreadsheet_id": source["spreadsheet_id"], "status": "already_running"})
                continue
            background_tasks.add_task(
                _refresh_grade_journal_background, database, settings, int(source["grade"]),
                source["spreadsheet_id"], run_id, None, "scheduled",
            )
            outcomes.append({"spreadsheet_id": source["spreadsheet_id"], "run_id": str(run_id), "status": "running"})
        return {"items": outcomes}

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
        if not canonical_admin_backend_enabled():
            return {"status": "blocked", "ok": False, "message": "Canonical schedule backend is disabled"}
        try:
            return refresh_current_week(database, get_settings(), week_start or None)
        except (GoogleLiveError, ValueError, RuntimeError) as error:
            return {"status": "blocked", "ok": False, "message": str(error)}

    @router.post("/admin/schedule/preview")
    def admin_schedule_preview(week_start: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        if not canonical_admin_backend_enabled():
            return {"status": "blocked", "ok": False, "message": "Canonical schedule backend is disabled"}
        try:
            result = preview_current_week(database, get_settings(), week_start or None)
            if result.get("status") == "preview":
                result["draft_changes"] = preview_changes(result)
            return result
        except (GoogleLiveError, ValueError, RuntimeError) as error:
            return {"status": "blocked", "ok": False, "message": str(error)}

    @router.post("/admin/schedule/template/confirm")
    def admin_schedule_confirm_template(payload: ScheduleConfirmTemplatePayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        raise HTTPException(status_code=410, detail="Legacy ScheduleAdmin writes are retired; use the constructor publication workflow")

    @router.post("/admin/schedule/template/preview")
    def admin_schedule_template_preview(payload: ScheduleConfirmTemplatePayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        if not canonical_admin_backend_enabled():
            return {"status": "blocked", "message": "Canonical schedule backend is disabled"}
        try:
            result = preview_current_template(database, get_settings(), payload.expected_version_id)
            if result.get("status") == "preview":
                result["draft_changes"] = preview_changes(result)
            return result
        except (GoogleLiveError, ValueError, RuntimeError) as error:
            return {"status": "blocked", "message": str(error)}

    @router.get("/admin/schedule/validate-students")
    def admin_schedule_validate_students(week_start: str, grades: str = "", init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        if not canonical_admin_backend_enabled():
            return {"status": "blocked", "ok": False, "message": "Canonical schedule backend is disabled"}
        selected_grades = [value.strip() for value in grades.split(",") if value.strip()]
        return validate_student_projections(database, week_start, grades=selected_grades or None)

    @router.post("/admin/schedule/recalculate")
    def admin_schedule_recalculate(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        return {"status": "blocked", "ok": False,
                "message": "There is no legacy recalculation path. Reconciliation must be completed against the approved canonical template."}

    @router.get("/admin/schedule/source-policy")
    def admin_schedule_source_policy(week_start: date | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        selected = week_start or date.today()
        monday = (selected - timedelta(days=selected.weekday())).isoformat()
        head = publication_head(database, f"week:{monday}") or publication_head(database, "template")
        return {"week_start": monday, "manual_publication_active": bool(head),
                "review_required": bool(head), "source_checked": False,
                "automatic_application_enabled": canonical_admin_backend_enabled() and not bool(head)}

    @router.get("/admin/schedule/overview")
    def admin_schedule_overview(week_start: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return effective_status(database, week_start) if canonical_admin_backend_enabled() else {"status": "canonical_backend_disabled", "authoritative": False, "weeks": [], "summary": {"lessons": 0, "issues": 0}}

    @router.get("/admin/schedule/observability")
    def admin_schedule_observability(week_start: str | None = None, editor: bool = False, effective_week_id: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        """Read-only canonical/effective schedule diagnostics for the admin console."""
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return schedule_admin_observability(database, week_start, include_student_projection=not editor, effective_week_id=effective_week_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/admin/schedule/draft")
    def admin_schedule_draft(scope_kind: str, scope_key: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return get_draft(database, scope_kind, scope_key)

    @router.get("/admin/schedule/experiments")
    def admin_schedule_experiments(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return {"items": list_experiments(database)}

    @router.get("/admin/schedule/experiments/capabilities")
    def admin_schedule_experiment_capabilities(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        local_workspace = os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1" and os.getenv("MY_ISLAND_TEST_PROFILE") != "e2e"
        school_enabled = isolated_consolidation_enabled() or local_workspace or (bool(database.database_url) and not get_settings().local_test_mode)
        return {"demo_available": get_settings().local_test_mode and not isolated_consolidation_enabled(),
                "school_available": school_enabled,
                "consolidated_schedule": os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") == "1"
                    and (isolated_consolidation_enabled() or bool(database.database_url) and not local_workspace)}

    def school_stores() -> tuple[Database, Database]:
        if isolated_consolidation_enabled():
            return database, database
        if os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1" and os.getenv("MY_ISLAND_TEST_PROFILE") != "e2e":
            try:
                source = school_source()
                # The explicitly enabled local workspace retains its isolated SQLite drafts.
                store = school_drafts()
                return source, store
            except (RuntimeError, ValueError) as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
        if database.database_url and not get_settings().local_test_mode:
            # Production drafts and the canonical directory live in the same Postgres database.
            return database, database
        raise HTTPException(status_code=404, detail="School workspace is disabled")

    def school_source_database() -> Database:
        if isolated_consolidation_enabled():
            return database
        if local_school_workspace_enabled():
            try:
                return school_source()
            except (RuntimeError, ValueError) as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
        if database.database_url and not get_settings().local_test_mode:
            return database
        raise HTTPException(status_code=404, detail="School workspace is disabled")

    def local_school_workspace_enabled() -> bool:
        return os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1" and os.getenv("MY_ISLAND_TEST_PROFILE") != "e2e"

    def require_real_school_admin(token: str | None, authenticated_principal: dict | None = None) -> None:
        if isolated_consolidation_enabled():
            require_consolidated_admin(database, token)
            return
        if not local_school_workspace_enabled():
            # Every production caller has already passed require_role(..., "admin") against
            # this same database, whether authenticated through Telegram or browser session.
            if database.database_url and not get_settings().local_test_mode and authenticated_principal:
                return
            raise HTTPException(status_code=404, detail="School workspace is disabled")
        if not token:
            raise HTTPException(status_code=401, detail="Real school administrator login is required")
        # The preview route has already validated this same browser session and
        # required its admin role. Reusing that request-scoped result avoids two
        # identical Supabase reads while preserving the separate school-session
        # check for Telegram-authenticated callers.
        verified_token = (authenticated_principal or {}).get("browser_session_token")
        if (authenticated_principal or {}).get("browser_session") and verified_token and hmac.compare_digest(str(token), str(verified_token)):
            return
        try:
            source = school_source()
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        school = Database(database_url=source.database_url)
        session = school.get_admin_browser_session(token)
        if not session or not school.user_has_role(session["user_id"], "admin"):
            raise HTTPException(status_code=401, detail="School administrator session is invalid or expired")

    def activity_store(workspace: str, token: str | None, principal: dict | None = None) -> Database:
        if workspace == "school":
            require_real_school_admin(token, principal)
            _, store = school_stores()
            return store
        if workspace != "default":
            raise HTTPException(status_code=422, detail="Unknown schedule workspace")
        return database

    @router.get("/admin/schedule/activities")
    def admin_activities(workspace: str = "default", include_archived: bool = False, init_data: str | None = None,
                         x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
                         x_school_admin_session: str | None = Header(default=None)):
        principal = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        store = activity_store(workspace, x_school_admin_session, principal)
        if not store.database_url:
            seed_activities(store)
        return {"items": list_activities(store, include_archived=include_archived)}

    @router.post("/admin/schedule/activities")
    def admin_activity_create(payload: dict, workspace: str = "default", init_data: str | None = None,
                              x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
                              x_school_admin_session: str | None = Header(default=None)):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return save_activity(activity_store(workspace, x_school_admin_session, actor), payload, actor_user_id=actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.put("/admin/schedule/activities/{activity_id}")
    def admin_activity_edit(activity_id: str, payload: dict, workspace: str = "default", init_data: str | None = None,
                            x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
                            x_school_admin_session: str | None = Header(default=None)):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            revision = payload.get("expected_revision")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise ValueError("expected_revision must be a positive integer")
            return save_activity(activity_store(workspace, x_school_admin_session, actor), payload,
                                 activity_id=activity_id, expected_revision=revision, actor_user_id=actor["id"])
        except ActivityConflict as error:
            raise HTTPException(status_code=409, detail=jsonable_encoder({"message": str(error), "current": error.current})) from error
        except ActivityNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def new_week(store: Database, experiment_id: str, payload: ScheduleWeekPayload, actor: dict):
        from backend.services.schedule_experiments import create_experiment_week
        try:
            return create_experiment_week(store, experiment_id, week_start=payload.week_start,
                                          expected_revision=payload.expected_revision, actor_user_id=actor["id"])
        except ExperimentConflict as error:
            raise HTTPException(status_code=409, detail=jsonable_encoder({"message": str(error), "current": error.current})) from error
        except ExperimentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/admin/schedule/experiments/{experiment_id}/week")
    def admin_schedule_week(experiment_id: str, payload: ScheduleWeekPayload, init_data: str | None = None,
                            x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return new_week(database, experiment_id, payload, actor)

    @router.post("/admin/schedule/experiments/school/{experiment_id}/week")
    def admin_school_week(experiment_id: str, payload: ScheduleWeekPayload, init_data: str | None = None,
                          x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
                          x_school_admin_session: str | None = Header(default=None)):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return new_week(activity_store("school", x_school_admin_session, actor), experiment_id, payload, actor)

    def saved_week_export(store: Database, source: Database, experiment_id: str, revision: int):
        try:
            return export_saved_week(store, experiment_id, expected_revision=revision, settings=get_settings(), directory_database=source)
        except ExperimentConflict as error:
            raise HTTPException(status_code=409, detail=jsonable_encoder({"message": str(error), "current": error.current})) from error
        except ExperimentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except GoogleLiveError as error:
            raise HTTPException(status_code=502, detail="Google Sheets временно недоступен. Повторный экспорт безопасен.") from error

    @router.post("/admin/schedule/experiments/{experiment_id}/export")
    def admin_schedule_export(experiment_id: str, payload: ScheduleExportPayload, init_data: str | None = None,
                              x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return saved_week_export(database, database, experiment_id, payload.expected_revision)

    @router.post("/admin/schedule/experiments/school/{experiment_id}/export")
    def admin_school_export(experiment_id: str, payload: ScheduleExportPayload, init_data: str | None = None,
                            x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
                            x_school_admin_session: str | None = Header(default=None)):
        principal = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        require_real_school_admin(x_school_admin_session, principal)
        source, store = school_stores()
        return saved_week_export(store, source, experiment_id, payload.expected_revision)

    @router.get("/admin/schedule/experiments/school")
    def admin_school_experiments(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_school_admin_session: str | None = Header(default=None)):
        principal = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        require_real_school_admin(x_school_admin_session, principal)
        _, store = school_stores()
        return {"items": list_experiments(store)}

    @router.get("/admin/schedule/experiments/school/directory")
    def admin_school_directory(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_school_admin_session: str | None = Header(default=None)):
        principal = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        require_real_school_admin(x_school_admin_session, principal)
        source = school_source_database()
        return school_directory(source)

    @router.post("/admin/schedule/experiments/school/effective-week/{effective_week_id}")
    def admin_school_edit_effective_week(effective_week_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_school_admin_session: str | None = Header(default=None)):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        require_real_school_admin(x_school_admin_session, actor)
        source, store = school_stores()
        if source is not store:
            raise HTTPException(status_code=422, detail="Historical editing requires the school workspace database")
        try:
            return create_effective_week_experiment(store, effective_week_id, actor_user_id=actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/admin/schedule/experiments/school")
    def admin_school_create_experiment(payload: ScheduleExperimentCreatePayload, init_data: str | None = None,
                                       x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
                                       x_school_admin_session: str | None = Header(default=None)):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        require_real_school_admin(x_school_admin_session, actor)
        _, store = school_stores()
        content = dict(payload.payload or {})
        content["draft_kind"] = "school"
        try:
            return create_experiment(store, name=payload.name, source=payload.source,
                                     payload=content, actor_user_id=actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/admin/schedule/experiments/school/import")
    def admin_school_import(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_school_admin_session: str | None = Header(default=None)):
        principal = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        require_real_school_admin(x_school_admin_session, principal)
        source, store = school_stores()
        try:
            if local_school_workspace_enabled():
                return import_school_template(source, store)
            return open_active_school_template(store)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/admin/schedule/experiments/school/audience-preview")
    def admin_school_audience_preview(payload: ScheduleExperimentAudiencePreviewPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_school_admin_session: str | None = Header(default=None)):
        import logging
        from time import perf_counter
        request_started = perf_counter()
        try:
            role_started = perf_counter()
            principal = authenticate(init_data, x_dev_auth, x_telegram_init_data)
            require_role(principal, "admin")
            role_ms = (perf_counter() - role_started) * 1000
            school_auth_started = perf_counter()
            require_real_school_admin(x_school_admin_session, principal)
            if os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1":
                logging.getLogger(__name__).info(
                    "school audience preview auth role_ms=%.1f school_admin_ms=%.1f",
                    role_ms, (perf_counter() - school_auth_started) * 1000,
                )
        except PostgresOperationalError as error:
            raise HTTPException(status_code=503, detail="Школьная авторизация временно недоступна") from error
        try:
            source = school_source_database()
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        try:
            preview_started = perf_counter()
            raw_date = payload.payload.get("reference_date") or payload.payload.get("week_start")
            reference_date = date.fromisoformat(str(raw_date)[:10]) if raw_date else date.today()
            snapshot = cached_audience_snapshot(reference_date, source)
            result = experiment_audience_preview(source, payload.payload, snapshot=snapshot)
            if os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1":
                logging.getLogger(__name__).info(
                    "school audience preview complete cached_snapshot=%s preview_ms=%.1f request_ms=%.1f lessons=%d",
                    bool(snapshot),
                    (perf_counter() - preview_started) * 1000,
                    (perf_counter() - request_started) * 1000,
                    len(payload.payload.get("lessons") or []),
                )
            return result
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except PostgresOperationalError as error:
            raise HTTPException(status_code=503, detail="Школьный справочник временно недоступен для расчёта аудитории") from error

    @router.get("/admin/schedule/experiments/school/{experiment_id}")
    def admin_school_get(experiment_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_school_admin_session: str | None = Header(default=None)):
        principal = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        require_real_school_admin(x_school_admin_session, principal)
        _, store = school_stores()
        try:
            return get_experiment(store, experiment_id)
        except ExperimentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/admin/schedule/experiments/school/{experiment_id}/duplicate")
    def admin_school_duplicate(experiment_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_school_admin_session: str | None = Header(default=None)):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        require_real_school_admin(x_school_admin_session, actor)
        _, store = school_stores()
        try:
            return duplicate_experiment(store, experiment_id, actor_user_id=actor["id"])
        except ExperimentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.put("/admin/schedule/experiments/school/{experiment_id}")
    def admin_school_save(experiment_id: str, payload: ScheduleExperimentSavePayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_school_admin_session: str | None = Header(default=None)):
        try:
            actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
            require_real_school_admin(x_school_admin_session, actor)
            source, store = school_stores()
            return save_experiment(store, experiment_id, expected_revision=payload.expected_revision,
                                   payload=payload.payload, name=payload.name, actor_user_id=actor["id"],
                                   audience_database=source)
        except ExperimentConflict as error:
            raise HTTPException(status_code=409, detail=jsonable_encoder({"message": str(error), "current": error.current})) from error
        except ExperimentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except PostgresOperationalError as error:
            raise HTTPException(status_code=503, detail="Школьный справочник временно недоступен; локальный черновик не записан") from error
        except sqlite3.OperationalError as error:
            raise HTTPException(status_code=503, detail=f"Локальная SQLite черновиков недоступна: {error}") from error
        except sqlite3.DatabaseError as error:
            raise HTTPException(status_code=500, detail="Ошибка локальной SQLite при записи черновика") from error

    @router.delete("/admin/schedule/experiments/school/{experiment_id}")
    def admin_school_delete(experiment_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"), x_school_admin_session: str | None = Header(default=None)):
        principal = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        require_real_school_admin(x_school_admin_session, principal)
        _, store = school_stores()
        try:
            delete_experiment(store, experiment_id)
            return {"deleted": experiment_id}
        except ExperimentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/admin/schedule/experiments/audience-preview")
    def admin_schedule_experiment_audience_preview(payload: ScheduleExperimentAudiencePreviewPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return experiment_audience_preview(database, payload.payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/admin/schedule/experiments")
    def admin_schedule_create_experiment(payload: ScheduleExperimentCreatePayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return create_experiment(database, name=payload.name, source=payload.source, payload=payload.payload, actor_user_id=actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/admin/schedule/experiments/{experiment_id}")
    def admin_schedule_get_experiment(experiment_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return get_experiment(database, experiment_id)
        except ExperimentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/admin/schedule/experiments/{experiment_id}/duplicate")
    def admin_schedule_duplicate_experiment(experiment_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return duplicate_experiment(database, experiment_id, actor_user_id=actor["id"])
        except ExperimentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.delete("/admin/schedule/experiments/{experiment_id}")
    def admin_schedule_delete_experiment(experiment_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            delete_experiment(database, experiment_id)
            return {"deleted": experiment_id}
        except ExperimentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.put("/admin/schedule/experiments/{experiment_id}")
    def admin_schedule_save_experiment(experiment_id: str, payload: ScheduleExperimentSavePayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        actor = require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return save_experiment(database, experiment_id, expected_revision=payload.expected_revision, payload=payload.payload, name=payload.name, actor_user_id=actor["id"])
        except ExperimentConflict as error:
            raise HTTPException(status_code=409, detail=jsonable_encoder({"message": str(error), "current": error.current})) from error
        except ExperimentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.put("/admin/schedule/draft")
    def admin_schedule_save_draft(payload: ScheduleDraftPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        raise HTTPException(status_code=410, detail="Legacy ScheduleAdmin writes are retired; use the constructor publication workflow")

    @router.delete("/admin/schedule/draft")
    def admin_schedule_discard_draft(scope_kind: str, scope_key: str, expected_revision: int, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        raise HTTPException(status_code=410, detail="Legacy ScheduleAdmin writes are retired; use the constructor publication workflow")

    @router.post("/admin/schedule/draft/publish")
    def admin_schedule_publish_draft(payload: SchedulePublishPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        raise HTTPException(status_code=410, detail="Legacy ScheduleAdmin writes are retired; use the constructor publication workflow")

    @router.post("/admin/schedule/audience-preview")
    def admin_schedule_audience_preview(payload: ScheduleAudiencePreviewPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        try:
            return audience_preview(database, payload.audience, payload.occupied_audiences)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/admin/schedule/import-to-draft")
    def admin_schedule_import_to_draft(payload: ScheduleImportDraftPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        raise HTTPException(status_code=410, detail="Legacy ScheduleAdmin writes are retired; use the constructor publication workflow")

    @router.get("/admin/schedule/lessons")
    def admin_schedule_lessons(week_start: str | None = None, status: str | None = None, teacher_id: str | None = None, group_id: str | None = None, lesson_type: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        result = effective_blocks(database, week_start) if canonical_admin_backend_enabled() and week_start else {"status": "canonical_not_materialized", "items": []}
        return {"items": result.get("items", []), "status": result.get("status")}

    @router.get("/admin/schedule/issues")
    def admin_schedule_issues(week_start: str | None = None, status: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        result = effective_blocks(database, week_start) if canonical_admin_backend_enabled() and week_start else {"status": "canonical_not_materialized", "items": []}
        return {"items": [{"block_key": item.get("block_key"), "unresolved": item.get("unresolved", []), "status": item.get("status"), "change_kind": item.get("change_kind")} for item in result.get("items", []) if item.get("unresolved")], "status": result.get("status")}

    @router.get("/admin/schedule/reconciliation")
    def admin_schedule_reconciliation(week_start: str | None = None, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        return {"status": "canonical_pending_reconciliation",
                "message": "Use the canonical v1 reconciliation queue; legacy mapping decisions are retired."}

    @router.get("/admin/schedule/allocation-qa")
    def admin_schedule_allocation_qa(week_start: str, grade: str | None = None, student_id: str | None = None,
                                     teacher_id: str | None = None, init_data: str | None = None,
                                     x_dev_auth: str | None = Header(default=None),
                                     x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        require_role(authenticate(init_data, x_dev_auth, x_telegram_init_data), "admin")
        raise HTTPException(status_code=409, detail="Allocation QA is unavailable until the canonical effective schedule is enabled")

    @router.post("/admin/schedule/mappings")
    def admin_schedule_save_mapping(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        raise HTTPException(status_code=409, detail="Legacy schedule mappings are retired; resolve canonical v1 through the reconciliation queue")

    @router.delete("/admin/schedule/mappings/{mapping_id}")
    def admin_schedule_retire_mapping(mapping_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        raise HTTPException(status_code=409, detail="Legacy schedule mappings are retired")

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
        actor = require_role(telegram_user, "admin")
        try:
            identity = create_identity(database, payload.display_name, payload.class_name, payload.kind, actor["id"], payload.subjects, payload.comment, payload.status)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        identity_data = dict(identity)
        return {"id": identity_data["id"], "kind": identity_data["kind"], "display_name": identity_data["display_name"], "class_name": identity_data["class_name"], "subjects": database.list_teacher_subjects(identity_data["id"]) if identity_data["kind"] == "teacher" else [], "comment": identity_data.get("admin_note")}

    @router.post("/admin/groups")
    def create_student_group(payload: GroupPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            group = create_group(database, payload.name, payload.group_type, display_name=payload.display_name, subject=payload.subject, base_class_name=payload.base_class_name, subject_subgroup=payload.subject_subgroup, exam_track=payload.exam_track, default_teacher_identity_id=payload.default_teacher_identity_id, comment=payload.comment, actor_user_id=actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        group_data = dict(group)
        return {**group_data, "comment": group_data.get("admin_note"), "active": bool(group_data.get("canonical"))}

    @router.patch("/admin/groups/{group_id}")
    def update_admin_group(group_id: str, payload: dict, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            allowed = {"name", "display_name", "group_type", "subject", "base_class_name", "subject_subgroup", "exam_track", "default_teacher_identity_id", "comment"}
            if set(payload) - allowed:
                raise ValueError("Unsupported group fields were supplied")
            group = database.update_admin_group(group_id, payload, actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not group:
            raise HTTPException(status_code=404, detail="Group was not found")
        group_data = dict(group)
        return {**group_data, "comment": group_data.get("admin_note"), "active": bool(group_data.get("canonical"))}

    @router.post("/admin/memberships/transfer")
    def transfer_student_membership(payload: MembershipTransferPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            result = database.update_student_memberships(payload.identity_id, payload.from_group_id, payload.to_group_id, actor["id"], payload.reason)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result

    @router.post("/admin/memberships")
    def create_student_membership(payload: MembershipPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            membership = add_membership(database, payload.group_id, payload.identity_id, "admin_override", payload.member_role, payload.source_ref or "admin_editor", actor["id"])
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"id": membership["id"], "group_id": membership["group_id"], "identity_id": membership["identity_id"], "member_role": membership["member_role"], "active": membership["active"]}

    @router.post("/admin/membership-overrides")
    def create_membership_override(payload: MembershipOverridePayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        if payload.member_role != "student" or payload.action not in {"include", "exclude"} or not payload.reason.strip():
            raise HTTPException(status_code=400, detail="A student membership action and reason are required")
        try:
            result = database.update_student_memberships(payload.identity_id, None if payload.action == "include" else payload.group_id, payload.group_id if payload.action == "include" else None, actor["id"], payload.reason)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"identity_id": result["identity_id"], "group_id": payload.group_id, "action": payload.action, "active": True}

    @router.post("/admin/teacher-assignments")
    def create_teacher_assignment(payload: TeacherAssignmentPayload, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        actor = require_role(telegram_user, "admin")
        try:
            if payload.grade_level and not database.teacher_grade_assignment_schema_ready():
                raise HTTPException(status_code=409, detail="Grade-wide teacher assignments require migration 036")
            if payload.assignment_id:
                if payload.active and payload.group_id and not payload.grade_level:
                    dimensions = {name: getattr(payload, name) for name in ("base_class_name", "subject_subgroup", "classroom_course_id", "exam_track") if name in payload.model_fields_set}
                    item = database.replace_teacher_assignment(payload.assignment_id, payload.teacher_identity_id,
                        payload.group_id, payload.subject, actor["id"], **dimensions)
                elif payload.active and payload.grade_level and not payload.group_id:
                    dimensions = {name: getattr(payload, name) for name in ("exam_track", "classroom_course_id") if name in payload.model_fields_set}
                    item = database.replace_teacher_grade_assignment(payload.assignment_id, payload.teacher_identity_id,
                        payload.grade_level, payload.subject, actor["id"], **dimensions)
                elif not payload.active and not payload.group_id and not payload.grade_level:
                    item = database.deactivate_teacher_assignment(payload.assignment_id, payload.teacher_identity_id, actor["id"])
                else:
                    raise ValueError("Assignment update requires assignment_id with either a canonical group or active=false")
                if not item:
                    raise HTTPException(status_code=404, detail="Teacher assignment was not found")
                return {"id": str(item["id"]), "active": bool(item["active"])}
            if bool(payload.group_id) == bool(payload.grade_level):
                raise ValueError("Provide exactly one of group_id or grade_level")
            if payload.grade_level:
                dimensions = {name: getattr(payload, name) for name in ("exam_track", "classroom_course_id") if name in payload.model_fields_set}
                items = database.set_teacher_grade_assignments(payload.teacher_identity_id, payload.grade_level, payload.subject, payload.active, actor["id"], **dimensions)
                return {"items": [{"id": item["id"], "group_id": None, "grade_level": item["grade_level"], "active": item["active"]} for item in items]}
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
    def create_schedule_scope(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        require_role(telegram_user, "admin")
        raise HTTPException(status_code=409, detail="Legacy schedule scopes are retired; canonical audiences are approved with the canonical template")

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
        identity_id = payload.target_user_id.removeprefix("identity:") if payload.target_user_id.startswith("identity:") else None
        target_user = None if identity_id else database.get_user_by_id(payload.target_user_id)
        target = database.get_student_identity(identity_id or (target_user["identity_id"] if target_user else ""))
        if not target or not target["identity_id"] or target["kind"] != "student":
            raise HTTPException(status_code=400, detail="Only a student identity can be previewed")
        created_at = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        target_user_id = target_user["id"] if target_user else reviewer["id"]
        preview = database.create_student_preview(reviewer["id"], target_user_id, created_at, expires_at, target_identity_id=target["identity_id"])
        profile = database.get_student_profile(target["id"])
        return {"id": preview["id"], "expires_at": preview["expires_at"], "target": {"id": target["id"], "display_name": profile["user"]["display_name"] if profile else target["display_name"], "class_name": profile["user"]["class_name"] if profile else target["class_name"]}}

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
        if not canonical_backend_enabled():
            return {
                "state": "canonical_backend_disabled",
                "schedule": [],
                "groups": database.list_teacher_groups(user["id"]),
                "information": [dict(row) for row in database.list_active_announcements(user["id"], "teacher")],
            }
        schedule_projection = project_teacher_range(database, user["identity_id"], requested_day)
        return {
            "state": schedule_projection.get("status"),
            "schedule": schedule_projection.get("items", []),
            "schedule_issues": schedule_projection.get("issues", []),
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

    @router.get("/teacher/journals")
    def teacher_journals(init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = require_role(telegram_user, "teacher")
        items = database.teacher_journal_sources(user["id"]) if user["identity_id"] else []
        return {"state": "approved", "items": items}

    @router.get("/teacher/groups/{group_id}")
    def teacher_group(group_id: str, init_data: str | None = None, x_dev_auth: str | None = Header(default=None), x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
        telegram_user = authenticate(init_data, x_dev_auth, x_telegram_init_data)
        user = require_role(telegram_user, "teacher")
        if not database.teacher_can_access_group(user["id"], group_id):
            raise HTTPException(status_code=403, detail="Teacher is not assigned to this group")
        journal = journal_view(database.list_group_journal(group_id, teacher_user_id=user['id']))
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
        detail = database.teacher_journal_detail(user["id"], source_id, marker)
        if not detail:
            raise HTTPException(status_code=403, detail="Teacher is not assigned to this journal subgroup")
        return detail

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
        if not canonical_backend_enabled():
            return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": "canonical_backend_disabled", "items": [], "issues": []}
        projection = project_teacher_range(database, user["identity_id"], start_day, end_day)
        return {"mode": "dev" if telegram_user.get("dev") else "telegram", "state": projection.get("status"), "items": projection.get("items", []), "issues": projection.get("issues", [])}

    return router
