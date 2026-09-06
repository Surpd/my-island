from __future__ import annotations

import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_user_id INTEGER UNIQUE,
  role TEXT NOT NULL CHECK (role IN ('student', 'teacher', 'admin')),
  identity_id INTEGER UNIQUE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS identities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('student', 'teacher')),
  display_name TEXT NOT NULL,
  class_name TEXT,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS identity_claims (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  identity_id INTEGER NOT NULL REFERENCES identities(id),
  requested_role TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'identity_conflict')),
  reviewed_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  group_type TEXT NOT NULL,
  UNIQUE(name, group_type)
);
CREATE TABLE IF NOT EXISTS memberships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL REFERENCES groups(id),
  identity_id INTEGER NOT NULL REFERENCES identities(id),
  member_role TEXT NOT NULL,
  source TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT,
  valid_until TEXT,
  UNIQUE(group_id, identity_id, source)
);
CREATE TABLE IF NOT EXISTS schedule_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_key TEXT NOT NULL UNIQUE,
  lesson_date TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT,
  subject TEXT NOT NULL,
  teacher TEXT,
  room TEXT,
  audience TEXT,
  subject_subgroup TEXT,
  exam_track TEXT,
  lesson_type TEXT,
  delivery_mode TEXT,
  parse_status TEXT NOT NULL DEFAULT 'parsed',
  parse_diagnostics TEXT,
  source_tab TEXT,
  source_coordinate TEXT,
  header_source_column TEXT,
  week_start TEXT,
  source_hash TEXT NOT NULL,
  raw_source TEXT
);
CREATE TABLE IF NOT EXISTS group_schedule_audiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL REFERENCES groups(id),
  audience TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  subject_subgroup TEXT NOT NULL DEFAULT '',
  exam_track TEXT NOT NULL DEFAULT '',
  UNIQUE(group_id, audience, subject, subject_subgroup, exam_track)
);
CREATE TABLE IF NOT EXISTS user_roles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('student', 'teacher', 'admin')),
  source TEXT NOT NULL DEFAULT 'legacy_user_role',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, role)
);
CREATE TABLE IF NOT EXISTS student_preview_sessions (
  id TEXT PRIMARY KEY,
  actor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  ended_at TEXT
);
CREATE INDEX IF NOT EXISTS user_roles_user_idx ON user_roles(user_id);
CREATE INDEX IF NOT EXISTS student_preview_actor_active_idx ON student_preview_sessions(actor_user_id, expires_at);
CREATE TABLE IF NOT EXISTS schedule_syncs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  source_hash TEXT,
  error TEXT,
  validation_problems TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  audience TEXT,
  expires_at TEXT,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  ends_at TEXT,
  location TEXT,
  audience TEXT,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS classroom_courses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL REFERENCES groups(id),
  external_course_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  teacher_account TEXT,
  section TEXT,
  description TEXT,
  room TEXT,
  update_time TEXT,
  raw_source TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS classroom_coursework (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  course_id INTEGER NOT NULL REFERENCES classroom_courses(id),
  external_coursework_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  due_at TEXT,
  alternate_link TEXT,
  state TEXT,
  work_type TEXT,
  max_points REAL,
  update_time TEXT,
  raw_source TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS classroom_student_submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  coursework_id INTEGER NOT NULL REFERENCES classroom_coursework(id),
  identity_id INTEGER NOT NULL REFERENCES identities(id),
  external_submission_id TEXT NOT NULL UNIQUE,
  external_student_id TEXT,
  state TEXT NOT NULL,
  assigned_grade REAL,
  draft_grade REAL,
  late INTEGER,
  raw_source TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS classroom_coursework_materials (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  course_id INTEGER REFERENCES classroom_courses(id),
  coursework_id INTEGER REFERENCES classroom_coursework(id),
  external_material_key TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL CHECK (source_type IN ('embedded', 'dedicated')),
  title TEXT NOT NULL DEFAULT '',
  material_type TEXT NOT NULL DEFAULT '',
  url TEXT,
  drive_file_id TEXT,
  raw_source TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK ((course_id IS NOT NULL) <> (coursework_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS official_grades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  identity_id INTEGER NOT NULL REFERENCES identities(id),
  subject TEXT NOT NULL,
  graded_on TEXT NOT NULL,
  value REAL NOT NULL,
  grade_type TEXT,
  weight REAL,
  comment TEXT,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_user_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id INTEGER,
  details TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: str | Path | None = None, database_url: str | None = None) -> None:
        # An explicit local path keeps isolated tests/local stores on SQLite even if the
        # process environment also contains the application's Postgres URL.
        self.database_url = database_url if database_url is not None else (None if path is not None else os.getenv("DATABASE_URL") or None)
        self.path = Path(path or os.getenv("DATABASE_PATH", "data/my-island.db"))

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if self.database_url:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as error:
                raise RuntimeError("psycopg is required when DATABASE_URL is configured") from error
            connection = psycopg.connect(self.database_url, row_factory=dict_row)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        # Production schema changes are explicit migrations, never app-startup mutations.
        if self.database_url:
            return
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO user_roles(user_id, role, source) SELECT id, role, 'legacy_user_role' FROM users"
            )

    def execute(self, connection: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
        if self.database_url:
            query = query.replace("?", "%s")
        return connection.execute(query, params)

    def get_or_create_user(self, telegram_user_id: int | None, role: str) -> Any:
        with self.connection() as connection:
            if telegram_user_id is not None:
                row = self.execute(connection, "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)).fetchone()
                if row:
                    return row
            inserted = self.execute(connection, "INSERT INTO users(telegram_user_id, role) VALUES (?, ?) RETURNING id", (telegram_user_id, role)).fetchone()
            return self.execute(connection, "SELECT * FROM users WHERE id = ?", (inserted["id"],)).fetchone()

    def ensure_bootstrap_roles(self, telegram_user_id: int, roles: tuple[str, ...]) -> Any | None:
        if not roles:
            return self.find_user(telegram_user_id)
        with self.connection() as connection:
            user = self.execute(connection, "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)).fetchone()
            if not user:
                return None
            placeholders = ",".join("?" for _ in roles)
            self.execute(connection, f"DELETE FROM user_roles WHERE user_id = ? AND role NOT IN ({placeholders})", (user["id"], *roles))
            for role in roles:
                self.execute(
                    connection,
                    "INSERT INTO user_roles(user_id, role, source) VALUES (?, ?, 'telegram_bootstrap') ON CONFLICT(user_id, role) DO NOTHING",
                    (user["id"], role),
                )
            primary_role = "teacher" if "teacher" in roles else roles[0]
            self.execute(connection, "UPDATE users SET role = ? WHERE id = ?", (primary_role, user["id"]))
            return self.execute(connection, "SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()

    def list_user_roles(self, user_id: Any) -> list[str]:
        with self.connection() as connection:
            rows = self.execute(connection, "SELECT role FROM user_roles WHERE user_id = ? ORDER BY role", (user_id,)).fetchall()
            if rows:
                return [str(row["role"]) for row in rows]
            row = self.execute(connection, "SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
            return [str(row["role"])] if row else []

    def user_has_role(self, user_id: Any, role: str) -> bool:
        return role in self.list_user_roles(user_id)

    def count_users_with_role(self, role: str) -> int:
        with self.connection() as connection:
            row = self.execute(connection, "SELECT COUNT(*) AS count FROM user_roles WHERE role = ?", (role,)).fetchone()
            return int(row["count"]) if row else 0

    def set_user_roles(self, user_id: Any, roles: list[str], primary_role: str) -> list[str]:
        with self.connection() as connection:
            current = self.execute(connection, "SELECT role FROM user_roles WHERE user_id = ?", (user_id,)).fetchall()
            if not current:
                self.execute(connection, "INSERT INTO user_roles(user_id, role, source) VALUES (?, ?, 'legacy_user_role') ON CONFLICT(user_id, role) DO NOTHING", (user_id, primary_role))
            placeholders = ",".join("?" for _ in roles)
            self.execute(connection, f"DELETE FROM user_roles WHERE user_id = ? AND role NOT IN ({placeholders})", (user_id, *roles))
            for role in roles:
                self.execute(
                    connection,
                    "INSERT INTO user_roles(user_id, role, source) VALUES (?, ?, 'admin_override') ON CONFLICT(user_id, role) DO UPDATE SET source = excluded.source",
                    (user_id, role),
                )
            self.execute(connection, "UPDATE users SET role = ? WHERE id = ?", (primary_role, user_id))
            return [str(row["role"]) for row in self.execute(connection, "SELECT role FROM user_roles WHERE user_id = ? ORDER BY role", (user_id,)).fetchall()]

    def create_student_preview(self, actor_user_id: Any, target_user_id: Any, created_at: str, expires_at: str) -> Any:
        import json
        import uuid
        preview_id = str(uuid.uuid4())
        with self.connection() as connection:
            self.execute(
                connection,
                "INSERT INTO student_preview_sessions(id, actor_user_id, target_user_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (preview_id, actor_user_id, target_user_id, created_at, expires_at),
            )
            self.execute(
                connection,
                "INSERT INTO audit_log(actor_user_id, action, entity_type, details) VALUES (?, ?, ?, ?)",
                (actor_user_id, "student_preview.started", "student_preview", json.dumps({"preview_id": preview_id})),
            )
            return self.execute(connection, "SELECT * FROM student_preview_sessions WHERE id = ?", (preview_id,)).fetchone()

    def get_active_student_preview(self, preview_id: str, actor_user_id: Any) -> Any | None:
        with self.connection() as connection:
            return self.execute(
                connection,
                "SELECT * FROM student_preview_sessions WHERE id = ? AND actor_user_id = ? AND ended_at IS NULL AND expires_at > CURRENT_TIMESTAMP",
                (preview_id, actor_user_id),
            ).fetchone()

    def end_student_preview(self, preview_id: str, actor_user_id: Any) -> bool:
        import json
        with self.connection() as connection:
            result = self.execute(
                connection,
                "UPDATE student_preview_sessions SET ended_at = CURRENT_TIMESTAMP WHERE id = ? AND actor_user_id = ? AND ended_at IS NULL",
                (preview_id, actor_user_id),
            )
            if result.rowcount:
                self.execute(
                    connection,
                    "INSERT INTO audit_log(actor_user_id, action, entity_type, details) VALUES (?, ?, ?, ?)",
                    (actor_user_id, "student_preview.ended", "student_preview", json.dumps({"preview_id": preview_id})),
                )
            return bool(result.rowcount)

    def find_user(self, telegram_user_id: int) -> Any | None:
        with self.connection() as connection:
            return self.execute(connection, "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)).fetchone()

    def get_user_by_id(self, user_id: Any) -> Any | None:
        with self.connection() as connection:
            return self.execute(connection, "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def create_claim(self, user_id: Any, identity_id: Any, requested_role: str) -> Any:
        with self.connection() as connection:
            existing = self.execute(connection,
                "SELECT * FROM identity_claims WHERE user_id = ? AND identity_id = ? AND status = 'pending'",
                (user_id, identity_id),
            ).fetchone()
            if existing:
                return existing
            inserted = self.execute(connection,
                "INSERT INTO identity_claims(user_id, identity_id, requested_role) VALUES (?, ?, ?) RETURNING id",
                (user_id, identity_id, requested_role),
            ).fetchone()
            return self.execute(connection, "SELECT * FROM identity_claims WHERE id = ?", (inserted["id"],)).fetchone()

    def review_claim(self, claim_id: Any, reviewer_id: Any, status: str) -> Any | None:
        with self.connection() as connection:
            claim = self.execute(connection, "SELECT * FROM identity_claims WHERE id = ?", (claim_id,)).fetchone()
            if not claim:
                return None
            if status == "approved":
                conflict = self.execute(connection,
                    "SELECT id FROM users WHERE identity_id = ? AND id != ?", (claim["identity_id"], claim["user_id"])
                ).fetchone()
                if conflict:
                    status = "identity_conflict"
                else:
                    self.execute(connection, "UPDATE users SET identity_id = ?, role = ? WHERE id = ?", (claim["identity_id"], claim["requested_role"], claim["user_id"]))
                    self.execute(
                        connection,
                        "INSERT INTO user_roles(user_id, role, source) VALUES (?, ?, 'identity_claim') ON CONFLICT(user_id, role) DO NOTHING",
                        (claim["user_id"], claim["requested_role"]),
                    )
            self.execute(connection, "UPDATE identity_claims SET status = ?, reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP WHERE id = ?", (status, reviewer_id, claim_id))
            self.execute(connection, "INSERT INTO audit_log(actor_user_id, action, entity_type, entity_id) VALUES (?, ?, ?, ?)", (reviewer_id, f"identity_claim.{status}", "identity_claim", claim_id))
            return self.execute(connection, "SELECT * FROM identity_claims WHERE id = ?", (claim_id,)).fetchone()

    def list_pending_claims(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return self.execute(connection, "SELECT c.*, i.display_name, i.class_name FROM identity_claims c JOIN identities i ON i.id = c.identity_id WHERE c.status = 'pending' ORDER BY c.created_at").fetchall()

    def list_schedule_entries(self, lesson_date: str) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return self.execute(connection, "SELECT lesson_date, start_time, end_time, subject, teacher, room, audience FROM schedule_entries WHERE lesson_date = ? ORDER BY start_time", (lesson_date,)).fetchall()

    def list_schedule_entries_for_user(self, user_id: Any, lesson_date: str, end_date: str | None = None) -> list[Any]:
        with self.connection() as connection:
            date_range = "se.lesson_date BETWEEN ?::date AND COALESCE(?::date, ?::date)" if self.database_url else "se.lesson_date BETWEEN ? AND COALESCE(?, ?)"
            return self.execute(
                connection,
                f"""SELECT DISTINCT se.lesson_date, se.start_time, se.end_time, se.subject, se.teacher,
                          se.room, se.audience, se.subject_subgroup, se.exam_track, se.lesson_type,
                          se.delivery_mode, se.parse_status, se.parse_diagnostics, se.source_tab,
                          se.source_coordinate
                     FROM schedule_entries se
                     JOIN group_schedule_audiences ga ON ga.audience = se.audience
                     JOIN memberships m ON m.group_id = ga.group_id AND m.active IS TRUE
                     JOIN users u ON u.identity_id = m.identity_id
                    WHERE u.id = ?
                      AND {date_range}
                      AND ((ga.subject_subgroup = '' AND ga.exam_track = ''
                            AND COALESCE(se.subject_subgroup, '') = '' AND COALESCE(se.exam_track, '') = '')
                        OR (ga.subject_subgroup <> '' AND se.subject_subgroup = ga.subject_subgroup
                            AND (ga.subject = '' OR se.subject = ga.subject))
                        OR (ga.exam_track <> '' AND se.exam_track = ga.exam_track
                            AND (ga.subject = '' OR se.subject = ga.subject)))
                    ORDER BY se.start_time, se.subject, se.source_coordinate""",
                (user_id, lesson_date, end_date, lesson_date),
            ).fetchall()

    def get_student_profile(self, user_id: Any) -> Any | None:
        with self.connection() as connection:
            user = self.execute(
                connection,
                """SELECT u.id, u.telegram_user_id, u.role, u.identity_id, i.display_name, i.class_name, i.kind
                     FROM users u LEFT JOIN identities i ON i.id = u.identity_id
                    WHERE u.id = ? AND u.identity_id IS NOT NULL
                      AND (u.role = 'student' OR EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role = 'student'))""",
                (user_id,),
            ).fetchone()
            if not user:
                return None
            groups = self.execute(
                connection,
                """SELECT g.id, g.name, g.group_type, m.member_role, m.source, m.active
                     FROM memberships m JOIN groups g ON g.id = m.group_id
                    WHERE m.identity_id = ? AND m.active IS TRUE
                    ORDER BY g.name""",
                (user["identity_id"],),
            ).fetchall()
            enriched_groups = []
            for group in groups:
                scopes = self.execute(
                    connection,
                    """SELECT audience, subject, subject_subgroup, exam_track
                         FROM group_schedule_audiences
                        WHERE group_id = ?
                        ORDER BY audience, subject, subject_subgroup, exam_track""",
                    (group["id"],),
                ).fetchall()
                scope_kind = "base_class"
                if any(row["exam_track"] for row in scopes):
                    scope_kind = "exam_track"
                elif any(row["subject_subgroup"] for row in scopes):
                    scope_kind = "subject_subgroup"
                enriched_groups.append({**dict(group), "scope_kind": scope_kind, "schedule_scopes": [dict(row) for row in scopes]})
            return {"user": user, "groups": enriched_groups}

    def list_teacher_courses(self, user_id: Any) -> list[Any]:
        with self.connection() as connection:
            return self.execute(
                connection,
                """SELECT c.external_course_id, c.title, c.section, c.description, c.group_id, g.name AS group_name
                     FROM classroom_courses c JOIN groups g ON g.id = c.group_id
                     JOIN memberships m ON m.group_id = g.id AND m.active IS TRUE AND m.member_role = 'teacher'
                     JOIN users u ON u.identity_id = m.identity_id
                    WHERE u.id = ?
                    ORDER BY c.title""",
                (user_id,),
            ).fetchall()

    def list_users_with_groups(self) -> list[Any]:
        with self.connection() as connection:
            users = self.execute(
                connection,
                """SELECT u.id, u.telegram_user_id, u.role, u.identity_id, i.display_name, i.class_name,
                              (SELECT c.status FROM identity_claims c WHERE c.user_id = u.id ORDER BY c.created_at DESC, c.id DESC LIMIT 1) AS claim_status
                     FROM users u LEFT JOIN identities i ON i.id = u.identity_id
                    ORDER BY u.created_at""",
            ).fetchall()
            role_rows = self.execute(connection, "SELECT user_id, role FROM user_roles ORDER BY role").fetchall()
            roles_by_user: dict[Any, list[str]] = {}
            for row in role_rows:
                roles_by_user.setdefault(row["user_id"], []).append(str(row["role"]))
            result = []
            for user in users:
                groups = self.execute(
                    connection,
                    """SELECT g.id, g.name, g.group_type, m.source
                         FROM memberships m JOIN groups g ON g.id = m.group_id
                        WHERE m.identity_id = ? AND m.active IS TRUE
                        ORDER BY g.name""",
                    (user["identity_id"],),
                ).fetchall() if user["identity_id"] else []
                roles = roles_by_user.get(user["id"], [str(user["role"])])
                result.append({**dict(user), "roles": roles, "groups": [dict(group) for group in groups]})
            return result

    def list_schedule_explanations_for_user(self, user_id: Any, lesson_date: str, end_date: str | None = None) -> list[dict[str, Any]]:
        entries = [dict(row) for row in self.list_schedule_entries_for_user(user_id, lesson_date, end_date)]
        with self.connection() as connection:
            memberships = [
                dict(row)
                for row in self.execute(
                    connection,
                    """SELECT g.name AS group_name, m.source, ga.audience, ga.subject,
                                      ga.subject_subgroup, ga.exam_track
                                 FROM memberships m
                                 JOIN groups g ON g.id = m.group_id
                                 JOIN group_schedule_audiences ga ON ga.group_id = m.group_id
                                 JOIN users u ON u.identity_id = m.identity_id
                                WHERE u.id = ? AND m.active IS TRUE
                                ORDER BY g.name, ga.subject, ga.subject_subgroup, ga.exam_track""",
                    (user_id,),
                ).fetchall()
            ]
        explained: list[dict[str, Any]] = []
        for entry in entries:
            matched: list[dict[str, Any]] = []
            for membership in memberships:
                if membership["audience"] != entry.get("audience"):
                    continue
                entry_subgroup = entry.get("subject_subgroup") or ""
                entry_track = entry.get("exam_track") or ""
                scope_subject = membership.get("subject") or ""
                if membership.get("subject_subgroup"):
                    if entry_subgroup != membership["subject_subgroup"] or (scope_subject and scope_subject != entry.get("subject")):
                        continue
                elif membership.get("exam_track"):
                    if entry_track != membership["exam_track"] or (scope_subject and scope_subject != entry.get("subject")):
                        continue
                elif entry_subgroup or entry_track:
                    continue
                matched.append({
                    "group_name": membership["group_name"],
                    "source": membership["source"],
                    "audience": membership["audience"],
                    "subject": scope_subject,
                    "subject_subgroup": membership.get("subject_subgroup") or "",
                    "exam_track": membership.get("exam_track") or "",
                })
            entry["matched_memberships"] = matched
            entry["inclusion_reason"] = "; ".join(
                f"{item['group_name']} ({item['source']})" for item in matched
            ) or "Нет совпавшей активной membership"
            explained.append(entry)
        return explained

    def list_schedule_parse_issues(self, audience: str | None = None) -> list[Any]:
        with self.connection() as connection:
            query = """SELECT lesson_date, start_time, subject, audience, parse_status, parse_diagnostics,
                              source_tab, source_coordinate, raw_source
                         FROM schedule_entries
                        WHERE parse_status IN ('partial', 'ambiguous', 'failed')"""
            params: tuple[Any, ...] = ()
            if audience:
                query += " AND audience = ?"
                params = (audience,)
            query += " ORDER BY lesson_date, start_time, source_coordinate"
            return self.execute(connection, query, params).fetchall()

    def list_schedule_syncs(self, limit: int = 20) -> list[Any]:
        with self.connection() as connection:
            return self.execute(connection, "SELECT id, status, source, source_hash, error, validation_problems, created_at FROM schedule_syncs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def list_classroom_sync_audit(self, limit: int = 20) -> list[Any]:
        with self.connection() as connection:
            return self.execute(connection, "SELECT action, entity_type, details, created_at FROM audit_log WHERE entity_type = 'classroom_sync' ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def record_audit_event(self, action: str, entity_type: str, details: Any | None = None, actor_user_id: Any | None = None) -> None:
        import json
        with self.connection() as connection:
            self.execute(
                connection,
                "INSERT INTO audit_log(actor_user_id, action, entity_type, details) VALUES (?, ?, ?, ?)",
                (actor_user_id, action, entity_type, json.dumps(details, ensure_ascii=False, sort_keys=True, default=str) if details is not None else None),
            )

    def mark_missing_classroom_coursework(self, course_id: Any, seen_external_ids: list[str]) -> None:
        with self.connection() as connection:
            if seen_external_ids:
                placeholders = ",".join("?" for _ in seen_external_ids)
                self.execute(connection, f"UPDATE classroom_coursework SET state = 'REMOVED' WHERE course_id = ? AND external_coursework_id NOT IN ({placeholders})", (course_id, *seen_external_ids))
            else:
                self.execute(connection, "UPDATE classroom_coursework SET state = 'REMOVED' WHERE course_id = ?", (course_id,))

    def list_classroom_coursework_for_course(self, external_course_id: str) -> list[Any]:
        with self.connection() as connection:
            return self.execute(
                connection,
                """SELECT cw.external_coursework_id, cw.title, cw.description, cw.due_at, cw.state,
                          cw.work_type, cw.max_points, cw.update_time, c.external_course_id, c.title AS course_title
                     FROM classroom_coursework cw
                     JOIN classroom_courses c ON c.id = cw.course_id
                    WHERE c.external_course_id = ?
                    ORDER BY cw.due_at NULLS LAST, cw.id DESC""",
                (external_course_id,),
            ).fetchall()

    def list_classroom_materials_for_coursework(self, external_coursework_id: str) -> list[Any]:
        with self.connection() as connection:
            return self.execute(
                connection,
                """SELECT m.external_material_key, m.source_type, m.title, m.material_type, m.url, m.drive_file_id
                     FROM classroom_coursework_materials m
                     JOIN classroom_coursework cw ON cw.id = m.coursework_id
                    WHERE cw.external_coursework_id = ?
                    ORDER BY m.id""",
                (external_coursework_id,),
            ).fetchall()

    def list_active_announcements(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return self.execute(connection, "SELECT id, title, body, audience, expires_at FROM announcements WHERE active IS TRUE ORDER BY id DESC").fetchall()

    def list_official_grades_for_user(self, user_id: Any) -> list[Any]:
        with self.connection() as connection:
            return self.execute(
                connection,
                """SELECT g.subject, g.graded_on, g.value, g.grade_type, g.weight, g.comment, g.source
                   FROM official_grades g
                   JOIN users u ON u.identity_id = g.identity_id
                  WHERE u.id = ? AND u.identity_id IS NOT NULL
                  ORDER BY g.graded_on DESC, g.id DESC""",
                (user_id,),
            ).fetchall()

    def upsert_classroom_course(self, course: dict[str, Any], teacher_account: str) -> Any:
        with self.connection() as connection:
            group = self.execute(
                connection,
                "INSERT INTO groups(name, group_type) VALUES (?, ?) ON CONFLICT(name, group_type) DO UPDATE SET name = excluded.name RETURNING id",
                (course["title"], "class"),
            ).fetchone()
            self.execute(
                connection,
                """INSERT INTO classroom_courses(external_course_id, group_id, title, teacher_account, section, description, room, update_time, raw_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(external_course_id) DO UPDATE SET group_id = excluded.group_id, title = excluded.title,
                     teacher_account = excluded.teacher_account, section = excluded.section, description = excluded.description,
                     room = excluded.room, update_time = excluded.update_time, raw_source = excluded.raw_source""",
                (course["external_id"], group["id"], course["title"], teacher_account, course.get("section"), course.get("description"), course.get("room"), course.get("update_time"), course.get("raw_source")),
            )
            return self.execute(connection, "SELECT * FROM classroom_courses WHERE external_course_id = ?", (course["external_id"],)).fetchone()

    def upsert_classroom_coursework(self, coursework: dict[str, Any], course_id: Any) -> Any:
        with self.connection() as connection:
            self.execute(
                connection,
                """INSERT INTO classroom_coursework(course_id, external_coursework_id, title, description, due_at, alternate_link, state, work_type, max_points, update_time, raw_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(external_coursework_id) DO UPDATE SET course_id = excluded.course_id, title = excluded.title,
                     description = excluded.description, due_at = excluded.due_at, alternate_link = excluded.alternate_link,
                     state = excluded.state, work_type = excluded.work_type, max_points = excluded.max_points,
                     update_time = excluded.update_time, raw_source = excluded.raw_source""",
                (course_id, coursework["external_id"], coursework["title"], coursework.get("description", ""), coursework.get("due_at"), coursework.get("alternate_link"), coursework.get("state"), coursework.get("work_type"), coursework.get("max_points"), coursework.get("update_time"), coursework.get("raw_source")),
            )
            return self.execute(connection, "SELECT * FROM classroom_coursework WHERE external_coursework_id = ?", (coursework["external_id"],)).fetchone()

    def upsert_classroom_material(self, material: dict[str, Any], coursework_id: Any | None = None, course_id: Any | None = None) -> Any:
        with self.connection() as connection:
            self.execute(
                connection,
                """INSERT INTO classroom_coursework_materials(course_id, coursework_id, external_material_key, source_type, title, material_type, url, drive_file_id, raw_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(external_material_key) DO UPDATE SET course_id = excluded.course_id, coursework_id = excluded.coursework_id, source_type = excluded.source_type,
                     title = excluded.title, material_type = excluded.material_type, url = excluded.url,
                     drive_file_id = excluded.drive_file_id, raw_source = excluded.raw_source, updated_at = CURRENT_TIMESTAMP""",
                (course_id, coursework_id, material["external_material_key"], material["source_type"], material.get("title", ""), material.get("material_type", ""), material.get("url"), material.get("drive_file_id"), material.get("raw_source")),
            )
            return self.execute(connection, "SELECT * FROM classroom_coursework_materials WHERE external_material_key = ?", (material["external_material_key"],)).fetchone()

    def upsert_classroom_submission(self, submission: dict[str, Any], coursework_id: Any) -> Any:
        with self.connection() as connection:
            self.execute(
                connection,
                """INSERT INTO classroom_student_submissions(coursework_id, identity_id, external_submission_id, external_student_id, state, assigned_grade, draft_grade, late, raw_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(external_submission_id) DO UPDATE SET coursework_id = excluded.coursework_id,
                     identity_id = excluded.identity_id, external_student_id = excluded.external_student_id, state = excluded.state,
                     assigned_grade = excluded.assigned_grade, draft_grade = excluded.draft_grade, late = excluded.late,
                     raw_source = excluded.raw_source, updated_at = CURRENT_TIMESTAMP""",
                (coursework_id, submission.get("identity_id"), submission["external_id"], submission.get("external_student_id"), submission["state"], submission.get("assigned_grade"), submission.get("draft_grade"), submission.get("late"), submission.get("raw_source")),
            )
            return self.execute(connection, "SELECT * FROM classroom_student_submissions WHERE external_submission_id = ?", (submission["external_id"],)).fetchone()

    def list_classroom_coursework_for_user(self, user_id: Any) -> list[Any]:
        with self.connection() as connection:
            return self.execute(
                connection,
                """SELECT cw.external_coursework_id, cw.title, cw.description, cw.due_at, cw.alternate_link, cw.state,
                          cw.work_type, cw.max_points, cw.update_time, c.external_course_id, c.title AS course_title
                     FROM classroom_coursework cw
                     JOIN classroom_courses c ON c.id = cw.course_id
                     JOIN memberships m ON m.group_id = c.group_id AND m.active IS TRUE
                     JOIN users u ON u.identity_id = m.identity_id
                    WHERE u.id = ?
                    ORDER BY cw.due_at NULLS LAST, cw.id DESC""",
                (user_id,),
            ).fetchall()

    def list_classroom_submissions_for_user(self, user_id: Any) -> list[Any]:
        with self.connection() as connection:
            return self.execute(
                connection,
                """SELECT s.external_submission_id, s.external_student_id, s.state, s.assigned_grade, s.draft_grade, s.late,
                          cw.external_coursework_id, cw.title
                     FROM classroom_student_submissions s
                     JOIN classroom_coursework cw ON cw.id = s.coursework_id
                     JOIN users u ON u.identity_id = s.identity_id
                    WHERE u.id = ?
                    ORDER BY s.updated_at DESC""",
                (user_id,),
            ).fetchall()

    def create_identity(self, kind: str, display_name: str, class_name: str | None = None) -> Any:
        with self.connection() as connection:
            inserted = self.execute(
                connection,
                "INSERT INTO identities(kind, display_name, class_name) VALUES (?, ?, ?) RETURNING id",
                (kind, display_name, class_name),
            ).fetchone()
            return self.execute(connection, "SELECT * FROM identities WHERE id = ?", (inserted["id"],)).fetchone()

    def create_group(self, name: str, group_type: str) -> Any:
        with self.connection() as connection:
            row = self.execute(
                connection,
                "INSERT INTO groups(name, group_type) VALUES (?, ?) ON CONFLICT(name, group_type) DO UPDATE SET name = excluded.name RETURNING id",
                (name, group_type),
            ).fetchone()
            return self.execute(connection, "SELECT * FROM groups WHERE id = ?", (row["id"],)).fetchone()

    def create_membership(self, group_id: Any, identity_id: Any, member_role: str, source: str) -> Any:
        with self.connection() as connection:
            row = self.execute(
                connection,
                """INSERT INTO memberships(group_id, identity_id, member_role, source, active)
                   VALUES (?, ?, ?, ?, TRUE)
                   ON CONFLICT(group_id, identity_id, source) DO UPDATE SET active = TRUE
                   RETURNING id""",
                (group_id, identity_id, member_role, source),
            ).fetchone()
            return self.execute(connection, "SELECT * FROM memberships WHERE id = ?", (row["id"],)).fetchone()

    def map_group_schedule_audience(
        self,
        group_id: Any,
        audience: str,
        *,
        subject: str = "",
        subject_subgroup: str = "",
        exam_track: str = "",
    ) -> Any:
        with self.connection() as connection:
            row = self.execute(
                connection,
                """INSERT INTO group_schedule_audiences(group_id, audience, subject, subject_subgroup, exam_track)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(group_id, audience, subject, subject_subgroup, exam_track)
                   DO UPDATE SET audience = excluded.audience
                   RETURNING id""",
                (group_id, audience, subject, subject_subgroup, exam_track),
            ).fetchone()
            return self.execute(connection, "SELECT * FROM group_schedule_audiences WHERE id = ?", (row["id"],)).fetchone()
