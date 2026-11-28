from __future__ import annotations

import json
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID


class AccessError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def lock_access(database, connection):
    # A single row serializes infrequent authority changes and browser-code
    # issuance/consumption across service instances. SQLite takes a write lock.
    row = database.execute(connection, "UPDATE access_control SET id=id WHERE id=1 RETURNING manager_user_id").fetchone()
    if row is None:
        database.execute(connection, "INSERT INTO access_control(id) VALUES (1) ON CONFLICT(id) DO NOTHING")
        row = database.execute(connection, "UPDATE access_control SET id=id WHERE id=1 RETURNING manager_user_id").fetchone()
    return row["manager_user_id"]


def active_admin(database, connection, user_id):
    return database.execute(connection, """
        SELECT 1 FROM users u JOIN user_roles r ON r.user_id=u.id
        WHERE u.id=? AND u.access_revoked_at IS NULL AND r.role='admin'
    """, (user_id,)).fetchone() is not None


def record_telegram_contact(database, connection, profile):
    def display_field(key, limit):
        value = profile.get(key)
        if isinstance(value, str):
            return value.replace("\x00", "").strip()[:limit] or None
        return None

    return database.execute(connection, """
        INSERT INTO users(telegram_user_id, role, telegram_username, telegram_first_name, telegram_last_name, last_active_at)
        VALUES (?, 'student', ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(telegram_user_id) DO UPDATE SET
          telegram_username=excluded.telegram_username, telegram_first_name=excluded.telegram_first_name,
          telegram_last_name=excluded.telegram_last_name, last_active_at=CURRENT_TIMESTAMP
        RETURNING id, access_revoked_at
    """, (profile["id"], display_field("username", 32), display_field("first_name", 128),
          display_field("last_name", 128))).fetchone()


def bootstrap_manager(database, configured_ids, *, local_test=False):
    """Pin the original authority once, independently of subsequent logins."""
    with database.connection() as connection:
        control = database.execute(connection, "SELECT manager_user_id FROM access_control WHERE id=1").fetchone()
        if control is not None and control["manager_user_id"] is not None:
            return control["manager_user_id"]
        manager = lock_access(database, connection)
        if manager is not None:
            return manager
        configured_ids = tuple(configured_ids) or ((900002,) if local_test else ())
        admins = database.execute(connection, """
            SELECT u.id, u.telegram_user_id, r.source
            FROM users u JOIN user_roles r ON r.user_id=u.id
            WHERE u.access_revoked_at IS NULL AND r.role='admin' AND u.telegram_user_id IS NOT NULL
            ORDER BY r.created_at, u.id
        """).fetchall()
        candidates = {row["telegram_user_id"]: row["id"] for row in admins}
        manager = next((candidates[item] for item in configured_ids if item in candidates), None)
        if manager is None and not configured_ids:
            historical = [row["id"] for row in admins if row["source"] == "telegram_bootstrap"]
            manager = historical[0] if len(historical) == 1 else None
        if manager is None and not configured_ids and len(admins) == 1:
            manager = admins[0]["id"]
        if manager is None and not admins and configured_ids and not local_test:
            telegram_id = configured_ids[0]
            database.execute(connection, """
                INSERT INTO users(telegram_user_id, role) VALUES (?, 'admin')
                ON CONFLICT(telegram_user_id) DO NOTHING
            """, (telegram_id,))
            row = database.execute(connection, "SELECT id FROM users WHERE telegram_user_id=? AND access_revoked_at IS NULL", (telegram_id,)).fetchone()
            if row:
                manager = row["id"]
                database.execute(connection, "INSERT INTO user_roles(user_id, role, source) VALUES (?, 'admin', 'telegram_bootstrap')", (manager,))
        if manager is not None:
            database.execute(connection, "UPDATE access_control SET manager_user_id=? WHERE id=1", (manager,))
            _audit(database, connection, manager, "access.manager_initialized", "user", manager)
        return manager


def _key(database, value):
    try:
        return UUID(str(value)) if database.database_url else int(value)
    except (TypeError, ValueError, AttributeError):
        raise AccessError(404, "Запись не найдена.") from None


