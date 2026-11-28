from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import time
from urllib.parse import parse_qsl
from urllib.parse import urlsplit

from backend.config import Settings


class AuthError(ValueError):
    pass


def is_loopback_dev_auth_request(host: str | None, client_host: str | None, *, local_test_mode: bool = False) -> bool:
    """Dev role headers are valid only on a direct localhost request.

    Do not trust forwarded-for headers: a reverse proxy can accept attacker
    traffic while supplying a loopback value there.
    """
    if not local_test_mode:
        return False
    hostname = (urlsplit(f"//{host}").hostname or "").casefold()
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        return False
    try:
        return ipaddress.ip_address(client_host or "").is_loopback
    except ValueError:
        return False


def verify_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict:
    if not init_data or not bot_token:
        raise AuthError("Telegram initData is required")
    if len(init_data) > 16384:
        raise AuthError("Telegram initData is invalid")
    fields = parse_qsl(init_data, keep_blank_values=True)
    values = dict(fields)
    if len(values) != len(fields):
        raise AuthError("Telegram initData has duplicate fields")
    received_hash = values.pop("hash", "")
    if not re.fullmatch(r"[0-9a-f]{64}", received_hash):
        raise AuthError("Telegram initData hash is invalid")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise AuthError("Telegram initData signature is invalid")
    try:
        auth_date = int(values.get("auth_date", "0"))
    except ValueError as error:
        raise AuthError("Telegram auth date is invalid") from error
    age = time.time() - auth_date
    if auth_date <= 0 or age < -30 or age > max_age_seconds:
        raise AuthError("Telegram initData is expired")
    try:
        user = json.loads(values.get("user", "{}"))
    except json.JSONDecodeError as error:
        raise AuthError("Telegram user payload is invalid") from error
    if not isinstance(user, dict) or type(user.get("id")) is not int or not 0 < user["id"] < 2**52:
        raise AuthError("Telegram user id is invalid")
    return {**user, "_auth_date": auth_date}


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


def live_telegram_subject(database, principal: dict) -> dict | None:
    user = database.get_access_subject(telegram_id=principal["id"])
    if user and principal.get("_auth_date") is not None and user["telegram_auth_after"] is not None:
        if principal["_auth_date"] <= user["telegram_auth_after"]:
            return {**user, "roles": [], "identity_id": None, "role": None, "stale_telegram": True}
    return user
