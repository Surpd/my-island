from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SendMessage = Callable[[str, int, str], None]


def _send_message(bot_token: str, chat_id: int, text: str) -> None:
    payload = urlencode({"chat_id": str(chat_id), "text": text}).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        response.read()


def _command(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return first.split("@", maxsplit=1)[0].casefold()


def handle_admin_code_update(
    database: Any,
    update: dict[str, Any],
    bot_token: str,
    send_message: SendMessage = _send_message,
) -> bool:
    """Handle the temporary private-chat command used to obtain a browser login code."""
    message = update.get("message") if isinstance(update, dict) else None
    if not isinstance(message, dict) or _command(message.get("text")) != "/admincode":
        return False
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(sender, dict):
        return True
    telegram_user_id = sender.get("id")
    chat_id = chat.get("id")
    if not isinstance(telegram_user_id, int) or not isinstance(chat_id, int):
        return True

    user = database.find_user(telegram_user_id)
    if not user or not database.user_has_role(user["id"], "admin"):
        send_message(bot_token, chat_id, "Доступ разрешён только пользователю с ролью admin.")
        return True

    code, expires_at = database.create_admin_login_challenge(user["id"])
    database.record_audit_event(
        "admin_auth.challenge_created",
        "admin_browser_session",
        {"expires_at": expires_at, "transport": "telegram_bot"},
        user["id"],
    )
    send_message(
        bot_token,
        chat_id,
        f"Код для входа в Admin Console:\n{code}\n\nОдноразовый, действует 5 минут.",
    )
    return True