def _audit(database, connection, actor_id, action, entity, entity_id, **details):
    database.execute(connection, """
        INSERT INTO audit_log(actor_user_id, action, entity_type, entity_id, details) VALUES (?, ?, ?, ?, ?)
    """, (actor_id, action, entity, entity_id, json.dumps(details, ensure_ascii=False, default=str)))


def _get_account(database, connection, value):
    row = database.execute(connection, """
        SELECT u.*, l.identity_id AS linked_identity_id, l.status AS link_status, l.confirmed_at
        FROM users u LEFT JOIN account_identity_links l ON l.user_id=u.id
        WHERE u.id=? AND u.telegram_user_id IS NOT NULL
    """, (_key(database, value),)).fetchone()
    if row is None:
        raise AccessError(404, "Telegram-аккаунт не найден.")
    return row


def _get_identity(database, connection, value):
    identity = database.execute(connection, """
        SELECT id, kind, display_name, class_name FROM identities WHERE id=? AND status='active'
    """, (_key(database, value),)).fetchone()
    if identity is None:
        raise AccessError(404, "Действующая школьная запись не найдена.")
    return identity


def _roles(database, connection, user_id):
    return [str(row["role"]) for row in database.execute(connection,
        "SELECT role FROM user_roles WHERE user_id=? ORDER BY role", (user_id,)).fetchall()]


def _authorize(database, connection, actor, *, manager_only=False):
    manager = lock_access(database, connection)
    if not active_admin(database, connection, actor):
        raise AccessError(403, "Нужен действующий доступ администратора.")
    if manager_only and manager != actor:
        raise AccessError(403, "Это действие доступно управляющему администратору.")
    return manager


def _protect_target(database, connection, actor, manager, target):
    if actor != manager and (target == manager or "admin" in _roles(database, connection, target)):
        raise AccessError(403, "Доступ этого аккаунта меняет управляющий администратор.")


def _invalidate(database, connection, user_id):
    for table, field, user_field in (
        ("admin_browser_sessions", "revoked_at", "user_id"),
        ("admin_login_challenges", "consumed_at", "actor_user_id"),
        ("student_preview_sessions", "ended_at", "actor_user_id"),
    ):
        database.execute(connection, f"UPDATE {table} SET {field}=CURRENT_TIMESTAMP WHERE {user_field}=? AND {field} IS NULL", (user_id,))


def _revoke(database, connection, user_id):
    database.execute(connection, """
        UPDATE users SET access_revoked_at=CURRENT_TIMESTAMP, identity_id=NULL, telegram_auth_after=? WHERE id=?
    """, (int(time.time()), user_id))
    database.execute(connection, "UPDATE account_identity_links SET status='revoked' WHERE user_id=? AND status='confirmed'", (user_id,))
    _invalidate(database, connection, user_id)


def _bind(database, connection, actor, manager, target, identity, replace_user_id, source, source_ref=None):
    user_id, identity_id = target["id"], identity["id"]
    _protect_target(database, connection, actor, manager, user_id)
    occupied = database.execute(connection, """
        SELECT user_id FROM account_identity_links WHERE identity_id=? AND user_id<>?
    """, (identity_id, user_id)).fetchone()
    if occupied:
        previous = occupied["user_id"]
        if replace_user_id is None or _key(database, replace_user_id) != previous:
            raise AccessError(409, "У школьной записи уже есть аккаунт. Подтвердите замену конкретного аккаунта.")
        if previous == manager or "admin" in _roles(database, connection, previous):
            raise AccessError(403, "Сначала снимите административную роль со старого аккаунта.")
        _revoke(database, connection, previous)
        database.execute(connection, "DELETE FROM account_identity_links WHERE user_id=?", (previous,))
        _audit(database, connection, actor, "access.account_transferred", "user", previous, identity_id=identity_id, new_user_id=user_id)
    elif replace_user_id is not None:
        raise AccessError(409, "Прежняя привязка изменилась. Обновите список.")
    # Binding a revoked account preserves its revoked state until the operator
    # explicitly restores access. Approving a new account starts it connected.
    active = target["access_revoked_at"] is None
    database.execute(connection, """
        INSERT INTO account_identity_links(user_id, identity_id, status, source, source_ref, confirmed_by, confirmed_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET identity_id=excluded.identity_id, status=excluded.status,
          source=excluded.source, source_ref=excluded.source_ref, confirmed_by=excluded.confirmed_by,
          confirmed_at=excluded.confirmed_at
    """, (user_id, identity_id, "confirmed" if active else "revoked", source, source_ref, actor))
    # Existing role assignments remain available in the admin view. The
    # effective school role always has to match the confirmed identity.
    database.execute(connection, """
        INSERT INTO user_roles(user_id, role, source) VALUES (?, ?, ?)
        ON CONFLICT(user_id, role) DO NOTHING
    """, (user_id, identity["kind"], source))
    database.execute(connection, "UPDATE users SET identity_id=?, role=? WHERE id=?",
                     (identity_id if active else None, identity["kind"], user_id))
    if target["linked_identity_id"] not in {None, identity_id}:
        database.execute(connection, "UPDATE users SET telegram_auth_after=? WHERE id=?", (int(time.time()), user_id))
    _invalidate(database, connection, user_id)
    _audit(database, connection, actor, "access.linked", "user", user_id,
           identity_id=identity_id, previous_identity_id=target["linked_identity_id"])


