from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from backend.services.account_access import lock_access, record_telegram_contact


SendMessage = Callable[[str, int, str], None]


def _send_message(bot_token: str, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    fields = {"chat_id": str(chat_id), "text": text}
    if reply_markup is not None:
        fields["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    payload = urlencode(fields).encode("utf-8")
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
    if type(telegram_user_id) is not int or type(chat_id) is not int or telegram_user_id <= 0 or telegram_user_id != chat_id:
        return True

    user = database.find_user(telegram_user_id)
    if not user or not database.user_has_role(user["id"], "admin"):
        send_message(bot_token, chat_id, "Доступ разрешён только пользователю с ролью admin.")
        return True

    challenge = database.create_admin_login_challenge(user["id"])
    if not challenge:
        send_message(bot_token, chat_id, "Не удалось выдать код. Проверьте доступ или попробуйте через минуту.")
        return True
    code, expires_at = challenge
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


ROLE_LABELS = {"student": "Ученик", "teacher": "Преподаватель"}
STATUS = "Статус заявки"
CANCEL = "Отменить"
EDIT = "Исправить имя"
EDIT_CLASS = "Исправить класс"
EDIT_ROLE = "Изменить роль"
SUBMIT = "Отправить заявку"
RESTART = "Подать заново"
OTHER_CLASS = "Другой класс"


def _keyboard(*rows: list[str]) -> dict:
    return {"keyboard": [[{"text": item} for item in row] for row in rows],
            "resize_keyboard": True, "one_time_keyboard": True}


def _island_menu(web_app_url: str | None) -> dict:
    url = urlsplit(web_app_url or "")
    if url.scheme == "https" and url.hostname:
        # Telegram attaches signed initData when the Mini App opens from an inline button.
        return {"inline_keyboard": [[{"text": "Открыть Остров", "web_app": {"url": web_app_url}}]]}
    return {"remove_keyboard": True}


def _rejection_message(reason: str | None) -> str:
    message = "Заявку отклонили."
    if reason:
        message += f"\nПричина: {reason}"
    return message + "\nМожно исправить сведения и подать её заново."


def notify_registration_result(
    bot_token: str, telegram_user_id: int, status: str, reason: str | None = None, web_app_url: str | None = None,
) -> bool:
    """Send a decision after the access transaction commits."""
    if not bot_token or status not in {"approved", "rejected"}:
        return False
    try:
        if status == "approved":
            _send_message(bot_token, telegram_user_id, "Заявка подтверждена. Добро пожаловать на Остров!", _island_menu(web_app_url))
        else:
            _send_message(bot_token, telegram_user_id, _rejection_message(reason), _keyboard([RESTART], [STATUS]))
    except Exception:
        # Transport errors may contain the URL with the bot token.
        return False
    return True


def resolve_bot_username(bot_token: str) -> str | None:
    if not bot_token:
        return None
    try:
        with urlopen(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=3) as response:
            account = json.loads(response.read())
        username = account.get("result", {}).get("username")
        if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            return username
    except (OSError, ValueError, AttributeError):
        # Network errors may include the credential-bearing request URL.
        pass
    return None


def _school_classes(database: Any, connection: Any) -> list[str]:
    rows = database.execute(connection, """
        WITH classes AS (
            SELECT trim(COALESCE(NULLIF(trim(base_class_name), ''), name)) AS class_name FROM groups
            WHERE group_type = 'class' AND canonical IS TRUE AND archived_at IS NULL
        )
        SELECT class_name FROM classes WHERE class_name <> ''
        UNION
        SELECT trim(class_name) FROM identities
        WHERE kind = 'student' AND status = 'active' AND class_name IS NOT NULL AND trim(class_name) <> ''
          AND NOT EXISTS (SELECT 1 FROM classes)
        ORDER BY class_name LIMIT 60
    """).fetchall()
    return [row["class_name"] for row in rows]


def _set_draft(database: Any, connection: Any, user_id: Any, step: str, role: str | None = None,
               class_name: str | None = None, name: str | None = None) -> dict:
    return dict(database.execute(connection, """
        INSERT INTO telegram_registration_drafts(user_id, step, requested_role, class_name, display_name)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET step = excluded.step, requested_role = excluded.requested_role,
            class_name = excluded.class_name, display_name = excluded.display_name, updated_at = CURRENT_TIMESTAMP
        RETURNING *
    """, (user_id, step, role, class_name, name)).fetchone())


def _prompt(draft: dict, classes: list[str], prefix: str = "") -> tuple[str, dict]:
    step = draft["step"]
    controls = [STATUS, CANCEL]
    if step == "role":
        return prefix + "Выберите, кто вы.", _keyboard(list(ROLE_LABELS.values()), controls)
    if step == "class":
        rows = [classes[i:i + 3] for i in range(0, len(classes), 3)]
        return prefix + "Выберите свой класс.", _keyboard(*rows, [OTHER_CLASS], controls)
    if step == "manual_class":
        return prefix + "Напишите свой класс, например 8Б.", _keyboard(controls)
    if step == "name":
        return prefix + "Напишите имя и фамилию, как в школьном журнале.", _keyboard(controls)
    school_class = f"\nКласс: {draft['class_name']}" if draft["class_name"] else ""
    status = f"{ROLE_LABELS[draft['requested_role']]}{school_class}\nИмя и фамилия: {draft['display_name']}"
    edits = [EDIT, EDIT_ROLE]
    if draft["requested_role"] == "student":
        edits.insert(1, EDIT_CLASS)
    return prefix + f"Проверьте сведения\n\n{status}", _keyboard([SUBMIT], edits, controls)


def _registration_reply(
    database: Any, connection: Any, user: Any, text: str, command: str, web_app_url: str | None,
) -> tuple[str, dict] | None:
    user_id = user["id"]
    if user["access_revoked_at"]:
        return "Доступ приостановлен. Обратитесь к школьному администратору.", {"remove_keyboard": True}
    connected = database.execute(connection, """
        SELECT 1 FROM account_identity_links l JOIN identities i ON i.id = l.identity_id
        JOIN users u ON u.id=l.user_id AND u.identity_id=l.identity_id
        JOIN user_roles r ON r.user_id = l.user_id AND CAST(r.role AS TEXT) = i.kind
        WHERE l.user_id = ? AND l.status = 'confirmed' AND i.status = 'active'
        UNION ALL SELECT 1 FROM user_roles WHERE user_id = ? AND role = 'admin'
        LIMIT 1
    """, (user_id, user_id)).fetchone()
    if connected:
        return "Добро пожаловать на Остров!", _island_menu(web_app_url)
    claim = database.execute(connection, """
        SELECT status, review_reason FROM identity_claims WHERE user_id = ?
        ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, created_at DESC, id DESC LIMIT 1
    """, (user_id,)).fetchone()
    if claim and claim["status"] == "pending":
        return "Заявка ждёт проверки. Сообщим, когда администратор её рассмотрит.", _keyboard([STATUS])
    draft = database.execute(connection, "SELECT * FROM telegram_registration_drafts WHERE user_id = ?", (user_id,)).fetchone()
    if command == "/cancel" or text == CANCEL:
        database.execute(connection, "DELETE FROM telegram_registration_drafts WHERE user_id = ?", (user_id,))
        return "Черновик отменён. Чтобы начать заново, нажмите /start.", {"remove_keyboard": True}
    if not draft:
        if claim and claim["status"] in {"rejected", "identity_conflict"} and text != RESTART:
            return _rejection_message(claim["review_reason"]), _keyboard([RESTART], [STATUS])
        if command == "/status" or text == STATUS:
            return "Заявки пока нет. Начать регистрацию можно командой /start.", {"remove_keyboard": True}
        if command != "/start" and (text != RESTART or not claim):
            return None
        draft = _set_draft(database, connection, user_id, "role")
        return _prompt(draft, [], "Привет! Давайте подключим вас к Острову.\n\n")

    draft = dict(draft)
    step = draft["step"]
    role, class_name = draft["requested_role"], draft["class_name"]
    prefix = ""
    if command in {"/start", "/status"} or text == STATUS:
        prefix = "У вас есть черновик заявки.\n\n"
    elif step == "role" and text in ROLE_LABELS.values():
        role = next(key for key, label in ROLE_LABELS.items() if label == text)
        draft = _set_draft(database, connection, user_id, "class" if role == "student" else "name", role)
    elif step == "class":
        if text == OTHER_CLASS:
            draft = _set_draft(database, connection, user_id, "manual_class", role, name=draft["display_name"])
        elif text in _school_classes(database, connection):
            name = draft["display_name"]
            draft = _set_draft(database, connection, user_id, "review" if name else "name", role, text, name)
    elif step == "manual_class":
        if 1 <= len(text) <= 30 and any(c.isalnum() for c in text) and not text.startswith("/"):
            name = draft["display_name"]
            draft = _set_draft(database, connection, user_id, "review" if name else "name", role, text, name)
    elif step == "name":
        words = text.split()
        if 2 <= len(words) <= 8 and len(text) <= 120 and all(
            any(c.isalpha() for c in word) and all(c.isalpha() or c in "-‐‑’'" for c in word) for word in words
        ):
            draft = _set_draft(database, connection, user_id, "review", role, class_name, text)
        else:
            prefix = "Укажите имя и фамилию буквами.\n\n"
    elif step == "review":
        if text == EDIT_ROLE:
            draft = _set_draft(database, connection, user_id, "role")
        elif text == EDIT_CLASS and role == "student":
            draft = _set_draft(database, connection, user_id, "class", role, name=draft["display_name"])
        elif text == EDIT:
            draft = _set_draft(database, connection, user_id, "name", role, class_name)
        elif text == SUBMIT:
            claim = database.execute(connection, """
                INSERT INTO identity_claims(user_id, requested_role, submitted_name, submitted_class_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) WHERE status = 'pending' DO NOTHING
                RETURNING id
            """, (user_id, role, draft["display_name"], class_name)).fetchone()
            if claim:
                database.execute(connection, """
                    INSERT INTO audit_log(actor_user_id, action, entity_type, entity_id)
                    VALUES (?, 'identity_claim.submitted', 'identity_claim', ?)
                """, (user_id, claim["id"]))
            database.execute(connection, "DELETE FROM telegram_registration_drafts WHERE user_id = ?", (user_id,))
            return "Заявка отправлена. Сообщим, когда администратор её рассмотрит.", _keyboard([STATUS])
    classes = _school_classes(database, connection) if draft["step"] == "class" else []
    return _prompt(draft, classes, prefix)


def handle_telegram_update(
    database: Any, update: dict[str, Any], bot_token: str, web_app_url: str | None = None,
    send_message: Callable[[str, int, str, dict | None], None] | None = None,
) -> bool:
    message = update.get("message") if isinstance(update, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("text"), str):
        return False
    update_id, chat, sender = update.get("update_id"), message.get("chat"), message.get("from")
    if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(sender, dict):
        return False
    user_id = sender.get("id")
    if (type(user_id) is not int or not 0 < user_id < 2**52 or type(chat.get("id")) is not int
            or user_id != chat["id"] or sender.get("is_bot")
            or type(update_id) is not int or not 0 <= update_id < 2**63):
        return False
    text = " ".join(message["text"].split())
    if not text or len(text) > 4096:
        return False
    command = _command(text)
    if command.startswith("/") and command not in {"/start", "/status", "/cancel", "/admincode"}:
        return False
    if send_message is None:
        send_message = _send_message
    now = datetime.now(timezone.utc)
    rate_cutoff = now - timedelta(minutes=1)
    retention_cutoff = now - timedelta(days=2)
    if not database.database_url:
        rate_cutoff = rate_cutoff.strftime("%Y-%m-%d %H:%M:%S")
        retention_cutoff = retention_cutoff.strftime("%Y-%m-%d %H:%M:%S")
    with database.connection() as connection:
        lock_access(database, connection)
        existing = database.execute(connection, "SELECT id FROM users WHERE telegram_user_id = ?", (user_id,)).fetchone()
        if not existing and command != "/start" and command != "/admincode":
            return False
        recent = database.execute(connection, """
            SELECT COUNT(*) AS count FROM telegram_processed_updates
            WHERE telegram_user_id = ? AND created_at >= ?
        """, (user_id, rate_cutoff)).fetchone()
        if recent["count"] >= 20:
            return True
        recorded = database.execute(connection, """
            INSERT INTO telegram_processed_updates(update_id, telegram_user_id)
            VALUES (?, ?) ON CONFLICT(update_id) DO NOTHING RETURNING update_id
        """, (update_id, user_id)).fetchone()
        if not recorded:
            return True
        database.execute(connection, "DELETE FROM telegram_processed_updates WHERE created_at < ?", (retention_cutoff,))
        if command == "/admincode" and not existing:
            reply = ("Доступ разрешён только пользователю с ролью admin.", None)
        else:
            user = record_telegram_contact(database, connection, sender)
            reply = None if command == "/admincode" else _registration_reply(database, connection, user, text, command, web_app_url)

    # The draft or claim is durable even if Telegram cannot deliver the reply.
    try:
        if command == "/admincode" and reply is None:
            handle_admin_code_update(database, update, bot_token, lambda token, chat_id, response: send_message(token, chat_id, response, None))
        elif reply:
            send_message(bot_token, user_id, *reply)
    except OSError:
        # Telegram transport errors may contain the URL with the bot token.
        pass
    return True
