from __future__ import annotations

import argparse
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from backend.config import get_settings
from backend.services.google_contract import CLASSROOM_COURSEWORK_SCOPES, CLASSROOM_MATERIALS_READ_SCOPE, GOOGLE_IDENTITY_SCOPES, SHEETS_READ_SCOPE
from backend.services.google_live import GoogleApiError, GoogleLiveClient, GoogleLiveError, GoogleTokenStore, exchange_code
from backend.services.google_oauth import build_google_authorization_url, google_config
from backend.services.google_sheets import looks_like_school_schedule, parse_school_schedule_values


EXPECTED_ACCOUNT = "antischool.island@gmail.com"
REQUIRED_SCOPES = {*GOOGLE_IDENTITY_SCOPES, SHEETS_READ_SCOPE, "https://www.googleapis.com/auth/classroom.courses.readonly", "https://www.googleapis.com/auth/classroom.student-submissions.students.readonly", CLASSROOM_MATERIALS_READ_SCOPE}


class CallbackHandler(BaseHTTPRequestHandler):
    result: dict[str, str] = {}
    expected_state = ""

    def do_GET(self) -> None:  # noqa: N802
        query = parse_qs(urlparse(self.path).query)
        if query.get("state", [""])[0] != self.expected_state:
            CallbackHandler.result = {"error": "oauth_state_mismatch"}
        elif query.get("error"):
            CallbackHandler.result = {"error": query["error"][0]}
        elif query.get("code", [""])[0]:
            CallbackHandler.result = {"code": query["code"][0]}
        else:
            CallbackHandler.result = {"error": "oauth_callback_missing_code"}
        body = b"Google OAuth callback received. You can return to the terminal."
        self.send_response(200 if "code" in CallbackHandler.result else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _callback_server(redirect_uri: str, state: str) -> HTTPServer:
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise GoogleLiveError("Local smoke test requires an http://localhost redirect URI")
    port = parsed.port or 80
    CallbackHandler.result = {}
    CallbackHandler.expected_state = state
    return HTTPServer((parsed.hostname, port), CallbackHandler)


def _authorize(config: object) -> dict[str, object]:
    state = secrets.token_urlsafe(32)
    redirect_uri = getattr(config, "redirect_uri", None)
    if not redirect_uri:
        raise GoogleLiveError("GOOGLE_OAUTH_REDIRECT_URI is required")
    server = _callback_server(redirect_uri, state)
    url = build_google_authorization_url(get_settings(), state, prompt="consent")
    print("Open this URL in a browser and authorize antischool.island@gmail.com:", flush=True)
    print(url, flush=True)
    while not CallbackHandler.result:
        server.handle_request()
    print("OAuth callback received; exchanging code server-side.", flush=True)
    server.server_close()
    result = CallbackHandler.result
    if "error" in result:
        raise GoogleLiveError(result["error"])
    return exchange_code(config, result["code"])


def run(reauthorize: bool) -> int:
    settings = get_settings()
    config = google_config(settings)
    if not config.oauth_ready or not config.spreadsheet_id:
        raise GoogleLiveError("Google OAuth client and GOOGLE_SHEETS_SPREADSHEET_ID must be configured")
    store = GoogleTokenStore()
    token = None if reauthorize else store.load()
    if token is None:
        token = _authorize(config)
        # Persist the complete token response immediately; access tokens are short-lived,
        # while the refresh token is the durable credential for both Google APIs.
        store.save(token)
        token_source = "new authorization code"
    else:
        token_source = "stored refresh token"
        if not token.get("refresh_token"):
            raise GoogleLiveError("Stored token has no refresh token; use --reauthorize once")
    client = GoogleLiveClient(config, token, store)
    print("OAuth token received; verifying account.", flush=True)
    account = client.userinfo()
    email = str(account.get("email", ""))
    if email.lower() != EXPECTED_ACCOUNT:
        raise GoogleLiveError(f"Authorized account mismatch: {email or 'unknown account'}")
    granted_scopes = set(str(client.token.get("scope", "")).split())
    scope_aliases = {"email": {"email", "https://www.googleapis.com/auth/userinfo.email"}}
    missing_scopes = sorted(scope for scope in REQUIRED_SCOPES if not (scope in granted_scopes or scope in scope_aliases and granted_scopes.intersection(scope_aliases[scope])))
    coursework_scopes_granted = sorted(granted_scopes.intersection(CLASSROOM_COURSEWORK_SCOPES))
    store.save(client.token)

    print("Reading Google Sheet.", flush=True)
    spreadsheet = client.spreadsheet(config.spreadsheet_id)
    sheets = spreadsheet.get("sheets") or []
    sheet_names = [sheet.get("properties", {}).get("title") for sheet in sheets]
    sheet_names = [name for name in sheet_names if isinstance(name, str) and name]
    if not sheet_names:
        raise GoogleLiveError("Spreadsheet has no readable sheet tabs")
    values_by_sheet = client.sheet_values_many(config.spreadsheet_id, [f"{name}!A:Z" for name in sheet_names])
    values = values_by_sheet[0] if values_by_sheet else []
    sheet_results: list[dict[str, object]] = []
    parsed_rows = 0
    parse_error: str | None = None
    selected_sheets: list[str] = []
    selected_raw_rows = 0
    for sheet, name, sheet_values in zip(sheets, sheet_names, values_by_sheet):
        properties = sheet.get("properties") or {}
        if properties.get("hidden"):
            sheet_results.append({"sheet": name, "raw_rows": len(sheet_values), "parser": "skipped_hidden"})
            continue
        if not looks_like_school_schedule(sheet_values):
            sheet_results.append({"sheet": name, "raw_rows": len(sheet_values), "parser": "not_schedule_matrix"})
            continue
        try:
            parsed = parse_school_schedule_values(sheet_values, sheet_title=name, spreadsheet_title=str(spreadsheet.get("properties", {}).get("title", "")))
            parsed_count = len(parsed)
            sheet_results.append({"sheet": name, "raw_rows": len(sheet_values), "parser": "ok", "parsed_rows": parsed_count})
            selected_sheets.append(name)
            selected_raw_rows += len(sheet_values)
            parsed_rows += parsed_count
            if len(selected_sheets) == 1:
                values = sheet_values
        except ValueError as error:
            sheet_results.append({"sheet": name, "raw_rows": len(sheet_values), "parser": "rejected", "error": str(error)})
            if parse_error is None:
                parse_error = str(error)
    sheet_parse = "ok" if selected_sheets else "rejected"

    print("Reading Classroom teacher courses.", flush=True)
    courses = client.classroom_courses_for_teacher()
    selected = None
    if settings.google_classroom_course_id:
        selected = next((course for course in courses if course.get("id") == settings.google_classroom_course_id), None)
        if selected is None:
            raise GoogleLiveError("Configured GOOGLE_CLASSROOM_COURSE_ID is not in the teacher course list")
    elif len(courses) == 1:
        selected = courses[0]
    else:
        raise GoogleLiveError(f"Expected one teacher course, found {len(courses)}")

    course_id = str(selected["id"])
    print("Reading Classroom coursework, materials and student submissions.", flush=True)
    endpoint_checks: dict[str, object] = {}
    try:
        coursework = client.coursework(course_id)
        endpoint_checks["coursework"] = {"status": "ok", "items": len(coursework)}
    except GoogleApiError as error:
        coursework = []
        endpoint_checks["coursework"] = {"status": "error", "http_status": error.status, "body": error.body}
    try:
        materials = client.coursework_materials(course_id)
        endpoint_checks["courseWorkMaterials"] = {"status": "ok", "items": len(materials)}
    except GoogleApiError as error:
        materials = []
        endpoint_checks["courseWorkMaterials"] = {"status": "error", "http_status": error.status, "body": error.body}
    try:
        submissions = [submission for work in coursework for submission in client.student_submissions(course_id, str(work.get("id", "")))]
        endpoint_checks["studentSubmissions"] = {"status": "ok", "items": len(submissions)}
    except GoogleApiError as error:
        submissions = []
        endpoint_checks["studentSubmissions"] = {"status": "error", "http_status": error.status, "body": error.body}
    material_count = sum(len(work.get("materials") or []) for work in coursework)
    report = {
        "oauth": {"status": "ok", "account": email, "token_source": token_source, "token_store": "data/google-oauth-token.json"},
        "scopes": {"requested_and_verified": sorted(REQUIRED_SCOPES), "coursework_scope_accepted": sorted(CLASSROOM_COURSEWORK_SCOPES), "coursework_scopes_granted": coursework_scopes_granted, "materials_scope_required_for_endpoint": CLASSROOM_MATERIALS_READ_SCOPE, "granted": sorted(granted_scopes), "missing_required": sorted(set(missing_scopes))},
        "sheets": {"status": "read_ok", "spreadsheet_id": config.spreadsheet_id, "title": spreadsheet.get("properties", {}).get("title"), "selected_schedule_sheets": selected_sheets, "raw_rows_selected": selected_raw_rows, "parsed_rows": parsed_rows, "parser": sheet_parse, "parser_error": parse_error, "tabs_checked": sheet_results},
        "classroom": {"status": "read_ok" if not any(isinstance(value, dict) and value.get("status") == "error" for value in endpoint_checks.values()) else "partial", "teacher_courses": len(courses), "course_id": course_id, "course_name": selected.get("name"), "role": "teacher", "coursework": len(coursework), "materials_embedded_in_coursework": material_count, "student_submissions": len(submissions), "endpoints": endpoint_checks},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local, read-only Google OAuth/Sheets/Classroom smoke test")
    parser.add_argument("--reauthorize", action="store_true", help="explicitly start one new consent flow; normal runs reuse the ignored local refresh token")
    try:
        raise SystemExit(run(parser.parse_args().reauthorize))
    except (GoogleLiveError, ValueError) as error:
        print(f"Google smoke test blocked: {error}", file=sys.stderr)
        raise SystemExit(1) from error