def change_account(database, actor, user_id, action, *, identity_id=None, replace_user_id=None, expected_identity_id=None, check_expected=False, expected_roles=None):
    with database.connection() as connection:
        manager = _authorize(database, connection, actor, manager_only=action in {"grant_admin", "remove_admin"})
        target = _get_account(database, connection, user_id)
        user_id = target["id"]
        _protect_target(database, connection, actor, manager, user_id)
        roles = _roles(database, connection, user_id)
        if expected_roles is not None and expected_roles - {"admin"} != set(roles) - {"admin"}:
            raise AccessError(400, "Школьная роль меняется после подтверждения школьной записи.")
        if action == "link":
            if identity_id is None:
                raise AccessError(400, "Выберите школьную запись.")
            expected = _key(database, expected_identity_id) if expected_identity_id is not None else None
            if check_expected and target["linked_identity_id"] != expected:
                raise AccessError(409, "Привязка уже изменилась. Обновите список.")
            identity = _get_identity(database, connection, identity_id)
            pending = database.execute(connection, "SELECT * FROM identity_claims WHERE user_id=? AND status='pending'", (user_id,)).fetchone()
            if pending:
                if pending["requested_role"] != identity["kind"] or target["access_revoked_at"] is not None:
                    raise AccessError(409, "Этот аккаунт подал заявку. Проверьте её в разделе «Заявки».")
                _bind(database, connection, actor, manager, target, identity, replace_user_id, "identity_claim", str(pending["id"]))
                return _finish_claim(database, connection, actor, pending, target, "approved", identity["id"], None)
            _bind(database, connection, actor, manager, target, identity, replace_user_id, "admin_directory")
        elif action == "revoke":
            if user_id == manager:
                raise AccessError(409, "Доступ управляющего администратора защищён.")
            if target["access_revoked_at"] is None:
                _revoke(database, connection, user_id)
                _audit(database, connection, actor, "access.revoked", "user", user_id)
        elif action == "restore":
            if target["access_revoked_at"] is not None or target["link_status"] == "revoked":
                linked = target["linked_identity_id"]
                if linked is not None:
                    if target["link_status"] not in {"revoked", "confirmed"}:
                        raise AccessError(409, "Сначала подтвердите школьную привязку аккаунта.")
                    identity = _get_identity(database, connection, linked)
                    database.execute(connection, """INSERT INTO user_roles(user_id,role,source)
                        VALUES (?, ?, 'admin_directory') ON CONFLICT(user_id,role) DO NOTHING""", (user_id, identity["kind"]))
                    database.execute(connection, "UPDATE account_identity_links SET status='confirmed' WHERE user_id=? AND status='revoked'", (user_id,))
                database.execute(connection, "UPDATE users SET access_revoked_at=NULL, identity_id=? WHERE id=?", (linked, user_id))
                _audit(database, connection, actor, "access.restored", "user", user_id)
        elif action == "grant_admin":
            if "admin" in roles:
                return
            if target["access_revoked_at"] is not None or target["link_status"] != "confirmed":
                raise AccessError(409, "Сначала подтвердите школьную запись и доступ пользователя.")
            identity = _get_identity(database, connection, target["linked_identity_id"])
            if identity["kind"] not in roles or target["identity_id"] != identity["id"]:
                raise AccessError(409, "Сначала исправьте школьную привязку пользователя.")
            database.execute(connection, "INSERT INTO user_roles(user_id, role, source) VALUES (?, 'admin', 'access_manager')", (user_id,))
            _audit(database, connection, actor, "access.admin_granted", "user", user_id)
        elif action == "remove_admin":
            if user_id == manager:
                raise AccessError(409, "Нельзя снять роль управляющего администратора.")
            if "admin" in roles:
                database.execute(connection, "DELETE FROM user_roles WHERE user_id=? AND role='admin'", (user_id,))
                fallback = next((role for role in roles if role != "admin"), "student")
                database.execute(connection, "UPDATE users SET role=?, telegram_auth_after=? WHERE id=?",
                                 (fallback, int(time.time()), user_id))
                _invalidate(database, connection, user_id)
                _audit(database, connection, actor, "access.admin_removed", "user", user_id)
        else:
            raise AccessError(400, "Действие не поддерживается.")


