from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


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

    @classmethod
    def from_env(cls) -> "Settings":
        origins = tuple(item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if item.strip())
        return cls(
            app_env=os.getenv("APP_ENV", "development").lower(),
            dev_auth_enabled=os.getenv("DEV_AUTH_ENABLED", "false").lower() == "true",
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            database_url=os.getenv("DATABASE_URL") or None,
            database_path=os.getenv("DATABASE_PATH", "data/my-island.db"),
            cors_origins=origins,
            backend_public_url=os.getenv("BACKEND_PUBLIC_URL") or None,
            frontend_public_url=os.getenv("FRONTEND_PUBLIC_URL") or None,
            google_sheets_spreadsheet_id=os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID") or None,
            google_classroom_course_id=os.getenv("GOOGLE_CLASSROOM_COURSE_ID") or None,
            google_oauth_client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID") or None,
            google_oauth_client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or None,
            google_oauth_redirect_uri=os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or None,
            google_oauth_refresh_token=os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN") or None,
        )

    def validate(self) -> None:
        if self.app_env == "production" and self.dev_auth_enabled:
            raise ValueError("DEV_AUTH_ENABLED must be false in production")
        if self.app_env == "production" and not self.database_url:
            raise ValueError("DATABASE_URL is required in production")


def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.validate()
    return settings
