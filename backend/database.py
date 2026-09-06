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
  audience_kind TEXT NOT NULL DEFAULT 'all',
  audience_ref TEXT,
  publish_at TEXT,
  starts_at TEXT,
  pinned INTEGER NOT NULL DEFAULT 0,
  author_user_id INTEGER REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'published',
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
CREATE TABLE IF NOT EXISTS teacher_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  teacher_identity_id INTEGER NOT NULL REFERENCES identities(id),
  group_id INTEGER NOT NULL REFERENCES groups(id),
  subject TEXT NOT NULL DEFAULT '',
  base_class_name TEXT,
  subject_subgroup TEXT,
  classroom_course_id INTEGER REFERENCES classroom_courses(id),
  exam_track TEXT,
  capability TEXT NOT NULL DEFAULT 'teach',
  source TEXT NOT NULL DEFAULT 'admin_override',
  source_ref TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT,
  valid_until TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(teacher_identity_id, group_id, subject, capability, source)
);
CREATE TABLE IF NOT EXISTS homeroom_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  teacher_identity_id INTEGER NOT NULL REFERENCES identities(id),
  class_group_id INTEGER NOT NULL REFERENCES groups(id),
  source TEXT NOT NULL DEFAULT 'admin_override',
  source_ref TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT,
  valid_until TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(teacher_identity_id, class_group_id, source)
);
CREATE TABLE IF NOT EXISTS membership_overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  identity_id INTEGER NOT NULL REFERENCES identities(id),
  group_id INTEGER NOT NULL REFERENCES groups(id),
  member_role TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('include', 'exclude')),
  reason TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT,
  valid_until TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS journal_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  spreadsheet_id TEXT NOT NULL,
  spreadsheet_title TEXT NOT NULL,
  grade INTEGER NOT NULL,
  subject TEXT NOT NULL,
  sheet_title TEXT NOT NULL,
  source_hash TEXT,
  status TEXT NOT NULL DEFAULT 'ready',
  last_synced_at TEXT,
  last_error TEXT,
  UNIQUE(spreadsheet_id, sheet_title)
);
CREATE TABLE IF NOT EXISTS journal_assessments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES journal_sources(id) ON DELETE CASCADE,
  external_key TEXT NOT NULL,
  title TEXT NOT NULL,
  assessed_on TEXT,
  weight REAL,
  max_score REAL,
  source_column TEXT NOT NULL,
  raw_source TEXT,
  UNIQUE(source_id, external_key)
);
CREATE TABLE IF NOT EXISTS journal_students (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES journal_sources(id) ON DELETE CASCADE,
  student_key TEXT NOT NULL,
  display_name TEXT NOT NULL,
  group_marker TEXT NOT NULL,
  identity_id INTEGER REFERENCES identities(id),
  match_status TEXT NOT NULL DEFAULT 'unlinked' CHECK (match_status IN ('unlinked','linked','ambiguous')),
  match_method TEXT,
  source_row INTEGER,
  raw_source TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_id, group_marker, student_key)
);
CREATE TABLE IF NOT EXISTS journal_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  assessment_id INTEGER NOT NULL REFERENCES journal_assessments(id) ON DELETE CASCADE,
  journal_student_id INTEGER REFERENCES journal_students(id) ON DELETE SET NULL,
  identity_id INTEGER REFERENCES identities(id),
  student_name TEXT NOT NULL,
  group_marker TEXT NOT NULL,
  numeric_score REAL,
  status TEXT,
  source_coordinate TEXT NOT NULL,
  raw_source TEXT,
  UNIQUE(assessment_id, source_coordinate)
);
CREATE TABLE IF NOT EXISTS journal_group_mappings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES journal_sources(id) ON DELETE CASCADE,
  group_marker TEXT NOT NULL,
  group_id INTEGER REFERENCES groups(id),
  base_class_name TEXT,
  subject_subgroup TEXT,
  classroom_course_id INTEGER REFERENCES classroom_courses(id),
  exam_track TEXT,
  source TEXT NOT NULL DEFAULT 'admin_override',
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_id, group_marker)
);
CREATE INDEX IF NOT EXISTS teacher_assignments_teacher_idx ON teacher_assignments(teacher_identity_id, active);
CREATE INDEX IF NOT EXISTS teacher_assignments_group_idx ON teacher_assignments(group_id, active);
CREATE INDEX IF NOT EXISTS homeroom_assignments_teacher_idx ON homeroom_assignments(teacher_identity_id, active);
CREATE INDEX IF NOT EXISTS homeroom_assignments_group_idx ON homeroom_assignments(class_group_id, active);
CREATE INDEX IF NOT EXISTS membership_overrides_subject_idx ON membership_overrides(identity_id, group_id, active);
CREATE INDEX IF NOT EXISTS membership_overrides_group_idx ON membership_overrides(group_id, active);
CREATE INDEX IF NOT EXISTS journal_results_identity_idx ON journal_results(identity_id);
CREATE INDEX IF NOT EXISTS journal_results_student_idx ON journal_results(journal_student_id);
CREATE INDEX IF NOT EXISTS journal_results_group_idx ON journal_results(group_marker);
CREATE INDEX IF NOT EXISTS journal_students_source_marker_idx ON journal_students(source_id, group_marker);
CREATE INDEX IF NOT EXISTS journal_students_identity_idx ON journal_students(identity_id);
CREATE INDEX IF NOT EXISTS journal_group_mappings_group_idx ON journal_group_mappings(group_id);
CREATE INDEX IF NOT EXISTS journal_group_mappings_dimensions_idx ON journal_group_mappings(source_id, subject_subgroup, base_class_name, exam_track);
CREATE INDEX IF NOT EXISTS announcements_author_idx ON announcements(author_user_id);
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
            announcement_columns = {row["name"] for row in connection.execute("PRAGMA table_info(announcements)").fetchall()}
            for name, definition in {
                "audience_kind": "TEXT NOT NULL DEFAULT 'all'",
                "audience_ref": "TEXT",
                "publish_at": "TEXT",
                "starts_at": "TEXT",
                "pinned": "INTEGER NOT NULL DEFAULT 0",
                "author_user_id": "INTEGER REFERENCES users(id)",
                "status": "TEXT NOT NULL DEFAULT 'published'",
            }.items():
                if name not in announcement_columns:
                    connection.execute(f"ALTER TABLE announcements ADD COLUMN {name} {definition}")
            result_columns = {row["name"] for row in connection.execute("PRAGMA table_info(journal_results)").fetchall()}
            if "journal_student_id" not in result_columns:
                connection.execute("ALTER TABLE journal_results ADD COLUMN journal_student_id INTEGER REFERENCES journal_students(id) ON DELETE SET NULL")
            mapping_columns = {row["name"] for row in connection.execute("PRAGMA table_info(journal_group_mappings)").fetchall()}
            for name, definition in {
                "base_class_name": "TEXT",
                "subject_subgroup": "TEXT",
                "classroom_course_id": "INTEGER REFERENCES classroom_courses(id)",
                "exam_track": "TEXT",
            }.items():
                if name not in mapping_columns:
                    connection.execute(f"ALTER TABLE journal_group_mappings ADD COLUMN {name} {definition}")
            assignment_columns = {row["name"] for row in connection.execute("PRAGMA table_info(teacher_assignments)").fetchall()}
            for name, definition in {
                "base_class_name": "TEXT",
                "subject_subgroup": "TEXT",
                "classroom_course_id": "INTEGER REFERENCES classroom_courses(id)",
                "exam_track": "TEXT",
            }.items():
                if name not in assignment_columns:
                    connection.execute(f"ALTER TABLE teacher_assignments ADD COLUMN {name} {definition}")
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
                      AND NOT EXISTS (
                        SELECT 1 FROM membership_overrides mo
                         WHERE mo.identity_id = m.identity_id AND mo.group_id = m.group_id
                           AND mo.member_role = m.member_role AND mo.action = 'exclude' AND mo.active IS TRUE
                           AND (mo.valid_from IS NULL OR mo.valid_from <= se.lesson_date)
                           AND (mo.valid_until IS NULL OR mo.valid_until >= se.lesson_date)
                      )
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
                      AND NOT EXISTS (
                        SELECT 1 FROM membership_overrides mo
                         WHERE mo.identity_id = m.identity_id AND mo.group_id = m.group_id
                           AND mo.member_role = m.member_role AND mo.action = 'exclude' AND mo.active IS TRUE
                           AND (mo.valid_from IS NULL OR mo.valid_from <= CURRENT_DATE)
                           AND (mo.valid_until IS NULL OR mo.valid_until >= CURRENT_DATE)
                      )
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
                teacher_assignments = self.execute(
                    connection,
                    """SELECT ta.id, ta.group_id, g.name AS group_name, ta.subject, ta.capability, ta.source, ta.active
                         FROM teacher_assignments ta JOIN groups g ON g.id = ta.group_id
                        WHERE ta.teacher_identity_id = ? ORDER BY g.name, ta.subject""",
                    (user["identity_id"],),
                ).fetchall() if user["identity_id"] else []
                homeroom = self.execute(
                    connection,
                    """SELECT h.id, h.class_group_id AS group_id, g.name AS group_name, h.source, h.active
                         FROM homeroom_assignments h JOIN groups g ON g.id = h.class_group_id
                        WHERE h.teacher_identity_id = ? ORDER BY g.name""",
                    (user["identity_id"],),
                ).fetchall() if user["identity_id"] else []
                result.append({
                    **dict(user), "roles": roles, "groups": [dict(group) for group in groups],
                    "teacher_assignments": [dict(item) for item in teacher_assignments],
                    "homeroom_assignments": [dict(item) for item in homeroom],
                })
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

    def list_active_announcements(self, user_id: Any | None = None, role: str | None = None) -> list[Any]:
        with self.connection() as connection:
            visibility = ""
            params: tuple[Any, ...] = ()
            if user_id is not None and role:
                visibility = """AND (
                    a.audience_kind = 'all' OR a.audience_kind = ?
                    OR (a.audience_kind = 'group' AND EXISTS (
                        SELECT 1 FROM memberships m JOIN users u ON u.identity_id = m.identity_id
                         WHERE u.id = ? AND m.active IS TRUE AND CAST(m.group_id AS TEXT) = a.audience_ref
                    ))
                )"""
                params = (role, user_id)
            return self.execute(
                connection,
                f"""SELECT a.id, a.title, a.body, a.audience, a.audience_kind, a.audience_ref,
                           a.publish_at, a.starts_at, a.expires_at, a.pinned, a.status,
                           i.display_name AS author_name
                      FROM announcements a
                      LEFT JOIN users au ON au.id = a.author_user_id
                      LEFT JOIN identities i ON i.id = au.identity_id
                     WHERE a.active IS TRUE AND a.status = 'published'
                       AND (a.publish_at IS NULL OR a.publish_at <= CURRENT_TIMESTAMP)
                       AND (a.starts_at IS NULL OR a.starts_at <= CURRENT_TIMESTAMP)
                       AND (a.expires_at IS NULL OR a.expires_at >= CURRENT_TIMESTAMP)
                       {visibility}
                     ORDER BY a.pinned DESC, COALESCE(a.publish_at, a.starts_at) DESC, a.id DESC""",
                params,
            ).fetchall()

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

    def get_classroom_course(self, course_id: Any) -> Any | None:
        with self.connection() as connection:
            return self.execute(connection, "SELECT * FROM classroom_courses WHERE id = ?", (course_id,)).fetchone()

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

    def get_identity(self, identity_id: Any) -> Any | None:
        with self.connection() as connection:
            return self.execute(connection, "SELECT * FROM identities WHERE id = ?", (identity_id,)).fetchone()

    def get_group(self, group_id: Any) -> Any | None:
        with self.connection() as connection:
            return self.execute(connection, "SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()

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

    def list_groups_admin(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self.execute(
                connection,
                """SELECT g.id, g.name, g.group_type,
                          COUNT(DISTINCT CASE WHEN m.active IS TRUE THEN m.identity_id END) AS member_count,
                          COUNT(DISTINCT CASE WHEN ta.active IS TRUE THEN ta.teacher_identity_id END) AS teacher_count
                     FROM groups g
                     LEFT JOIN memberships m ON m.group_id = g.id
                     LEFT JOIN teacher_assignments ta ON ta.group_id = g.id
                    GROUP BY g.id, g.name, g.group_type
                    ORDER BY g.group_type, g.name""",
            ).fetchall()
            return [dict(row) for row in rows]

    def set_membership_override(self, identity_id: Any, group_id: Any, member_role: str, action: str, actor_user_id: Any, reason: str = "") -> Any:
        with self.connection() as connection:
            self.execute(
                connection,
                "UPDATE membership_overrides SET active = FALSE WHERE identity_id = ? AND group_id = ? AND member_role = ? AND active IS TRUE",
                (identity_id, group_id, member_role),
            )
            row = self.execute(
                connection,
                """INSERT INTO membership_overrides(identity_id, group_id, member_role, action, reason, created_by)
                   VALUES (?, ?, ?, ?, ?, ?) RETURNING id""",
                (identity_id, group_id, member_role, action, reason, actor_user_id),
            ).fetchone()
            if action == "include":
                self.execute(
                    connection,
                    """INSERT INTO memberships(group_id, identity_id, member_role, source, active)
                       VALUES (?, ?, ?, 'admin_override', TRUE)
                       ON CONFLICT(group_id, identity_id, source) DO UPDATE SET member_role = excluded.member_role, active = TRUE""",
                    (group_id, identity_id, member_role),
                )
            else:
                self.execute(
                    connection,
                    "UPDATE memberships SET active = FALSE WHERE group_id = ? AND identity_id = ? AND source = 'admin_override'",
                    (group_id, identity_id),
                )
            return self.execute(connection, "SELECT * FROM membership_overrides WHERE id = ?", (row["id"],)).fetchone()

    def set_teacher_assignment(self, teacher_identity_id: Any, group_id: Any, subject: str, active: bool, actor_user_id: Any, *, base_class_name: str | None = None, subject_subgroup: str | None = None, classroom_course_id: Any | None = None, exam_track: str | None = None) -> Any:
        with self.connection() as connection:
            row = self.execute(
                connection,
                """INSERT INTO teacher_assignments(teacher_identity_id, group_id, subject, base_class_name, subject_subgroup, classroom_course_id, exam_track, source, active, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'admin_override', ?, ?)
                   ON CONFLICT(teacher_identity_id, group_id, subject, capability, source)
                   DO UPDATE SET base_class_name = excluded.base_class_name, subject_subgroup = excluded.subject_subgroup,
                     classroom_course_id = excluded.classroom_course_id, exam_track = excluded.exam_track,
                     active = excluded.active, created_by = excluded.created_by
                   RETURNING id""",
                (teacher_identity_id, group_id, subject.strip(), base_class_name, subject_subgroup, classroom_course_id, exam_track, active, actor_user_id),
            ).fetchone()
            return self.execute(connection, "SELECT * FROM teacher_assignments WHERE id = ?", (row["id"],)).fetchone()

    def set_homeroom_assignment(self, teacher_identity_id: Any, group_id: Any, active: bool, actor_user_id: Any) -> Any:
        with self.connection() as connection:
            row = self.execute(
                connection,
                """INSERT INTO homeroom_assignments(teacher_identity_id, class_group_id, source, active, created_by)
                   VALUES (?, ?, 'admin_override', ?, ?)
                   ON CONFLICT(teacher_identity_id, class_group_id, source)
                   DO UPDATE SET active = excluded.active, created_by = excluded.created_by
                   RETURNING id""",
                (teacher_identity_id, group_id, active, actor_user_id),
            ).fetchone()
            return self.execute(connection, "SELECT * FROM homeroom_assignments WHERE id = ?", (row["id"],)).fetchone()

    def teacher_can_access_group(self, user_id: Any, group_id: Any) -> bool:
        with self.connection() as connection:
            row = self.execute(
                connection,
                """SELECT 1 FROM users u
                     WHERE u.id = ? AND u.identity_id IS NOT NULL AND (
                       EXISTS (SELECT 1 FROM teacher_assignments ta WHERE ta.teacher_identity_id = u.identity_id AND ta.group_id = ? AND ta.active IS TRUE)
                       OR EXISTS (SELECT 1 FROM memberships m WHERE m.identity_id = u.identity_id AND m.group_id = ? AND m.member_role = 'teacher' AND m.active IS TRUE)
                       OR EXISTS (SELECT 1 FROM homeroom_assignments h WHERE h.teacher_identity_id = u.identity_id AND h.class_group_id = ? AND h.active IS TRUE)
                     )""",
                (user_id, group_id, group_id, group_id),
            ).fetchone()
            return bool(row)

    def list_teacher_groups(self, user_id: Any) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self.execute(
                connection,
                """SELECT DISTINCT g.id, g.name, g.group_type, COALESCE(ta.subject, '') AS subject,
                          COALESCE(ta.base_class_name, '') AS base_class_name,
                          COALESCE(ta.subject_subgroup, '') AS subject_subgroup,
                          COALESCE(ta.classroom_course_id, '') AS classroom_course_id,
                          COALESCE(ta.exam_track, '') AS exam_track,
                          CASE WHEN h.id IS NULL THEN FALSE ELSE TRUE END AS is_homeroom,
                          COUNT(DISTINCT CASE WHEN sm.active IS TRUE AND sm.member_role = 'student' THEN sm.identity_id END) AS student_count
                     FROM users u
                     JOIN groups g ON (
                       EXISTS (SELECT 1 FROM teacher_assignments x WHERE x.teacher_identity_id = u.identity_id AND x.group_id = g.id AND x.active IS TRUE)
                       OR EXISTS (SELECT 1 FROM memberships tm WHERE tm.identity_id = u.identity_id AND tm.group_id = g.id AND tm.member_role = 'teacher' AND tm.active IS TRUE)
                       OR EXISTS (SELECT 1 FROM homeroom_assignments hx WHERE hx.teacher_identity_id = u.identity_id AND hx.class_group_id = g.id AND hx.active IS TRUE)
                     )
                     LEFT JOIN teacher_assignments ta ON ta.teacher_identity_id = u.identity_id AND ta.group_id = g.id AND ta.active IS TRUE
                     LEFT JOIN homeroom_assignments h ON h.teacher_identity_id = u.identity_id AND h.class_group_id = g.id AND h.active IS TRUE
                     LEFT JOIN memberships sm ON sm.group_id = g.id
                    WHERE u.id = ?
                    GROUP BY g.id, g.name, g.group_type, ta.subject, ta.base_class_name, ta.subject_subgroup, ta.classroom_course_id, ta.exam_track, h.id
                    ORDER BY is_homeroom DESC, g.name""",
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_group_students(self, group_id: Any) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self.execute(
                connection,
                """SELECT i.id, i.display_name, i.class_name, m.source
                     FROM memberships m JOIN identities i ON i.id = m.identity_id
                    WHERE m.group_id = ? AND m.member_role = 'student' AND m.active IS TRUE
                      AND NOT EXISTS (SELECT 1 FROM membership_overrides mo WHERE mo.identity_id = i.id AND mo.group_id = m.group_id AND mo.member_role = 'student' AND mo.action = 'exclude' AND mo.active IS TRUE)
                    ORDER BY i.display_name""",
                (group_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_teacher_schedule(self, user_id: Any, start_day: str, end_day: str | None = None) -> list[Any]:
        with self.connection() as connection:
            date_range = "se.lesson_date BETWEEN ?::date AND COALESCE(?::date, ?::date)" if self.database_url else "se.lesson_date BETWEEN ? AND COALESCE(?, ?)"
            return self.execute(
                connection,
                f"""SELECT DISTINCT se.lesson_date, se.start_time, se.end_time, se.subject, se.teacher, se.room,
                          se.audience, se.subject_subgroup, se.exam_track, se.lesson_type, se.delivery_mode, se.source_coordinate
                     FROM users u
                     JOIN teacher_assignments ta ON ta.teacher_identity_id = u.identity_id AND ta.active IS TRUE
                     JOIN group_schedule_audiences ga ON ga.group_id = ta.group_id
                     JOIN schedule_entries se ON se.audience = ga.audience
                    WHERE u.id = ? AND {date_range} AND (ta.subject = '' OR ta.subject = se.subject)
                    ORDER BY se.lesson_date, se.start_time, se.subject""",
                (user_id, start_day, end_day, start_day),
            ).fetchall()

    def create_announcement(self, values: dict[str, Any], actor_user_id: Any) -> Any:
        with self.connection() as connection:
            row = self.execute(
                connection,
                """INSERT INTO announcements(title, body, audience, audience_kind, audience_ref, publish_at, starts_at, expires_at, pinned, author_user_id, status, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE) RETURNING id""",
                (values["title"], values["content"], values.get("audience_kind"), values.get("audience_kind", "all"), values.get("audience_ref"), values.get("publish_at"), values.get("starts_at"), values.get("ends_at"), bool(values.get("pinned")), actor_user_id, values.get("status", "draft")),
            ).fetchone()
            return self.execute(connection, "SELECT * FROM announcements WHERE id = ?", (row["id"],)).fetchone()

    def list_announcements_admin(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return [dict(row) for row in self.execute(connection, "SELECT id, title, body, audience_kind, audience_ref, publish_at, starts_at, expires_at, pinned, status, active FROM announcements ORDER BY id DESC").fetchall()]

    def list_audit_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self.execute(
                connection,
                """SELECT a.id, a.action, a.entity_type, a.entity_id, a.details, a.created_at,
                          COALESCE(i.display_name, 'System') AS actor_name
                     FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id LEFT JOIN identities i ON i.id = u.identity_id
                    ORDER BY a.created_at DESC, a.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def admin_overview(self) -> dict[str, int]:
        with self.connection() as connection:
            queries = {
                "pending_claims": "SELECT COUNT(*) AS value FROM identity_claims WHERE status = 'pending'",
                "users": "SELECT COUNT(*) AS value FROM users",
                "groups": "SELECT COUNT(*) AS value FROM groups",
                "parse_issues": "SELECT COUNT(*) AS value FROM schedule_entries WHERE parse_status IN ('partial','ambiguous','failed')",
                "journal_results": "SELECT COUNT(*) AS value FROM journal_results",
            }
            return {name: int(self.execute(connection, sql).fetchone()["value"]) for name, sql in queries.items()}

    def save_journal_snapshot(self, source: dict[str, Any], assessments: list[dict[str, Any]], results: list[dict[str, Any]], students: list[dict[str, Any]] | None = None) -> dict[str, int]:
        import json
        with self.connection() as connection:
            source_row = self.execute(
                connection,
                """INSERT INTO journal_sources(spreadsheet_id, spreadsheet_title, grade, subject, sheet_title, source_hash, status, last_synced_at, last_error)
                   VALUES (?, ?, ?, ?, ?, ?, 'ready', CURRENT_TIMESTAMP, NULL)
                   ON CONFLICT(spreadsheet_id, sheet_title) DO UPDATE SET spreadsheet_title = excluded.spreadsheet_title,
                     grade = excluded.grade, subject = excluded.subject, source_hash = excluded.source_hash,
                     status = 'ready', last_synced_at = CURRENT_TIMESTAMP, last_error = NULL
                   RETURNING id""",
                (source["spreadsheet_id"], source["spreadsheet_title"], source["grade"], source["subject"], source["sheet_title"], source["source_hash"]),
            ).fetchone()
            source_id = source_row["id"]
            self.execute(connection, "DELETE FROM journal_assessments WHERE source_id = ?", (source_id,))
            assessment_ids: dict[str, Any] = {}
            raw_value = "?::jsonb" if self.database_url else "?"
            for item in assessments:
                row = self.execute(
                    connection,
                    f"""INSERT INTO journal_assessments(source_id, external_key, title, assessed_on, weight, max_score, source_column, raw_source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, {raw_value}) RETURNING id""",
                    (source_id, item["external_key"], item["title"], item.get("assessed_on"), item.get("weight"), item.get("max_score"), item["source_column"], json.dumps(item.get("raw_source"), ensure_ascii=False)),
                ).fetchone()
                assessment_ids[item["external_key"]] = row["id"]
            identities = self.execute(connection, "SELECT id, display_name FROM identities WHERE kind = 'student' AND status = 'active'").fetchall()
            by_name: dict[str, list[Any]] = {}
            for identity in identities:
                by_name.setdefault(" ".join(str(identity["display_name"]).casefold().split()), []).append(identity["id"])
            roster: dict[tuple[str, str], dict[str, Any]] = {}
            for item in students or []:
                student_key = item.get("student_key") or " ".join(str(item["student_name"]).casefold().split())
                roster.setdefault((item["group_marker"], student_key), item)
            for item in results:
                student_key = item.get("student_key") or " ".join(str(item["student_name"]).casefold().split())
                roster.setdefault((item["group_marker"], student_key), item)
            student_ids: dict[tuple[str, str], Any] = {}
            linked_students = 0
            for (group_marker, student_key), item in roster.items():
                matches = by_name.get(" ".join(str(item["student_name"]).casefold().split()), [])
                identity_id = matches[0] if len(matches) == 1 else None
                match_status = "linked" if len(matches) == 1 else "ambiguous" if len(matches) > 1 else "unlinked"
                match_method = "canonical_name" if len(matches) == 1 else None
                raw_student = json.dumps(item.get("raw_source"), ensure_ascii=False)
                row = self.execute(
                    connection,
                    f"""INSERT INTO journal_students(source_id, student_key, display_name, group_marker, identity_id, match_status, match_method, source_row, raw_source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, {raw_value})
                       ON CONFLICT(source_id, group_marker, student_key) DO UPDATE SET
                         display_name = excluded.display_name,
                         identity_id = COALESCE(journal_students.identity_id, excluded.identity_id),
                         match_status = CASE WHEN COALESCE(journal_students.identity_id, excluded.identity_id) IS NULL THEN excluded.match_status ELSE 'linked' END,
                         match_method = CASE WHEN COALESCE(journal_students.identity_id, excluded.identity_id) IS NULL THEN excluded.match_method ELSE COALESCE(journal_students.match_method, excluded.match_method) END,
                         source_row = excluded.source_row,
                         raw_source = excluded.raw_source,
                         updated_at = CURRENT_TIMESTAMP
                       RETURNING id, identity_id""",
                    (source_id, student_key, item["student_name"], group_marker, identity_id, match_status, match_method, item.get("source_row") or item.get("raw_source", {}).get("student_row"), raw_student),
                ).fetchone()
                student_ids[(group_marker, student_key)] = row["id"]
                linked_students += int(row["identity_id"] is not None)
            if roster:
                self.execute(
                    connection,
                    f"DELETE FROM journal_students WHERE source_id = ? AND (group_marker, student_key) NOT IN ({','.join('(?, ?)' for _ in roster)})",
                    (source_id, *[value for pair in roster for value in pair]),
                )
            mapped = 0
            for item in results:
                student_key = item.get("student_key") or " ".join(str(item["student_name"]).casefold().split())
                student_id = student_ids[(item["group_marker"], student_key)]
                student = self.execute(connection, "SELECT identity_id FROM journal_students WHERE id = ?", (student_id,)).fetchone()
                identity_id = student["identity_id"] if student else None
                mapped += int(identity_id is not None)
                self.execute(
                    connection,
                    f"""INSERT INTO journal_results(assessment_id, journal_student_id, identity_id, student_name, group_marker, numeric_score, status, source_coordinate, raw_source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, {raw_value})""",
                    (assessment_ids[item["assessment_key"]], student_id, identity_id, item["student_name"], item["group_marker"], item.get("numeric_score"), item.get("status"), item["source_coordinate"], json.dumps(item.get("raw_source"), ensure_ascii=False)),
                )
            return {"assessments": len(assessments), "results": len(results), "roster_students": len(roster), "linked_students": linked_students, "mapped_results": mapped}

    def map_journal_group(self, source_id: Any, group_marker: str, group_id: Any | None, actor_user_id: Any, *, base_class_name: str | None = None, subject_subgroup: str | None = None, classroom_course_id: Any | None = None, exam_track: str | None = None, source: str = "admin_override") -> Any:
        with self.connection() as connection:
            row = self.execute(
                connection,
                """INSERT INTO journal_group_mappings(source_id, group_marker, group_id, base_class_name, subject_subgroup, classroom_course_id, exam_track, source, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source_id, group_marker) DO UPDATE SET
                     group_id = excluded.group_id, base_class_name = excluded.base_class_name,
                     subject_subgroup = excluded.subject_subgroup, classroom_course_id = excluded.classroom_course_id,
                     exam_track = excluded.exam_track, source = excluded.source, created_by = excluded.created_by
                   RETURNING id""",
                (source_id, group_marker, group_id, base_class_name, subject_subgroup, classroom_course_id, exam_track, source, actor_user_id),
            ).fetchone()
            return self.execute(connection, "SELECT * FROM journal_group_mappings WHERE id = ?", (row["id"],)).fetchone()

    def journal_group_marker_exists(self, source_id: Any, group_marker: str) -> bool:
        with self.connection() as connection:
            row = self.execute(
                connection,
                """SELECT 1
                     FROM journal_assessments ja
                     JOIN journal_results jr ON jr.assessment_id = ja.id
                    WHERE ja.source_id = ? AND jr.group_marker = ?
                    LIMIT 1""",
                (source_id, group_marker),
            ).fetchone()
            return bool(row)

    def list_group_journal(self, group_id: Any) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self.execute(
                connection,
                """SELECT ja.id AS assessment_id, ja.title, ja.assessed_on, ja.weight, ja.max_score,
                          js2.id AS journal_student_id, COALESCE(jr.identity_id, js2.identity_id) AS identity_id,
                          js2.display_name AS student_name, jr.numeric_score, jr.status, jr.source_coordinate,
                          js.subject, js.sheet_title, gm.group_marker, gm.base_class_name,
                          gm.subject_subgroup, gm.classroom_course_id, gm.exam_track
                     FROM journal_group_mappings gm
                     JOIN journal_sources js ON js.id = gm.source_id
                      JOIN journal_assessments ja ON ja.source_id = js.id
                      JOIN journal_students js2 ON js2.source_id = js.id AND js2.group_marker = gm.group_marker
                      LEFT JOIN journal_results jr ON jr.assessment_id = ja.id AND jr.journal_student_id = js2.id
                    WHERE gm.group_id = ?
                    ORDER BY ja.assessed_on, ja.source_column, jr.student_name""",
                (group_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_journal_marker(self, source_id: Any, group_marker: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self.execute(
                connection,
                """SELECT ja.id AS assessment_id, ja.title, ja.assessed_on, ja.weight, ja.max_score,
                          js2.id AS journal_student_id, COALESCE(jr.identity_id, js2.identity_id) AS identity_id,
                          js2.display_name AS student_name, jr.numeric_score, jr.status, jr.source_coordinate,
                          source.subject, source.sheet_title, gm.group_marker, gm.base_class_name,
                          gm.subject_subgroup, gm.classroom_course_id, gm.exam_track
                     FROM journal_group_mappings gm
                     JOIN journal_sources source ON source.id = gm.source_id
                     JOIN journal_assessments ja ON ja.source_id = source.id
                     JOIN journal_students js2 ON js2.source_id = source.id AND js2.group_marker = gm.group_marker
                     LEFT JOIN journal_results jr ON jr.assessment_id = ja.id AND jr.journal_student_id = js2.id
                    WHERE gm.source_id = ? AND gm.group_marker = ?
                    ORDER BY ja.assessed_on, ja.source_column, js2.display_name""",
                (source_id, group_marker),
            ).fetchall()
            return [dict(row) for row in rows]

    def teacher_can_access_journal_marker(self, user_id: Any, source_id: Any, group_marker: str) -> bool:
        with self.connection() as connection:
            row = self.execute(
                connection,
                """SELECT 1
                     FROM users u
                     JOIN journal_group_mappings gm ON gm.source_id = ? AND gm.group_marker = ?
                     JOIN journal_sources js ON js.id = gm.source_id
                    WHERE u.id = ? AND u.identity_id IS NOT NULL AND (
                      EXISTS (
                        SELECT 1 FROM teacher_assignments ta
                         WHERE ta.teacher_identity_id = u.identity_id AND ta.active IS TRUE
                           AND (ta.subject = '' OR lower(ta.subject) = lower(js.subject))
                           AND (
                             ta.group_id = gm.group_id
                             OR (ta.subject_subgroup IS NOT NULL AND ta.subject_subgroup = gm.subject_subgroup)
                           )
                           AND (ta.base_class_name IS NULL OR ta.base_class_name = gm.base_class_name)
                           AND (ta.classroom_course_id IS NULL OR ta.classroom_course_id = gm.classroom_course_id)
                           AND (ta.exam_track IS NULL OR ta.exam_track = gm.exam_track)
                      )
                      OR EXISTS (
                        SELECT 1 FROM memberships m
                         WHERE m.identity_id = u.identity_id AND m.group_id = gm.group_id
                           AND m.member_role = 'teacher' AND m.active IS TRUE
                      )
                    )""",
                (source_id, group_marker, user_id),
            ).fetchone()
            return bool(row)

    def journal_status(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self.execute(
                connection,
                """SELECT js.id, js.spreadsheet_title, js.grade, js.subject, js.sheet_title, js.status, js.last_synced_at, js.last_error,
                          COUNT(DISTINCT ja.id) AS assessment_count, COUNT(DISTINCT jr.id) AS result_count,
                          COUNT(DISTINCT js2.id) AS roster_student_count,
                          COUNT(DISTINCT CASE WHEN jr.identity_id IS NULL THEN jr.id END) AS account_unlinked_count,
                          COUNT(DISTINCT CASE WHEN jr.identity_id IS NULL THEN jr.id END) AS unmapped_count,
                          COUNT(DISTINCT CASE WHEN jr.journal_student_id IS NULL THEN jr.id END) AS roster_unlinked_count
                     FROM journal_sources js LEFT JOIN journal_assessments ja ON ja.source_id = js.id LEFT JOIN journal_results jr ON jr.assessment_id = ja.id
                     LEFT JOIN journal_students js2 ON js2.source_id = js.id
                    GROUP BY js.id, js.spreadsheet_title, js.grade, js.subject, js.sheet_title, js.status, js.last_synced_at, js.last_error
                    ORDER BY js.grade, js.subject""",
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                markers = self.execute(
                    connection,
                    """SELECT DISTINCT jr.group_marker
                         FROM journal_assessments ja
                         JOIN journal_results jr ON jr.assessment_id = ja.id
                        WHERE ja.source_id = ?
                        ORDER BY jr.group_marker""",
                    (row["id"],),
                ).fetchall()
                mappings = self.execute(
                    connection,
                    """SELECT gm.group_marker, gm.group_id, g.name AS group_name, gm.base_class_name,
                              gm.subject_subgroup, gm.classroom_course_id, cc.title AS classroom_course_title,
                              gm.exam_track, gm.source
                         FROM journal_group_mappings gm
                         LEFT JOIN groups g ON g.id = gm.group_id
                         LEFT JOIN classroom_courses cc ON cc.id = gm.classroom_course_id
                        WHERE gm.source_id = ?
                        ORDER BY gm.group_marker""",
                    (row["id"],),
                ).fetchall()
                item["markers"] = [marker["group_marker"] for marker in markers]
                item["mappings"] = [dict(mapping) for mapping in mappings]
                items.append(item)
            return items

    def record_journal_failure(self, spreadsheet_id: str, subject: str, error: str) -> None:
        with self.connection() as connection:
            self.execute(
                connection,
                """UPDATE journal_sources
                      SET status = 'error', last_error = ?
                    WHERE spreadsheet_id = ? AND subject = ?""",
                (error[:1000], spreadsheet_id, subject),
            )