def review_claim(database, actor, claim_id, decision, *, identity_id=None, reason=None, replace_user_id=None):
    with database.connection() as connection:
        manager = _authorize(database, connection, actor)
        claim = database.execute(connection, "SELECT * FROM identity_claims WHERE id=?", (_key(database, claim_id),)).fetchone()
        if claim is None:
            raise AccessError(404, "Заявка не найдена.")
        if claim["status"] != "pending":
            raise AccessError(409, "Эту заявку уже рассмотрели. Обновите список.")
        target = _get_account(database, connection, claim["user_id"])
        _protect_target(database, connection, actor, manager, target["id"])
        if decision == "approve":
            identity_id = identity_id or claim["identity_id"]
            if identity_id is None:
                raise AccessError(400, "Выберите школьную запись для подтверждения.")
            if target["access_revoked_at"] is not None or target["link_status"] == "confirmed":
                raise AccessError(409, "У аккаунта уже изменился доступ. Проверьте его в списке аккаунтов.")
            identity = _get_identity(database, connection, identity_id)
            if identity["kind"] != claim["requested_role"]:
                raise AccessError(409, "Выбранная школьная запись не соответствует роли в заявке.")
            identity_id = identity["id"]
            _bind(database, connection, actor, manager, target, identity, replace_user_id, "identity_claim", str(claim_id))
            status, reason = "approved", None
        elif decision == "reject":
            reason = (reason or "").replace("\x00", "").strip()
            if not reason or len(reason) > 500:
                raise AccessError(400, "Укажите причину отклонения длиной до 500 символов.")
            status, identity_id = "rejected", claim["identity_id"]
        else:
            raise AccessError(400, "Решение не поддерживается.")
        return _finish_claim(database, connection, actor, claim, target, status, identity_id, reason)


def _finish_claim(database, connection, actor, claim, target, status, identity_id, reason):
    database.execute(connection, """
        UPDATE identity_claims SET status=?, identity_id=?, review_reason=?, reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP WHERE id=?
    """, (status, identity_id, reason, actor, claim["id"]))
    _audit(database, connection, actor, f"identity_claim.{status}", "identity_claim", claim["id"], user_id=target["id"], identity_id=identity_id)
    return {"id": str(claim["id"]), "status": status, "reason": reason, "telegram_user_id": target["telegram_user_id"]}


def _normalize(value):
    value = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    return " ".join(value.split())


def _name_keys(value):
    parts = _normalize(value).split()
    return {tuple(sorted(parts)), tuple(sorted(parts[:2]))} if len(parts) >= 2 else {tuple(parts)}


def _date(value):
    if value is None:
        return None
    timestamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    return (timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)).isoformat()


