from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


load_dotenv()


def _parse_telegram_user_ids(value: str) -> tuple[int, ...]:
    ids: list[int] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            user_id = int(raw)
        except ValueError as error:
            raise ValueError("TELEGRAM_BOOTSTRAP_USER_IDS must contain comma-separated numeric Telegram user ids") from error
        if user_id <= 0:
            raise ValueError("TELEGRAM_BOOTSTRAP_USER_IDS must contain positive Telegram user ids")
        ids.append(user_id)
    return tuple(dict.fromkeys(ids))


@dataclass(frozen=True)
class Settings:
    app_env: str
    dev_auth_enabled: bool
    telegram_bot_token: str
    database_url: str | None
    database_path: str
    cors_origins: tuple[str, ...]
    backend_public_url: str | None
    frontend_public_url: str | None
    google_sheets_spreadsheet_id: str | None
    google_classroom_course_id: str | None
    google_oauth_client_id: str | None
    google_oauth_client_secret: str | None
    google_oauth_redirect_uri: str | None
    google_oauth_refresh_token: str | None
    telegram_bootstrap_user_ids: tuple[int, ...] = ()
    telegram_webhook_secret: str = ""
    google_oauth_token_encryption_key: str = ""
    google_oauth_allowed_email: str = "antischool.island@gmail.com"
    journal_cron_secret: str = ""
    # Scheduling is fail-closed until canonical is explicitly enabled for a pilot.
    schedule_backend: str = "disabled"
    local_test_mode: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        app_env = os.getenv("APP_ENV", "development").lower()
        origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if item.strip()]
        if app_env != "production":
            origins.extend(("http://localhost:3000", "http://localhost:4173"))
        cors_origins = tuple(dict.fromkeys(origins))
        return cls(
            app_env=app_env,
            dev_auth_enabled=os.getenv("DEV_AUTH_ENABLED", "false").lower() == "true",
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", ""),
            database_url=os.getenv("DATABASE_URL") or None,
            database_path=os.getenv("DATABASE_PATH", "data/my-island.db"),
            cors_origins=cors_origins,
            backend_public_url=os.getenv("BACKEND_PUBLIC_URL") or None,
            frontend_public_url=os.getenv("FRONTEND_PUBLIC_URL") or None,
            google_sheets_spreadsheet_id=os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID") or None,
            google_classroom_course_id=os.getenv("GOOGLE_CLASSROOM_COURSE_ID") or None,
            google_oauth_client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID") or None,
            google_oauth_client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or None,
            google_oauth_redirect_uri=os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or None,
            google_oauth_refresh_token=os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN") or None,
            google_oauth_token_encryption_key=os.getenv("GOOGLE_OAUTH_TOKEN_ENCRYPTION_KEY", ""),
            google_oauth_allowed_email=os.getenv("GOOGLE_OAUTH_ALLOWED_EMAIL", "antischool.island@gmail.com").strip().casefold(),
            journal_cron_secret=os.getenv("JOURNAL_CRON_SECRET", "").strip(),
            telegram_bootstrap_user_ids=_parse_telegram_user_ids(os.getenv("TELEGRAM_BOOTSTRAP_USER_IDS", "")),
            schedule_backend=os.getenv("SCHEDULE_BACKEND", "disabled").strip().casefold() or "disabled",
            local_test_mode=os.getenv("MY_ISLAND_LOCAL_TEST_MODE", "false").strip().lower() == "true",
        )

    def validate(self) -> None:
        if self.app_env == "production" and self.dev_auth_enabled:
            raise ValueError("DEV_AUTH_ENABLED must be false in production")
        if self.app_env == "production" and not self.database_url:
            raise ValueError("DATABASE_URL is required in production")
        if self.app_env == "production" and not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required in production")
        if self.app_env == "production" and os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") == "1":
            parsed = urlparse(self.database_url or "")
            project = "kmrcbewmxeqqxfauiwyy"
            direct = parsed.hostname == f"db.{project}.supabase.co"
            pooler = (parsed.hostname == "aws-1-eu-west-1.pooler.supabase.com"
                      and parsed.username == f"postgres.{project}")
            if parsed.scheme not in {"postgres", "postgresql"} or not (direct or pooler):
                raise ValueError("Consolidated production requires the approved My Island Supabase database")
            if (os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1"
                    or os.getenv("MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE") == "1"):
                raise ValueError("Consolidated production cannot use local/read-only school mode")
        if self.local_test_mode:
            profile = os.getenv("MY_ISLAND_TEST_PROFILE", "manual")
            if profile not in {"manual", "e2e", "schedule", "consolidation"}:
                raise ValueError("Unknown local test profile")
            expected_db = (Path(__file__).resolve().parent.parent / ".local-test" /
                           (profile if profile in {"e2e", "schedule", "consolidation"} else "") / "my-island-test.sqlite3").resolve()
            configured_db = Path(self.database_path).resolve()
            if configured_db != expected_db or self.database_url:
                raise ValueError("Local test mode requires the dedicated .local-test SQLite database")
            if self.app_env != "development" or not self.dev_auth_enabled:
                raise ValueError("Local test mode requires APP_ENV=development and DEV_AUTH_ENABLED=true")
            external_settings = (
                self.telegram_bot_token,
                self.telegram_webhook_secret,
                self.google_sheets_spreadsheet_id,
                self.google_classroom_course_id,
                self.google_oauth_client_id,
                self.google_oauth_client_secret,
                self.google_oauth_redirect_uri,
                self.google_oauth_refresh_token,
                self.google_oauth_token_encryption_key,
            )
            if any(external_settings):
                raise ValueError("Local test mode must not contain Telegram or Google credentials/configuration")
        if os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1":
            if self.app_env != "development" or self.local_test_mode or self.dev_auth_enabled:
                raise ValueError("Local school mode requires development settings with test mode and dev auth disabled")
            if not self.database_url:
                raise ValueError("Local school mode requires the approved Supabase DATABASE_URL")
            parsed = urlparse(self.database_url)
            approved_project = "kmrcbewmxeqqxfauiwyy"
            approved_host = parsed.hostname in {f"db.{approved_project}.supabase.co", "aws-1-eu-west-1.pooler.supabase.com"}
            approved_user = bool(parsed.username and parsed.username.endswith(f".{approved_project}"))
            if not approved_host or not approved_user:
                raise ValueError("Local school mode DATABASE_URL does not match the approved My Island Supabase project")


def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.validate()
    return settings
