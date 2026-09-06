from __future__ import annotations

from urllib.parse import urlencode

from backend.config import Settings
from backend.services.google_contract import CLASSROOM_READ_SCOPES, GOOGLE_IDENTITY_SCOPES, SHEETS_READ_SCOPE, GoogleIntegrationConfig


GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def google_config(settings: Settings) -> GoogleIntegrationConfig:
    return GoogleIntegrationConfig(
        spreadsheet_id=settings.google_sheets_spreadsheet_id,
        classroom_course_id=settings.google_classroom_course_id,
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        redirect_uri=settings.google_oauth_redirect_uri,
    )


def build_google_authorization_url(settings: Settings, state: str, prompt: str | None = None) -> str:
    """Build one consent URL; prompt is only used for explicit first grant/reauthorization."""
    config = google_config(settings)
    if not config.client_id or not config.redirect_uri:
        raise ValueError("Google OAuth client id and redirect URI are required")
    scopes = (*GOOGLE_IDENTITY_SCOPES, SHEETS_READ_SCOPE, *CLASSROOM_READ_SCOPES)
    params = {'client_id': config.client_id, 'redirect_uri': config.redirect_uri, 'response_type': 'code', 'access_type': 'offline', 'scope': ' '.join(scopes), 'state': state}
    if prompt:
        params['prompt'] = prompt
    return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"