def access_snapshot(database, actor):
    with database.connection() as connection:
        if not active_admin(database, connection, actor):
            raise AccessError(403, "Нужен действующий доступ администратора.")
        manager = database.execute(connection, "SELECT manager_user_id FROM access_control WHERE id=1").fetchone()["manager_user_id"]
        identity_rows = database.execute(connection, """
            SELECT i.id, i.display_name, i.class_name, i.kind, i.status, l.user_id
            FROM identities i LEFT JOIN account_identity_links l ON l.identity_id=i.id
            ORDER BY i.kind, i.display_name, i.id
        """).fetchall()
        roles = defaultdict(list)
        for role in database.execute(connection, "SELECT user_id, role FROM user_roles ORDER BY role").fetchall():
            roles[role["user_id"]].append(str(role["role"]))
        user_rows = database.execute(connection, """
            SELECT u.*, l.identity_id AS linked_id, l.status AS link_status, l.confirmed_at
            FROM users u LEFT JOIN account_identity_links l ON l.user_id=u.id
            WHERE u.telegram_user_id IS NOT NULL ORDER BY u.created_at DESC, u.id
        """).fetchall()
        claim_rows = database.execute(connection, """
            SELECT c.*, i.display_name AS old_name, i.class_name AS old_class
            FROM identity_claims c LEFT JOIN identities i ON i.id=c.identity_id
            JOIN users u ON u.id=c.user_id AND u.telegram_user_id IS NOT NULL
            ORDER BY c.created_at DESC, c.id DESC
        """).fetchall()
    identities = {row["id"]: {
        "id": str(row["id"]), "display_name": row["display_name"], "class_name": row["class_name"],
        "kind": row["kind"], "linked_user_id": str(row["user_id"]) if row["user_id"] is not None else None,
    } for row in identity_rows}
    active = [identities[row["id"]] for row in identity_rows if row["status"] == "active"]
    active_ids = {item["id"] for item in active}
    accounts = []
    for row in user_rows:
        current_roles = roles[row["id"]]
        state = "unconfirmed"
        if row["access_revoked_at"] is not None or row["link_status"] == "revoked":
            state = "revoked"
        elif (row["link_status"] == "confirmed" and str(row["linked_id"]) in active_ids
              and row["identity_id"] == row["linked_id"]
              and identities[row["linked_id"]]["kind"] in current_roles) or "admin" in current_roles:
            state = "connected"
        accounts.append({
            "id": str(row["id"]), "telegram_user_id": str(row["telegram_user_id"]), "username": row["telegram_username"],
            "telegram_first_name": row["telegram_first_name"], "telegram_last_name": row["telegram_last_name"],
            "roles": current_roles, "state": state, "identity": identities.get(row["linked_id"]),
            "link_status": row["link_status"],
            "connected_at": _date(row["confirmed_at"]), "last_active_at": _date(row["last_active_at"]),
            "can_manage_administrators": row["id"] == manager, "created_at": _date(row["created_at"]),
        })
    candidates = defaultdict(set)
    for identity in active:
        class_name = _normalize(identity["class_name"]) if identity["kind"] == "student" else ""
        for name in _name_keys(identity["display_name"]):
            candidates[(identity["kind"], class_name, name)].add(identity["id"])
    claims = []
    for row in claim_rows:
        name, school_class = row["submitted_name"] or row["old_name"], row["submitted_class_name"] or row["old_class"]
        key = tuple(sorted(_normalize(name).split()))
        suggested = candidates.get((row["requested_role"], _normalize(school_class) if row["requested_role"] == "student" else "", key), set())
        claims.append({
            "id": str(row["id"]), "user_id": str(row["user_id"]), "requested_role": row["requested_role"],
            "submitted_name": name, "submitted_class": school_class, "status": str(row["status"]),
            "created_at": _date(row["created_at"]), "reviewed_at": _date(row["reviewed_at"]), "reason": row["review_reason"],
            "identity_id": str(row["identity_id"]) if row["identity_id"] is not None else None,
            "resolved_identity_name": row["old_name"] if row["status"] == "approved" else None,
            "resolved_identity_class": row["old_class"] if row["status"] == "approved" else None,
            "candidate_ids": sorted(suggested),
        })
    return {"can_manage_administrators": actor == manager, "accounts": accounts, "claims": claims, "identities": active,
            "counts": {"connected": sum(item["state"] == "connected" for item in accounts),
                       "pending": sum(item["status"] == "pending" for item in claims),
                       "unlinked_identities": sum(item["linked_user_id"] is None for item in active)}}
