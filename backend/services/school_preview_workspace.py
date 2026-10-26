"""Read-only school data with isolated, local preview lifecycle state.

The source database is used only through ``ReadOnlySchoolDatabase`` methods.
Preview metadata is kept in a separate SQLite file and contains opaque IDs,
never copied names or other directory data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from backend.database import Database
from backend.services.school_experiment_workspace import ReadOnlySchoolDatabase, SCHOOL_DB


class SchoolPreviewDatabase(ReadOnlySchoolDatabase):
    """Canonical read methods plus local-only preview and audit persistence."""

    school_preview_isolated = True

    def __init__(self, url: str, session_store_path: str | Path | None = None) -> None:
        super().__init__(url)
        self.session_store_path = Path(session_store_path or SCHOOL_DB.parent / "school-preview.sqlite3")

    def initialize(self) -> None:
        """Initialize only the private local control store; never migrate source."""
        self.session_store_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.session_store_path)
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS school_preview_sessions (
                id TEXT PRIMARY KEY, actor_user_id TEXT NOT NULL, target_identity_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('student','teacher')),
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, ended_at TEXT)""")
            connection.execute("CREATE INDEX IF NOT EXISTS school_preview_actor_active_idx ON school_preview_sessions(actor_user_id, expires_at)")
            connection.execute("""CREATE TABLE IF NOT EXISTS school_preview_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id TEXT, action TEXT NOT NULL,
                entity_type TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _local_connection(self):
        self.session_store_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.session_store_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_student_preview(
        self, actor_user_id: Any, target_user_id: Any, created_at: str,
        expires_at: str, target_identity_id: Any | None = None, role: str = "student",
    ) -> dict[str, Any]:
        if role not in {"student", "teacher"}:
            raise ValueError("Preview role must be student or teacher")
        if target_identity_id is None:
            raise ValueError("A school identity ID is required")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        # Lifecycle is always capped at one hour, regardless of caller input.
        expiry = now + timedelta(hours=1)
        preview_id = str(uuid.uuid4())
        self.initialize()
        with self._local_connection() as connection:
            connection.execute(
                "INSERT INTO school_preview_sessions(id,actor_user_id,target_identity_id,role,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                (preview_id, str(actor_user_id), str(target_identity_id), role,
                 now.strftime("%Y-%m-%d %H:%M:%S"), expiry.strftime("%Y-%m-%d %H:%M:%S")),
            )
            connection.execute(
                "INSERT INTO school_preview_audit(actor_user_id,action,entity_type,details) VALUES(?,?,?,?)",
                (str(actor_user_id), "student_preview.started", "student_preview", json.dumps({"preview_id": preview_id})),
            )
            row = connection.execute("SELECT * FROM school_preview_sessions WHERE id=?", (preview_id,)).fetchone()
        return self._preview_row(row)

    @staticmethod
    def _preview_row(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["target_user_id"] = result["actor_user_id"]
        result["target_identity_id"] = result.pop("target_identity_id")
        return result

    def get_active_student_preview(self, preview_id: str, actor_user_id: Any) -> dict[str, Any] | None:
        self.initialize()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self._local_connection() as connection:
            row = connection.execute(
                "SELECT * FROM school_preview_sessions WHERE id=? AND actor_user_id=? AND ended_at IS NULL AND expires_at>?",
                (preview_id, str(actor_user_id), now),
            ).fetchone()
        return self._preview_row(row)

    def end_student_preview(self, preview_id: str, actor_user_id: Any) -> bool:
        self.initialize()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self._local_connection() as connection:
            result = connection.execute(
                "UPDATE school_preview_sessions SET ended_at=? WHERE id=? AND actor_user_id=? AND ended_at IS NULL",
                (now, preview_id, str(actor_user_id)),
            )
            if result.rowcount:
                connection.execute(
                    "INSERT INTO school_preview_audit(actor_user_id,action,entity_type,details) VALUES(?,?,?,?)",
                    (str(actor_user_id), "student_preview.ended", "student_preview", json.dumps({"preview_id": preview_id})),
                )
            return bool(result.rowcount)

    def record_audit_event(self, action: str, entity_type: str, details: Any | None = None, actor_user_id: Any | None = None) -> None:
        self.initialize()
        with self._local_connection() as connection:
            connection.execute(
                "INSERT INTO school_preview_audit(actor_user_id,action,entity_type,details) VALUES(?,?,?,?)",
                (str(actor_user_id) if actor_user_id is not None else None, action, entity_type,
                 json.dumps(details, ensure_ascii=False, sort_keys=True, default=str) if details is not None else None),
            )

    def get_admin_browser_session(self, token: str) -> Any | None:
        """Validate a source session without mutating its last-seen timestamp."""
        with self.connection() as connection:
            row = self.execute(connection, """SELECT s.*, u.telegram_user_id, u.role, u.identity_id
                FROM admin_browser_sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.revoked_at IS NULL""", (self._token_hash(token),)).fetchone()
        if not row:
            return None
        try:
            if datetime.fromisoformat(str(row["expires_at"])) <= datetime.now(timezone.utc):
                return None
        except ValueError:
            return None
        return row

    def consume_admin_login_challenge(self, token: str, ttl_seconds: int = 86400 * 7) -> tuple[str, Any] | None:
        """Allow the existing login flow to write only its auth tables."""
        return Database(database_url=self.database_url).consume_admin_login_challenge(token, ttl_seconds)

    def revoke_admin_browser_session(self, token: str) -> None:
        """Revoke an auth session using the existing narrowly scoped DB method."""
        Database(database_url=self.database_url).revoke_admin_browser_session(token)

    def get_or_create_user(self, telegram_user_id: int | None, role: str) -> Any:
        user = self.find_user(telegram_user_id) if telegram_user_id is not None else None
        if user:
            return user
        raise ValueError("School preview cannot create users")

    def ensure_bootstrap_roles(self, telegram_user_id: int, roles: tuple[str, ...]) -> Any | None:
        return self.find_user(telegram_user_id)

