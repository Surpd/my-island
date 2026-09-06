from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from backend.config import Settings


class AuthError(ValueError):
    pass


def verify_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict:
    if not init_data or not bot_token:
        raise AuthError("Telegram initData is required")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise AuthError("Telegram initData hash is missing")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise AuthError("Telegram initData signature is invalid")
    auth_date = int(values.get("auth_date", "0"))
    if auth_date <= 0 or time.time() - auth_date > max_age_seconds:
        raise AuthError("Telegram initData is expired")
    try:
        user = json.loads(values.get("user", "{}"))
    except json.JSONDecodeError as error:
        raise AuthError("Telegram user payload is invalid") from error
    if not user.get("id"):
        raise AuthError("Telegram user id is missing")
    return user


def resolve_auth(init_data: str | None, dev_header: str | None) -> dict:
    """Resolve a signed Telegram user or an explicit local-only demo identity."""
    settings = Settings.from_env()
    if settings.app_env != "production" and settings.dev_auth_enabled and dev_header in {"student:1", "teacher:1", "admin:1"}:
        if dev_header == "admin:1":
            return {"id": 900002, "first_name": "Local", "last_name": "Admin", "role": "admin", "dev": True}
        if dev_header == "teacher:1":
            return {"id": 900003, "first_name": "Local", "last_name": "Teacher", "role": "teacher", "dev": True}
        return {"id": 900001, "first_name": "Куренков", "last_name": "Иван", "role": "student", "dev": True}
    if settings.app_env == "production" and not init_data:
        raise AuthError("Production requests must include Telegram initData")
    return verify_telegram_init_data(init_data or "", settings.telegram_bot_token)
