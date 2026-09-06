from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, quote
from urllib.request import Request, urlopen

from backend.services.google_contract import GoogleIntegrationConfig


class GoogleLiveError(RuntimeError):
    pass


class GoogleApiError(GoogleLiveError):
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Google API request failed ({status})")


def _decode_json(response: Any) -> dict[str, Any]:
    try:
        payload = json.loads(response.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GoogleLiveError("Google returned a non-JSON response") from error
    if not isinstance(payload, dict):
        raise GoogleLiveError("Google returned an unexpected response")
    return payload


def _request_json(request: Request) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=30) as response:
            return _decode_json(response)
    except HTTPError as error:
        try:
            raw_body = error.read().decode("utf-8")
            payload = json.loads(raw_body)
            body = payload if isinstance(payload, dict) else raw_body
            reason = payload.get("error", "unknown_error") if isinstance(payload, dict) else "unknown_error"
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = "non_json_error_body"
            reason = "http_error"
        raise GoogleApiError(error.code, body) from error
    except URLError as error:
        raise GoogleLiveError("Google request could not be reached") from error


class GoogleTokenStore:
    """Local-only token store. The ignored data directory is never part of the repository."""

    def __init__(self, path: str | Path = "data/google-oauth-token.json") -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            refresh_token_value = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")
            if refresh_token_value:
                return {"refresh_token": refresh_token_value, "scope": os.getenv("GOOGLE_OAUTH_GRANTED_SCOPES", "")}
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GoogleLiveError("Stored Google token file is unreadable") from error
        if not isinstance(payload, dict) or not payload.get("refresh_token") and not payload.get("access_token"):
            raise GoogleLiveError("Stored Google token file has an invalid shape")
        return payload

    def save(self, token: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(token, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)

    def has_refresh_token(self) -> bool:
        token = self.load()
        return bool(token and token.get("refresh_token"))


def exchange_code(config: GoogleIntegrationConfig, code: str) -> dict[str, Any]:
    if not config.client_id or not config.client_secret or not config.redirect_uri:
        raise GoogleLiveError("Google OAuth client configuration is incomplete")
    request = Request(
        "https://oauth2.googleapis.com/token",
        data=urlencode({
            "code": code,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
            "grant_type": "authorization_code",
        }).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    token = _request_json(request)
    if not token.get("access_token"):
        raise GoogleLiveError("Google token response did not contain an access token")
    token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600))
    return token


def refresh_token(config: GoogleIntegrationConfig, token: dict[str, Any]) -> dict[str, Any]:
    if not config.client_id or not config.client_secret or not token.get("refresh_token"):
        raise GoogleLiveError("Google refresh token or client configuration is missing")
    request = Request(
        "https://oauth2.googleapis.com/token",
        data=urlencode({
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "refresh_token": token["refresh_token"],
            "grant_type": "refresh_token",
        }).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    refreshed = _request_json(request)
    if not refreshed.get("access_token"):
        raise GoogleLiveError("Google refresh response did not contain an access token")
    refreshed.setdefault("refresh_token", token["refresh_token"])
    refreshed.setdefault("scope", token.get("scope", ""))
    refreshed["expires_at"] = int(time.time()) + int(refreshed.get("expires_in", 3600))
    return refreshed


class GoogleLiveClient:
    def __init__(self, config: GoogleIntegrationConfig, token: dict[str, Any], store: GoogleTokenStore) -> None:
        self.config = config
        self.token = token
        self.store = store

    def _access_token(self) -> str:
        if int(self.token.get("expires_at", 0)) <= int(time.time()) + 60:
            self.token = refresh_token(self.config, self.token)
            self.store.save(self.token)
        access_token = self.token.get("access_token")
        if not access_token:
            raise GoogleLiveError("Google access token is missing")
        return str(access_token)

    def get(self, base_url: str, path: str = "", params: dict[str, str | list[str]] | None = None) -> dict[str, Any]:
        url = urljoin(base_url, path)
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        return _request_json(Request(url, headers={"Authorization": f"Bearer {self._access_token()}"}))

    def userinfo(self) -> dict[str, Any]:
        return self.get("https://openidconnect.googleapis.com/", "v1/userinfo")

    def spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        return self.get(
            "https://sheets.googleapis.com/",
            f"v4/spreadsheets/{quote(spreadsheet_id, safe='')}",
            {"fields": "spreadsheetId,properties.title,sheets(properties,merges)"},
        )

    def sheet_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        values = self.sheet_values_many(spreadsheet_id, [range_name])
        return values[0] if values else []

    def sheet_values_many(self, spreadsheet_id: str, range_names: list[str]) -> list[list[list[str]]]:
        payload = self.get(
            "https://sheets.googleapis.com/",
            f"v4/spreadsheets/{quote(spreadsheet_id, safe='')}/values:batchGet",
            {"majorDimension": "ROWS", "ranges": range_names},
        )
        value_ranges = payload.get("valueRanges") or []
        result: list[list[list[str]]] = []
        for value_range in value_ranges:
            values = value_range.get("values") or []
            result.append(values if isinstance(values, list) else [])
        return result

    def classroom_courses_for_teacher(self) -> list[dict[str, Any]]:
        courses: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params = {"pageSize": "100", "teacherId": "me", "courseStates": "ACTIVE"}
            if page_token:
                params["pageToken"] = page_token
            payload = self.get("https://classroom.googleapis.com/", "v1/courses", params)
            items = payload.get("courses") or []
            courses.extend(item for item in items if isinstance(item, dict))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return courses

    def coursework(self, course_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {"pageSize": "100"}
            if page_token:
                params["pageToken"] = page_token
            payload = self.get("https://classroom.googleapis.com/", f"v1/courses/{quote(course_id, safe='')}/courseWork", params)
            items.extend(item for item in (payload.get("courseWork") or []) if isinstance(item, dict))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return items

    def student_submissions(self, course_id: str, coursework_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {"pageSize": "100", "userId": "all"}
            if page_token:
                params["pageToken"] = page_token
            payload = self.get("https://classroom.googleapis.com/", f"v1/courses/{quote(course_id, safe='')}/courseWork/{quote(coursework_id, safe='')}/studentSubmissions", params)
            items.extend(item for item in (payload.get("studentSubmissions") or []) if isinstance(item, dict))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return items

    def coursework_materials(self, course_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {"pageSize": "100"}
            if page_token:
                params["pageToken"] = page_token
            payload = self.get("https://classroom.googleapis.com/", f"v1/courses/{quote(course_id, safe='')}/courseWorkMaterials", params)
            items.extend(item for item in (payload.get("courseWorkMaterial") or []) if isinstance(item, dict))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return items
