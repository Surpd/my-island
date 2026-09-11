from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
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
  status TEXT NOT NULL DEFAULT 'active',
  origin TEXT,
  source_ref TEXT
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
  display_name TEXT,
  subject TEXT,
  base_class_name TEXT,
  subject_subgroup TEXT,
  exam_track TEXT,
  provenance_source TEXT,
  provenance_ref TEXT,
  canonical INTEGER NOT NULL DEFAULT 1,
  UNIQUE(name, group_type)
);
CREATE TABLE IF NOT EXISTS memberships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL REFERENCES groups(id),
  identity_id INTEGER NOT NULL REFERENCES identities(id),
  member_role TEXT NOT NULL CHECK (member_role = 'student'),
  source TEXT NOT NULL,
  source_ref TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT,
  valid_until TEXT,
  created_by INTEGER REFERENCES users(id),
  UNIQUE(group_id, identity_id, member_role, source, source_ref)
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
  raw_source TEXT,
  audience_rule TEXT,
  resolved_audience TEXT,
  teacher_identity_ids TEXT NOT NULL DEFAULT '[]',
  source_snapshot_id INTEGER,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS schedule_lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_snapshot_id INTEGER NOT NULL REFERENCES school_source_snapshots(id) ON DELETE CASCADE,
  source_record_id INTEGER REFERENCES school_source_records(id) ON DELETE SET NULL,
  record_key TEXT NOT NULL,
  sheet_id TEXT,
  tab_title TEXT,
  version_kind TEXT NOT NULL DEFAULT 'weekly',
  week_start TEXT,
  week_end TEXT,
  weekday INTEGER,
  lesson_date TEXT,
  start_time TEXT,
  end_time TEXT,
  subject TEXT NOT NULL DEFAULT '',
  teacher_hint TEXT NOT NULL DEFAULT '',
  room TEXT NOT NULL DEFAULT '',
  audience TEXT NOT NULL DEFAULT '',
  activity_type TEXT NOT NULL DEFAULT 'lesson',
  lesson_kind TEXT NOT NULL DEFAULT 'lesson',
  modifiers TEXT NOT NULL DEFAULT '{}',
  resolution_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
  resolved_identity_ids TEXT NOT NULL DEFAULT '[]',
  resolved_group_ids TEXT NOT NULL DEFAULT '[]',
  confidence REAL,
  evidence TEXT NOT NULL DEFAULT '{}',
  source_cell TEXT,
  source_color TEXT,
  merge_data TEXT,
  baseline_record_key TEXT,
  baseline_data TEXT,
  diff_status TEXT,
  issue_reason TEXT,
  diagnostics TEXT NOT NULL DEFAULT '{}',
  raw_payload TEXT NOT NULL DEFAULT '{}',
  UNIQUE(source_snapshot_id, record_key)
);
CREATE TABLE IF NOT EXISTS schedule_source_tabs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES school_sources(id) ON DELETE CASCADE,
  sheet_id TEXT NOT NULL, title TEXT NOT NULL, classification TEXT NOT NULL,
  week_start TEXT, week_end TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_id, sheet_id)
);
CREATE INDEX IF NOT EXISTS schedule_lessons_snapshot_idx ON schedule_lessons(source_snapshot_id, lesson_date, start_time);
CREATE INDEX IF NOT EXISTS schedule_lessons_status_idx ON schedule_lessons(resolution_status);
CREATE INDEX IF NOT EXISTS schedule_lessons_week_status_idx ON schedule_lessons(source_snapshot_id, version_kind, week_start, resolution_status);
CREATE INDEX IF NOT EXISTS schedule_lessons_diff_idx ON schedule_lessons(source_snapshot_id, diff_status);
CREATE INDEX IF NOT EXISTS schedule_source_tabs_source_week_idx ON schedule_source_tabs(source_id, classification, week_start);
CREATE TABLE IF NOT EXISTS schedule_lesson_audiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_snapshot_id INTEGER NOT NULL REFERENCES school_source_snapshots(id) ON DELETE CASCADE,
  lesson_id INTEGER NOT NULL REFERENCES schedule_lessons(id) ON DELETE CASCADE,
  week_start TEXT,
  lesson_date TEXT NOT NULL,
  start_time TEXT NOT NULL,
  grade_scope TEXT NOT NULL,
  audience_kind TEXT NOT NULL,
  resolved_group_ids TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  rule_reason TEXT NOT NULL,
  provenance TEXT NOT NULL DEFAULT '{}',
  UNIQUE(source_snapshot_id, lesson_id)
);
CREATE TABLE IF NOT EXISTS schedule_student_allocations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_snapshot_id INTEGER NOT NULL REFERENCES school_source_snapshots(id) ON DELETE CASCADE,
  week_start TEXT,
  lesson_date TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT,
  grade_scope TEXT NOT NULL,
  student_identity_id INTEGER NOT NULL REFERENCES identities(id),
  lesson_id INTEGER REFERENCES schedule_lessons(id) ON DELETE CASCADE,
  allocation_kind TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL,
  provenance TEXT NOT NULL DEFAULT '{}',
  UNIQUE(source_snapshot_id, lesson_date, start_time, grade_scope, student_identity_id)
);
CREATE INDEX IF NOT EXISTS schedule_lesson_audiences_slot_idx ON schedule_lesson_audiences(source_snapshot_id, week_start, lesson_date, start_time, grade_scope);
CREATE INDEX IF NOT EXISTS schedule_lesson_audiences_lesson_idx ON schedule_lesson_audiences(lesson_id);
CREATE INDEX IF NOT EXISTS schedule_student_allocations_slot_idx ON schedule_student_allocations(source_snapshot_id, week_start, lesson_date, start_time, grade_scope, status);
CREATE INDEX IF NOT EXISTS schedule_student_allocations_student_idx ON schedule_student_allocations(student_identity_id, lesson_date, start_time);
CREATE INDEX IF NOT EXISTS schedule_student_allocations_lesson_idx ON schedule_student_allocations(lesson_id);
CREATE TABLE IF NOT EXISTS group_schedule_audiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL REFERENCES groups(id),
  audience TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  subject_subgroup TEXT NOT NULL DEFAULT '',
  exam_track TEXT NOT NULL DEFAULT '',
  archived INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS account_identity_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  identity_id INTEGER NOT NULL UNIQUE REFERENCES identities(id),
  status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('pending','confirmed','conflict','revoked')),
  source TEXT NOT NULL,
  source_ref TEXT,
  confirmed_by INTEGER REFERENCES users(id),
  confirmed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
CREATE INDEX IF NOT EXISTS account_identity_links_status_idx ON account_identity_links(status, created_at);
CREATE INDEX IF NOT EXISTS student_preview_actor_active_idx ON student_preview_sessions(actor_user_id, expires_at);
CREATE TABLE IF NOT EXISTS schedule_syncs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  source_hash TEXT,
  error TEXT,
  validation_problems TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS admin_login_challenges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  actor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS admin_browser_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  revoked_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS admin_reconciliation_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_user_id INTEGER REFERENCES users(id),
  status TEXT NOT NULL,
  payload TEXT NOT NULL,
  result TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at TEXT,
  applied_at TEXT
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
  source_ref TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT,
  valid_until TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(teacher_identity_id, group_id, subject, capability, source, source_ref)
);
CREATE TABLE IF NOT EXISTS homeroom_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  teacher_identity_id INTEGER NOT NULL REFERENCES identities(id),
  class_group_id INTEGER NOT NULL REFERENCES groups(id),
  source TEXT NOT NULL DEFAULT 'admin_override',
  source_ref TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT,
  valid_until TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(teacher_identity_id, class_group_id, source, source_ref)
);
CREATE TABLE IF NOT EXISTS membership_overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  identity_id INTEGER NOT NULL REFERENCES identities(id),
  group_id INTEGER NOT NULL REFERENCES groups(id),
  member_role TEXT NOT NULL CHECK (member_role = 'student'),
  action TEXT NOT NULL CHECK (action IN ('include', 'exclude')),
  reason TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT,
  valid_until TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS student_selection_facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  identity_id INTEGER NOT NULL REFERENCES identities(id),
  academic_year TEXT NOT NULL,
  grade_level TEXT NOT NULL,
  subject TEXT NOT NULL,
  selection_kind TEXT NOT NULL CHECK (selection_kind IN ('oge','ege_profile')),
  source TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '{}',
  active INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT,
  valid_until TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(identity_id, academic_year, subject, selection_kind, source, source_ref)
);
CREATE UNIQUE INDEX IF NOT EXISTS student_selection_facts_active_logical_idx
  ON student_selection_facts(identity_id, academic_year, subject, selection_kind) WHERE active = 1 AND valid_until IS NULL;
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
CREATE TABLE IF NOT EXISTS school_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_type TEXT NOT NULL CHECK (source_type IN ('base_class_list','instructional_group_list','exam_profile_list','journal','schedule','classroom','manual','other')),
  external_key TEXT NOT NULL,
  display_name TEXT NOT NULL,
  location_ref TEXT,
  authority_status TEXT NOT NULL DEFAULT 'unknown' CHECK (authority_status IN ('unknown','reference','authoritative','manual')),
  configuration TEXT NOT NULL DEFAULT '{}',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_type, external_key)
);
CREATE TABLE IF NOT EXISTS school_sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES school_sources(id),
  mode TEXT NOT NULL CHECK (mode IN ('bootstrap','incremental','full_reparse')),
  status TEXT NOT NULL DEFAULT 'started' CHECK (status IN ('started','staged','applied','failed','unresolved')),
  idempotency_key TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  diagnostics TEXT NOT NULL DEFAULT '{}',
  UNIQUE(source_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS school_source_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES school_sources(id),
  sync_run_id INTEGER NOT NULL REFERENCES school_sync_runs(id),
  previous_snapshot_id INTEGER REFERENCES school_source_snapshots(id),
  fingerprint TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  effective_from TEXT,
  effective_until TEXT,
  raw_payload TEXT,
  structural_payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'staged' CHECK (status IN ('staged','valid','rejected','superseded')),
  is_last_known_valid INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (effective_until IS NULL OR effective_from IS NULL OR effective_until >= effective_from),
  UNIQUE(source_id, fingerprint)
);
CREATE UNIQUE INDEX IF NOT EXISTS school_source_snapshots_last_valid_idx ON school_source_snapshots(source_id) WHERE is_last_known_valid = 1;
CREATE TABLE IF NOT EXISTS school_source_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL REFERENCES school_source_snapshots(id) ON DELETE CASCADE,
  record_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  change_kind TEXT NOT NULL CHECK (change_kind IN ('new','changed','unchanged','deleted')),
  parse_status TEXT NOT NULL DEFAULT 'structural' CHECK (parse_status IN ('structural','semantic_required','validated','unresolved','rejected')),
  raw_payload TEXT,
  structural_payload TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(snapshot_id, record_key)
);
CREATE TABLE IF NOT EXISTS school_semantic_interpretations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_record_id INTEGER NOT NULL REFERENCES school_source_records(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  request_fingerprint TEXT NOT NULL,
  request_payload TEXT NOT NULL,
  response_payload TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','validated','rejected','unresolved','failed')),
  validation_diagnostics TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_record_id, provider, model, request_fingerprint)
);
CREATE TABLE IF NOT EXISTS school_candidate_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sync_run_id INTEGER NOT NULL REFERENCES school_sync_runs(id),
  source_record_id INTEGER REFERENCES school_source_records(id),
  change_type TEXT NOT NULL CHECK (change_type IN ('create','update','end','map')),
  entity_type TEXT NOT NULL CHECK (entity_type IN ('person','group','membership','teacher_assignment','homeroom_assignment','source_mapping','schedule_audience','selection_fact')),
  natural_key TEXT NOT NULL,
  proposed_payload TEXT NOT NULL,
  evidence TEXT NOT NULL CHECK (evidence <> '{}'),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','applied','unresolved','conflict')),
  manual_decision INTEGER NOT NULL DEFAULT 0,
  decision_by INTEGER REFERENCES users(id),
  decision_at TEXT,
  applied_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(sync_run_id, entity_type, natural_key)
);
CREATE TABLE IF NOT EXISTS school_resolution_issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sync_run_id INTEGER NOT NULL REFERENCES school_sync_runs(id),
  source_record_id INTEGER REFERENCES school_source_records(id),
  candidate_change_id INTEGER REFERENCES school_candidate_changes(id),
  issue_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','ignored')),
  details TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '{}',
  resolution TEXT,
  resolved_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS school_source_mappings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES school_sources(id),
  external_key TEXT NOT NULL,
  mapping_type TEXT NOT NULL CHECK (mapping_type IN ('identity','group','classroom_course','subject','audience_rule')),
  identity_id INTEGER REFERENCES identities(id),
  group_id INTEGER REFERENCES groups(id),
  classroom_course_id INTEGER REFERENCES classroom_courses(id),
  canonical_value TEXT,
  status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','confirmed','conflict','revoked')),
  manually_confirmed INTEGER NOT NULL DEFAULT 0,
  evidence TEXT NOT NULL DEFAULT '{}',
  valid_from TEXT,
  valid_until TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  supersedes_mapping_id INTEGER REFERENCES school_source_mappings(id),
  CHECK ((identity_id IS NOT NULL) + (group_id IS NOT NULL) + (classroom_course_id IS NOT NULL) + (canonical_value IS NOT NULL) = 1),
  CHECK ((mapping_type = 'identity' AND identity_id IS NOT NULL) OR (mapping_type = 'group' AND group_id IS NOT NULL) OR (mapping_type = 'classroom_course' AND classroom_course_id IS NOT NULL) OR (mapping_type IN ('subject','audience_rule') AND canonical_value IS NOT NULL)),
  CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS school_source_mappings_current_idx ON school_source_mappings(source_id, external_key, mapping_type) WHERE valid_until IS NULL AND status <> 'revoked';
CREATE INDEX IF NOT EXISTS school_sync_runs_review_idx ON school_sync_runs(status, started_at);
CREATE INDEX IF NOT EXISTS school_source_records_diff_idx ON school_source_records(snapshot_id, change_kind, parse_status);
CREATE INDEX IF NOT EXISTS school_candidate_changes_review_idx ON school_candidate_changes(status, entity_type, created_at);
CREATE INDEX IF NOT EXISTS school_resolution_issues_open_idx ON school_resolution_issues(status, issue_type, created_at);
CREATE INDEX IF NOT EXISTS school_source_mappings_target_idx ON school_source_mappings(mapping_type, status, identity_id, group_id);
CREATE INDEX IF NOT EXISTS school_source_snapshots_sync_run_idx ON school_source_snapshots(sync_run_id);
CREATE INDEX IF NOT EXISTS school_source_snapshots_previous_idx ON school_source_snapshots(previous_snapshot_id);
CREATE INDEX IF NOT EXISTS school_candidate_changes_source_record_idx ON school_candidate_changes(source_record_id);
CREATE INDEX IF NOT EXISTS school_candidate_changes_decision_by_idx ON school_candidate_changes(decision_by);
CREATE INDEX IF NOT EXISTS school_resolution_issues_sync_run_idx ON school_resolution_issues(sync_run_id);
CREATE INDEX IF NOT EXISTS school_resolution_issues_source_record_idx ON school_resolution_issues(source_record_id);
CREATE INDEX IF NOT EXISTS school_resolution_issues_candidate_idx ON school_resolution_issues(candidate_change_id);
CREATE INDEX IF NOT EXISTS school_resolution_issues_resolved_by_idx ON school_resolution_issues(resolved_by);
CREATE INDEX IF NOT EXISTS school_source_mappings_identity_idx ON school_source_mappings(identity_id);
CREATE INDEX IF NOT EXISTS school_source_mappings_group_idx ON school_source_mappings(group_id);
CREATE INDEX IF NOT EXISTS school_source_mappings_classroom_course_idx ON school_source_mappings(classroom_course_id);
CREATE INDEX IF NOT EXISTS school_source_mappings_created_by_idx ON school_source_mappings(created_by);
CREATE INDEX IF NOT EXISTS school_source_mappings_supersedes_idx ON school_source_mappings(supersedes_mapping_id);
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
CREATE INDEX IF NOT EXISTS journal_group_mappings_classroom_course_idx ON journal_group_mappings(classroom_course_id);
CREATE INDEX IF NOT EXISTS teacher_assignments_classroom_course_idx ON teacher_assignments(classroom_course_id);
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
            self._ensure_sqlite_schema_parity(connection)
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
            identity_columns = {row["name"] for row in connection.execute("PRAGMA table_info(identities)").fetchall()}
            for name, definition in {"origin": "TEXT", "source_ref": "TEXT"}.items():
                if name not in identity_columns:
                    connection.execute(f"ALTER TABLE identities ADD COLUMN {name} {definition}")
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
            connection.execute(
                "INSERT OR IGNORE INTO account_identity_links(user_id, identity_id, status, source, source_ref, confirmed_at) SELECT id, identity_id, 'confirmed', 'legacy_backfill', 'users.identity_id', created_at FROM users WHERE identity_id IS NOT NULL"
            )

    def _ensure_sqlite_schema_parity(self, connection: Any) -> None:
        """Apply only additive compatibility changes to an existing local SQLite store.

        Production/Postgres remains migration-driven. Local stores created before the
        current SCHEMA are upgraded in place so read-only admin diagnostics do not fail
        merely because a newer additive column is missing.
        """
        additions: dict[str, dict[str, str]] = {
            "groups": {
                "display_name": "TEXT",
                "subject": "TEXT",
                "base_class_name": "TEXT",
                "subject_subgroup": "TEXT",
                "exam_track": "TEXT",
                "provenance_source": "TEXT",
                "provenance_ref": "TEXT",
                "canonical": "INTEGER NOT NULL DEFAULT 1",
            },
            "memberships": {
                "source_ref": "TEXT NOT NULL DEFAULT ''",
                "created_by": "INTEGER",
            },
            "identities": {"origin": "TEXT", "source_ref": "TEXT"},
            "schedule_entries": {
                "audience_rule": "TEXT",
                "resolved_audience": "TEXT",
                "teacher_identity_ids": "TEXT NOT NULL DEFAULT '[]'",
                "source_snapshot_id": "INTEGER",
                "archived": "INTEGER NOT NULL DEFAULT 0",
            },
            "schedule_syncs": {"archived": "INTEGER NOT NULL DEFAULT 0"},
            "group_schedule_audiences": {"archived": "INTEGER NOT NULL DEFAULT 0"},
            "schedule_lessons": {
                "source_record_id": "INTEGER REFERENCES school_source_records(id) ON DELETE SET NULL",
                "sheet_id": "TEXT", "tab_title": "TEXT", "version_kind": "TEXT NOT NULL DEFAULT 'weekly'",
                "week_start": "TEXT", "week_end": "TEXT", "weekday": "INTEGER",
                "teacher_hint": "TEXT NOT NULL DEFAULT ''", "room": "TEXT NOT NULL DEFAULT ''",
                "activity_type": "TEXT NOT NULL DEFAULT 'lesson'", "modifiers": "TEXT NOT NULL DEFAULT '{}'",
                "confidence": "REAL", "evidence": "TEXT NOT NULL DEFAULT '{}'", "source_cell": "TEXT",
                "source_color": "TEXT", "merge_data": "TEXT", "baseline_record_key": "TEXT",
                "baseline_data": "TEXT", "diff_status": "TEXT", "issue_reason": "TEXT",
            },
            "announcements": {
                "audience_kind": "TEXT NOT NULL DEFAULT 'all'",
                "audience_ref": "TEXT",
                "publish_at": "TEXT",
                "starts_at": "TEXT",
                "pinned": "INTEGER NOT NULL DEFAULT 0",
                "author_user_id": "INTEGER",
                "status": "TEXT NOT NULL DEFAULT 'published'",
            },
            "journal_results": {"journal_student_id": "INTEGER"},
            "journal_group_mappings": {
                "base_class_name": "TEXT",
                "subject_subgroup": "TEXT",
                "classroom_course_id": "INTEGER",
                "exam_track": "TEXT",
            },
            "teacher_assignments": {
                "base_class_name": "TEXT",
                "subject_subgroup": "TEXT",
                "classroom_course_id": "INTEGER",
                "exam_track": "TEXT",
            },
        }
        for table, columns in additions.items():
            present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, definition in columns.items():
                if name not in present:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        connection.execute("CREATE TABLE IF NOT EXISTS admin_login_challenges (id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE, actor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL, consumed_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        connection.execute("CREATE TABLE IF NOT EXISTS admin_browser_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL, last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, revoked_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        connection.execute("CREATE TABLE IF NOT EXISTS admin_reconciliation_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER REFERENCES users(id), status TEXT NOT NULL, payload TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, reviewed_at TEXT, applied_at TEXT)")

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_admin_login_challenge(self, actor_user_id: Any, ttl_seconds: int = 300) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        with self.connection() as connection:
            self.execute(connection, "INSERT INTO admin_login_challenges(token_hash, actor_user_id, expires_at) VALUES (?, ?, ?)", (self._token_hash(token), actor_user_id, expires_at))
        return token, expires_at

    def consume_admin_login_challenge(self, token: str, ttl_seconds: int = 86400 * 7) -> tuple[str, Any] | None:
        now = datetime.now(timezone.utc)
        with self.connection() as connection:
            row = self.execute(connection, "SELECT * FROM admin_login_challenges WHERE token_hash = ? AND consumed_at IS NULL", (self._token_hash(token),)).fetchone()
            if not row:
                return None
            try:
                expires_at = datetime.fromisoformat(str(row["expires_at"]))
            except ValueError:
                return None
            if expires_at <= now:
                return None
            self.execute(connection, "UPDATE admin_login_challenges SET consumed_at = ? WHERE id = ?", (now.isoformat(), row["id"]))
            session_token = secrets.token_urlsafe(48)
            session_expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
            self.execute(connection, "INSERT INTO admin_browser_sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)", (self._token_hash(session_token), row["actor_user_id"], session_expires))
            return session_token, row["actor_user_id"]

    def get_admin_browser_session(self, token: str) -> Any | None:
        now = datetime.now(timezone.utc)
        with self.connection() as connection:
            row = self.execute(connection, "SELECT s.*, u.telegram_user_id, u.role, u.identity_id FROM admin_browser_sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.revoked_at IS NULL", (self._token_hash(token),)).fetchone()
            if not row:
                return None
            try:
                if datetime.fromisoformat(str(row["expires_at"])) <= now:
                    return None
            except ValueError:
                return None
            self.execute(connection, "UPDATE admin_browser_sessions SET last_seen_at = ? WHERE id = ?", (now.isoformat(), row["id"]))
            return row

    def revoke_admin_browser_session(self, token: str) -> None:
        with self.connection() as connection:
            self.execute(connection, "UPDATE admin_browser_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = ? AND revoked_at IS NULL", (self._token_hash(token),))

    def create_reconciliation_run(self, actor_user_id: Any, payload: dict[str, Any]) -> Any:
        with self.connection() as connection:
            payload_sql = "?::jsonb" if self.database_url else "?"
            row = self.execute(connection, f"INSERT INTO admin_reconciliation_runs(actor_user_id, status, payload) VALUES (?, 'ready_for_review', {payload_sql}) RETURNING id", (actor_user_id, json.dumps(payload, ensure_ascii=False, default=str))).fetchone()
            return row["id"]

    @staticmethod
    def _decode_json_value(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if not value:
            return value
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return value

    def get_latest_reconciliation_run(self) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = self.execute(connection, "SELECT * FROM admin_reconciliation_runs ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                return None
            item = dict(row)
            item["payload"] = self._decode_json_value(item["payload"])
            if item.get("result"):
                item["result"] = self._decode_json_value(item["result"])
            return item

    def get_reconciliation_run(self, run_id: Any) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = self.execute(connection, "SELECT * FROM admin_reconciliation_runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["payload"] = self._decode_json_value(item["payload"])
            if item.get("result"):
                item["result"] = self._decode_json_value(item["result"])
            return item

    def mark_reconciliation_reviewed(self, run_id: Any, actor_user_id: Any) -> dict[str, Any] | None:
        with self.connection() as connection:
            self.execute(connection, "UPDATE admin_reconciliation_runs SET status = 'approved', reviewed_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'ready_for_review'", (run_id,))
        return self.get_reconciliation_run(run_id)

    def finish_reconciliation_run(self, run_id: Any, result: dict[str, Any], status: str = "applied") -> dict[str, Any] | None:
        with self.connection() as connection:
            result_sql = "?::jsonb" if self.database_url else "?"
            self.execute(connection, f"UPDATE admin_reconciliation_runs SET status = ?, result = {result_sql}, applied_at = CURRENT_TIMESTAMP WHERE id = ?", (status, json.dumps(result, ensure_ascii=False, default=str), run_id))
        return self.get_reconciliation_run(run_id)

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
                        """INSERT INTO account_identity_links(user_id, identity_id, status, source, source_ref, confirmed_by, confirmed_at)
                           VALUES (?, ?, 'confirmed', 'identity_claim', ?, ?, CURRENT_TIMESTAMP)
                           ON CONFLICT(user_id) DO UPDATE SET identity_id = excluded.identity_id, status = 'confirmed', source = excluded.source,
                             source_ref = excluded.source_ref, confirmed_by = excluded.confirmed_by, confirmed_at = excluded.confirmed_at""",
                        (claim["user_id"], claim["identity_id"], str(claim_id), reviewer_id),
                    )
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
            return self.execute(connection, "SELECT lesson_date, start_time, end_time, subject, teacher, room, audience FROM schedule_entries WHERE lesson_date = ? AND archived IS FALSE ORDER BY start_time", (lesson_date,)).fetchall()

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
                     JOIN group_schedule_audiences ga ON ga.audience = se.audience AND ga.archived IS FALSE
                     JOIN memberships m ON m.group_id = ga.group_id AND m.active IS TRUE
                     JOIN users u ON u.identity_id = m.identity_id
                     WHERE u.id = ?
                        AND se.archived IS FALSE
                       AND (m.valid_from IS NULL OR m.valid_from <= se.lesson_date)
                       AND (m.valid_until IS NULL OR m.valid_until >= se.lesson_date)
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
                       AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE)
                       AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
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
                        WHERE group_id = ? AND archived IS FALSE
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
                      JOIN teacher_assignments ta ON ta.group_id = g.id AND ta.active IS TRUE
                      JOIN users u ON u.identity_id = ta.teacher_identity_id
                     WHERE u.id = ?
                       AND (ta.valid_from IS NULL OR ta.valid_from <= CURRENT_DATE)
                       AND (ta.valid_until IS NULL OR ta.valid_until >= CURRENT_DATE)
                    ORDER BY c.title""",
                (user_id,),
            ).fetchall()

    def list_users_with_groups(self) -> list[Any]:
        with self.connection() as connection:
            users = self.execute(
                connection,
                """SELECT u.id, u.telegram_user_id, u.role, u.identity_id, i.display_name, i.class_name, i.kind AS identity_kind,
                              COALESCE(ail.status, CASE WHEN u.identity_id IS NULL THEN 'unlinked' ELSE 'confirmed' END) AS identity_status,
                              COALESCE(ail.source, CASE WHEN u.identity_id IS NULL THEN NULL ELSE 'legacy' END) AS identity_source,
                              (SELECT c.status FROM identity_claims c WHERE c.user_id = u.id ORDER BY c.created_at DESC, c.id DESC LIMIT 1) AS claim_status
                     FROM users u LEFT JOIN identities i ON i.id = u.identity_id
                     LEFT JOIN account_identity_links ail ON ail.user_id = u.id AND ail.identity_id = u.identity_id
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
                      """SELECT g.id, g.name, g.display_name, g.group_type, m.source
                          FROM memberships m JOIN groups g ON g.id = m.group_id
                         WHERE m.identity_id = ? AND m.active IS TRUE
                           AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE)
                           AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
                         ORDER BY g.name""",
                    (user["identity_id"],),
                ).fetchall() if user["identity_id"] else []
                roles = roles_by_user.get(user["id"], [str(user["role"])])
                teacher_assignments = self.execute(
                    connection,
                     """SELECT ta.id, ta.group_id, g.name AS group_name, g.display_name AS group_display_name, ta.subject, ta.capability, ta.source, ta.active
                         FROM teacher_assignments ta JOIN groups g ON g.id = ta.group_id
                        WHERE ta.teacher_identity_id = ? ORDER BY g.name, ta.subject""",
                    (user["identity_id"],),
                ).fetchall() if user["identity_id"] else []
                homeroom = self.execute(
                    connection,
                     """SELECT h.id, h.class_group_id AS group_id, g.name AS group_name, g.display_name AS group_display_name, h.source, h.active
                         FROM homeroom_assignments h JOIN groups g ON g.id = h.class_group_id
                        WHERE h.teacher_identity_id = ? ORDER BY g.name""",
                    (user["identity_id"],),
                ).fetchall() if user["identity_id"] else []
                result.append({
                    **dict(user), "has_account": True, "roles": roles, "groups": [dict(group) for group in groups],
                    "teacher_assignments": [dict(item) for item in teacher_assignments],
                    "homeroom_assignments": [dict(item) for item in homeroom],
                })
            unlinked_identities = self.execute(
                connection,
                """SELECT i.id AS identity_id, i.kind AS identity_kind, i.display_name, i.class_name,
                          'confirmed' AS identity_status
                     FROM identities i
                    WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.identity_id = i.id)
                    ORDER BY i.display_name""",
            ).fetchall()
            for identity in unlinked_identities:
                result.append({
                    **dict(identity),
                    "id": f"identity:{identity['identity_id']}",
                    "has_account": False,
                    "identity_source": "school_record",
                    "roles": [str(identity["identity_kind"])],
                    "groups": [],
                    "teacher_assignments": [],
                    "homeroom_assignments": [],
                })
            return result

    def list_people_library(
        self,
        kind: str | None = None,
        query: str | None = None,
        class_name: str | None = None,
        group_id: str | None = None,
        identity_id: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Canonical School Directory view with explicit relationship semantics."""
        clauses = ["i.status = 'active'"]
        params: list[Any] = []
        if kind in {"student", "teacher"}:
            clauses.append("i.kind = ?")
            params.append(kind)
        if query:
            clauses.append("lower(i.display_name || ' ' || coalesce(i.class_name, '')) like ?")
            params.append(f"%{query.strip().lower()}%")
        if class_name:
            clauses.append(
                "EXISTS (SELECT 1 FROM memberships cm JOIN groups cg ON cg.id = cm.group_id "
                "WHERE cm.identity_id = i.id AND cm.active IS TRUE AND cg.group_type = 'class' "
                "AND cg.name = ? AND (cm.valid_from IS NULL OR cm.valid_from <= CURRENT_DATE) "
                "AND (cm.valid_until IS NULL OR cm.valid_until >= CURRENT_DATE))"
            )
            params.append(class_name)
        if group_id:
            clauses.append(
                "EXISTS (SELECT 1 FROM memberships gm WHERE gm.identity_id = i.id "
                "AND gm.group_id = ? AND gm.active IS TRUE "
                "AND (gm.valid_from IS NULL OR gm.valid_from <= CURRENT_DATE) "
                "AND (gm.valid_until IS NULL OR gm.valid_until >= CURRENT_DATE))"
            )
            params.append(group_id)
        if identity_id is not None:
            clauses.append("i.id = ?")
            params.append(identity_id)
        with self.connection() as connection:
            identities = self.execute(
                connection,
                f"SELECT i.id, i.kind, i.display_name, i.class_name, i.status, i.origin, i.source_ref FROM identities i WHERE {' AND '.join(clauses)} ORDER BY CASE WHEN i.kind = 'student' THEN 0 ELSE 1 END, i.display_name",
                params,
            ).fetchall()
            result: list[dict[str, Any]] = []
            for identity in identities:
                identity_id = identity["id"]
                groups = self.execute(
                    connection,
                    """SELECT g.id, g.name, g.display_name, g.group_type, g.subject, g.base_class_name,
                              g.subject_subgroup, g.exam_track, m.source, m.source_ref
                         FROM memberships m JOIN groups g ON g.id = m.group_id
                        WHERE m.identity_id = ? AND m.active IS TRUE
                          AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE)
                          AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
                        ORDER BY g.group_type, g.name""",
                    (identity_id,),
                ).fetchall()
                group_items_by_id: dict[Any, dict[str, Any]] = {}
                for raw_item in groups:
                    item = dict(raw_item)
                    item["membership_kind"] = (
                        "base_class" if item["group_type"] == "class"
                        else "exam_profile" if item["group_type"] == "exam_track"
                        else "instructional" if item["group_type"] in {"subject_group", "instructional_group"}
                        else "other"
                    )
                    item["is_manual"] = item.get("source") == "admin_override"
                    item["membership_sources"] = [item.get("source")]
                    item["source_refs"] = [item.get("source_ref")] if item.get("source_ref") else []
                    existing = group_items_by_id.get(item["id"])
                    if existing is None:
                        group_items_by_id[item["id"]] = item
                    else:
                        existing["is_manual"] = existing["is_manual"] or item["is_manual"]
                        existing["membership_sources"] = sorted(set(existing["membership_sources"] + item["membership_sources"]))
                        existing["source_refs"] = sorted(set(existing["source_refs"] + item["source_refs"]))
                        if existing.get("source") == "admin_override" and item.get("source") != "admin_override":
                            existing["source"] = item.get("source")
                            existing["source_ref"] = item.get("source_ref")
                group_items = list(group_items_by_id.values())
                base_classes = [item for item in group_items if item["membership_kind"] == "base_class"]
                instructional_memberships = [item for item in group_items if item["membership_kind"] == "instructional"]
                selection_rows = self.execute(
                    connection,
                    """SELECT id, academic_year, grade_level, subject, selection_kind,
                              source, source_ref, evidence
                         FROM student_selection_facts
                        WHERE identity_id = ? AND active IS TRUE
                          AND (valid_from IS NULL OR valid_from <= CURRENT_DATE)
                          AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
                        ORDER BY grade_level, subject""",
                    (identity_id,),
                ).fetchall()
                selection_facts = [dict(item) for item in selection_rows]
                # Compatibility projection for the existing Admin card. Actual
                # OGE/EGE roster groups remain instructional memberships.
                exam_profile_memberships = [{
                    "id": f"selection:{item['id']}",
                    "name": f"selection:{item['grade_level']}:{item['subject']}",
                    "display_name": f"{item['subject']} · {'ОГЭ' if item['selection_kind'] == 'oge' else 'ЕГЭ/профиль'}",
                    "group_type": "selection_fact",
                    "subject": item["subject"],
                    "base_class_name": item["grade_level"],
                    "subject_subgroup": None,
                    "exam_track": "ОГЭ" if item["selection_kind"] == "oge" else "ЕГЭ",
                    "membership_kind": "exam_profile",
                    "source": item["source"],
                    "source_ref": item["source_ref"],
                    "source_refs": [item["source_ref"]],
                    "membership_sources": [item["source"]],
                    "is_manual": item["source"] == "admin_override",
                } for item in selection_facts]
                assignments = self.execute(
                    connection,
                    """SELECT ta.id, g.id AS group_id, g.name, g.display_name, g.group_type,
                              ta.subject, ta.base_class_name, ta.subject_subgroup, ta.exam_track,
                              ta.source, ta.source_ref
                         FROM teacher_assignments ta JOIN groups g ON g.id = ta.group_id
                        WHERE ta.teacher_identity_id = ? AND ta.active IS TRUE
                          AND (ta.valid_from IS NULL OR ta.valid_from <= CURRENT_DATE)
                          AND (ta.valid_until IS NULL OR ta.valid_until >= CURRENT_DATE)
                        ORDER BY g.name""",
                    (identity_id,),
                ).fetchall()
                homerooms = self.execute(
                    connection,
                    """SELECT h.id, g.id AS group_id, g.name, g.display_name
                         FROM homeroom_assignments h JOIN groups g ON g.id = h.class_group_id
                        WHERE h.teacher_identity_id = ? AND h.active IS TRUE
                          AND (h.valid_from IS NULL OR h.valid_from <= CURRENT_DATE)
                          AND (h.valid_until IS NULL OR h.valid_until >= CURRENT_DATE)
                        ORDER BY g.name""",
                    (identity_id,),
                ).fetchall()
                account = self.execute(
                    connection,
                    """SELECT u.id AS user_id, u.telegram_user_id, u.role,
                              COALESCE(ail.status, 'unlinked') AS account_status
                         FROM users u LEFT JOIN account_identity_links ail ON ail.user_id = u.id
                        WHERE u.identity_id = ? LIMIT 1""",
                    (identity_id,),
                ).fetchone()
                roles = {identity["kind"]}
                if account:
                    roles.add(account["role"])
                    for role in self.execute(connection, "SELECT role FROM user_roles WHERE user_id = ?", (account["user_id"],)).fetchall():
                        roles.add(role["role"])
                unresolved = self.execute(
                    connection,
                    """SELECT id, issue_type, status, details, evidence
                         FROM school_resolution_issues
                        WHERE status = 'open' AND CAST(details AS TEXT) LIKE ?
                        ORDER BY created_at DESC""",
                    (f"%{identity['display_name']}%",),
                ).fetchall()
                result.append({
                    **dict(identity),
                    "identity_kind": identity["kind"],
                    "roles": sorted(roles),
                    "user_id": account["user_id"] if account else None,
                    "telegram_user_id": account["telegram_user_id"] if account else None,
                    "account_status": account["account_status"] if account else "unlinked",
                    "has_account": bool(account),
                    # `groups` remains for backwards compatibility; new Admin UI must
                    # consume the typed projections below instead of guessing by label.
                    "groups": group_items,
                    "base_class": base_classes[0] if len(base_classes) == 1 else None,
                    "base_classes": base_classes,
                    "instructional_memberships": instructional_memberships,
                    "exam_profile_memberships": exam_profile_memberships,
                    "selection_facts": selection_facts,
                    "relationship_issue": "multiple_active_base_classes" if len(base_classes) > 1 else None,
                    "teacher_assignments": [dict(item) for item in assignments],
                    "homerooms": [dict(item) for item in homerooms],
                    "unresolved": [dict(item) for item in unresolved],
                })
            return result

    def get_people_library_person(self, identity_id: Any) -> dict[str, Any] | None:
        items = self.list_people_library(identity_id=identity_id)
        return next((item for item in items if str(item["id"]) == str(identity_id)), None)

    def list_people_library_summary(
        self,
        kind: str | None = None,
        query: str | None = None,
        class_name: str | None = None,
        group_id: str | None = None,
        has_issues: bool | None = None,
        protected: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Return the collection projection without per-person database round trips.

        The full directory projection is intentionally retained for person details and
        compatibility callers. The Admin collection only needs counts and labels, so
        load its relationships in batches instead of issuing several queries per row.
        """
        clauses = ["i.status = 'active'"]
        params: list[Any] = []
        if kind in {"student", "teacher"}:
            clauses.append("i.kind = ?")
            params.append(kind)
        if query:
            clauses.append("lower(i.display_name || ' ' || coalesce(i.class_name, '')) like ?")
            params.append(f"%{query.strip().lower()}%")
        if class_name:
            clauses.append(
                "EXISTS (SELECT 1 FROM memberships cm JOIN groups cg ON cg.id = cm.group_id "
                "WHERE cm.identity_id = i.id AND cm.active IS TRUE AND cg.group_type = 'class' "
                "AND cg.name = ? AND (cm.valid_from IS NULL OR cm.valid_from <= CURRENT_DATE) "
                "AND (cm.valid_until IS NULL OR cm.valid_until >= CURRENT_DATE))"
            )
            params.append(class_name)
        if group_id:
            clauses.append(
                "EXISTS (SELECT 1 FROM memberships gm WHERE gm.identity_id = i.id "
                "AND gm.group_id = ? AND gm.active IS TRUE "
                "AND (gm.valid_from IS NULL OR gm.valid_from <= CURRENT_DATE) "
                "AND (gm.valid_until IS NULL OR gm.valid_until >= CURRENT_DATE))"
            )
            params.append(group_id)
        if has_issues is True:
            clauses.append("EXISTS (SELECT 1 FROM school_resolution_issues ri WHERE ri.status = 'open' AND CAST(ri.details AS TEXT) LIKE '%' || i.display_name || '%')")
        elif has_issues is False:
            clauses.append("NOT EXISTS (SELECT 1 FROM school_resolution_issues ri WHERE ri.status = 'open' AND CAST(ri.details AS TEXT) LIKE '%' || i.display_name || '%')")

        with self.connection() as connection:
            identities = [
                dict(row)
                for row in self.execute(
                    connection,
                    f"SELECT i.id, i.kind, i.display_name, i.class_name, i.status, i.origin, i.source_ref "
                    f"FROM identities i WHERE {' AND '.join(clauses)} "
                    "ORDER BY CASE WHEN i.kind = 'student' THEN 0 ELSE 1 END, i.display_name",
                    tuple(params),
                ).fetchall()
            ]
            if not identities:
                return []

            latest_reconciliation = self.execute(
                connection,
                "SELECT payload FROM admin_reconciliation_runs ORDER BY id DESC LIMIT 1",
            ).fetchone()
            protected_identity_ids: set[str] = set()
            if latest_reconciliation:
                payload = self._decode_json_value(latest_reconciliation["payload"])
                for assignment in (payload.get("assignments", []) if isinstance(payload, dict) else []):
                    if isinstance(assignment, dict) and assignment.get("action") == "PROTECTED" and assignment.get("identity_id") is not None:
                        protected_identity_ids.add(str(assignment["identity_id"]))
            if protected is not None:
                identities = [item for item in identities if (str(item["id"]) in protected_identity_ids) == protected]
                if not identities:
                    return []

            identity_ids = [item["id"] for item in identities]
            placeholders = ",".join("?" for _ in identity_ids)
            memberships = self.execute(
                connection,
                f"""SELECT m.identity_id, g.id, g.name, g.display_name, g.group_type, g.subject,
                                  g.base_class_name, g.subject_subgroup, g.exam_track, m.source, m.source_ref
                             FROM memberships m JOIN groups g ON g.id = m.group_id
                            WHERE m.identity_id IN ({placeholders}) AND m.active IS TRUE
                              AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE)
                              AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
                            ORDER BY g.group_type, g.name""",
                tuple(identity_ids),
            ).fetchall()
            memberships_by_identity: dict[Any, dict[Any, dict[str, Any]]] = {}
            for raw in memberships:
                item = dict(raw)
                item["membership_kind"] = (
                    "base_class" if item["group_type"] == "class"
                    else "exam_profile" if item["group_type"] == "exam_track"
                    else "instructional" if item["group_type"] in {"subject_group", "instructional_group"}
                    else "other"
                )
                item["is_manual"] = item.get("source") == "admin_override"
                memberships_by_identity.setdefault(item["identity_id"], {})[item["id"]] = item

            accounts = self.execute(
                connection,
                f"""SELECT u.identity_id, u.id AS user_id, u.telegram_user_id, u.role,
                                  COALESCE(ail.status, 'unlinked') AS account_status
                             FROM users u LEFT JOIN account_identity_links ail ON ail.user_id = u.id
                            WHERE u.identity_id IN ({placeholders})""",
                tuple(identity_ids),
            ).fetchall()
            account_by_identity = {row["identity_id"]: dict(row) for row in accounts}
            user_ids = [row["user_id"] for row in accounts]
            roles_by_user: dict[Any, set[str]] = {}
            if user_ids:
                role_placeholders = ",".join("?" for _ in user_ids)
                role_rows = self.execute(
                    connection,
                    f"SELECT user_id, role FROM user_roles WHERE user_id IN ({role_placeholders})",
                    tuple(user_ids),
                ).fetchall()
                for row in role_rows:
                    roles_by_user.setdefault(row["user_id"], set()).add(row["role"])

            open_issues = [
                dict(row)
                for row in self.execute(
                    connection,
                    "SELECT details FROM school_resolution_issues WHERE status = 'open'",
                ).fetchall()
            ]
            result: list[dict[str, Any]] = []
            for identity in identities:
                grouped = list(memberships_by_identity.get(identity["id"], {}).values())
                base_classes = [item for item in grouped if item["membership_kind"] == "base_class"]
                instructional = [item for item in grouped if item["membership_kind"] == "instructional"]
                account = account_by_identity.get(identity["id"])
                roles = {identity["kind"]}
                if account:
                    roles.add(account["role"])
                    roles.update(roles_by_user.get(account["user_id"], set()))
                display_name = str(identity.get("display_name") or "")
                unresolved_count = sum(
                    1 for issue in open_issues if display_name and display_name in str(issue.get("details") or "")
                )
                result.append({
                    **identity,
                    "identity_kind": identity["kind"],
                    "roles": sorted(roles),
                    "user_id": account["user_id"] if account else None,
                    "telegram_user_id": account["telegram_user_id"] if account else None,
                    "account_status": account["account_status"] if account else "unlinked",
                    "has_account": bool(account),
                    "groups": grouped,
                    "base_class": base_classes[0] if len(base_classes) == 1 else None,
                    "base_classes": base_classes,
                    "instructional_memberships": instructional,
                    "exam_profile_memberships": [],
                    "selection_facts": [],
                    "relationship_issue": "multiple_active_base_classes" if len(base_classes) > 1 else None,
                    "teacher_assignments": [],
                    "homerooms": [],
                    "unresolved": [],
                    "unresolved_count": unresolved_count,
                    "protected_case": str(identity["id"]) in protected_identity_ids,
                })
            return result

    def list_people_filter_options(self) -> dict[str, list[dict[str, Any]]]:
        with self.connection() as connection:
            classes = self.execute(
                connection,
                """SELECT DISTINCT class_name AS value FROM identities
                    WHERE status = 'active' AND class_name IS NOT NULL AND trim(class_name) <> ''
                    ORDER BY class_name""",
            ).fetchall()
            groups = self.execute(
                connection,
                """SELECT id, name, display_name, group_type, base_class_name, subject
                     FROM groups WHERE canonical IS TRUE
                    ORDER BY COALESCE(display_name, name), name""",
            ).fetchall()
            return {
                "classes": [{"value": row["value"], "label": row["value"]} for row in classes],
                "groups": [dict(row) for row in groups],
            }

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
                                  JOIN group_schedule_audiences ga ON ga.group_id = m.group_id AND ga.archived IS FALSE
                                 JOIN users u ON u.identity_id = m.identity_id
                                WHERE u.id = ? AND m.active IS TRUE
                                  AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE)
                                  AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
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
                        WHERE archived IS FALSE AND parse_status IN ('partial', 'ambiguous', 'failed')"""
            params: tuple[Any, ...] = ()
            if audience:
                query += " AND audience = ?"
                params = (audience,)
            query += " ORDER BY lesson_date, start_time, source_coordinate"
            return self.execute(connection, query, params).fetchall()

    def list_schedule_syncs(self, limit: int = 20) -> list[Any]:
        with self.connection() as connection:
            return self.execute(connection, "SELECT id, status, source, source_hash, error, validation_problems, created_at FROM schedule_syncs WHERE archived IS FALSE ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

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
                            AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE)
                            AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
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

    def upsert_classroom_course(self, course: dict[str, Any], teacher_account: str, group_id: Any | None = None) -> Any:
        with self.connection() as connection:
            existing = self.execute(
                connection,
                "SELECT id, group_id FROM classroom_courses WHERE external_course_id = ?",
                (course["external_id"],),
            ).fetchone()
            mapped_group_id = existing["group_id"] if existing else group_id
            if not mapped_group_id:
                raise ValueError("Classroom course has no explicit internal group mapping")
            if not self.execute(connection, "SELECT 1 FROM groups WHERE id = ?", (mapped_group_id,)).fetchone():
                raise ValueError("Classroom course mapping points to a missing internal group")
            self.execute(
                connection,
                """INSERT INTO classroom_courses(external_course_id, group_id, title, teacher_account, section, description, room, update_time, raw_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(external_course_id) DO UPDATE SET title = excluded.title,
                      teacher_account = excluded.teacher_account, section = excluded.section, description = excluded.description,
                      room = excluded.room, update_time = excluded.update_time, raw_source = excluded.raw_source""",
                (course["external_id"], mapped_group_id, course["title"], teacher_account, course.get("section"), course.get("description"), course.get("room"), course.get("update_time"), course.get("raw_source")),
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
                       AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE)
                       AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
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

    def create_membership(self, group_id: Any, identity_id: Any, member_role: str, source: str, source_ref: str = "", created_by: Any | None = None) -> Any:
        with self.connection() as connection:
            identity = self.execute(connection, "SELECT kind, status FROM identities WHERE id = ?", (identity_id,)).fetchone()
            group = self.execute(connection, "SELECT canonical FROM groups WHERE id = ?", (group_id,)).fetchone()
            if member_role != "student" or not identity or identity["kind"] != "student" or identity["status"] != "active":
                raise ValueError("Membership requires an active student identity")
            if not group or not group["canonical"]:
                raise ValueError("Membership requires a canonical group")
            row = self.execute(
                connection,
                """INSERT INTO memberships(group_id, identity_id, member_role, source, source_ref, active, created_by)
                   VALUES (?, ?, ?, ?, ?, TRUE, ?)
                   ON CONFLICT(group_id, identity_id, member_role, source, source_ref) DO UPDATE SET active = TRUE
                   RETURNING id""",
                (group_id, identity_id, member_role, source, source_ref.strip(), created_by),
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
                """SELECT g.id, g.name, g.display_name, g.group_type, g.subject, g.base_class_name,
                          g.subject_subgroup, g.exam_track, g.provenance_source, g.provenance_ref,
                          g.canonical,
                           COUNT(DISTINCT m.identity_id) AS member_count,
                           COUNT(DISTINCT ta.teacher_identity_id) AS teacher_count
                      FROM groups g
                      LEFT JOIN memberships m ON m.group_id = g.id AND m.active IS TRUE
                        AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE) AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
                      LEFT JOIN teacher_assignments ta ON ta.group_id = g.id AND ta.active IS TRUE
                        AND (ta.valid_from IS NULL OR ta.valid_from <= CURRENT_DATE) AND (ta.valid_until IS NULL OR ta.valid_until >= CURRENT_DATE)
                     WHERE g.canonical IS TRUE
                    GROUP BY g.id, g.name, g.group_type
                    ORDER BY g.group_type, g.name""",
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                group = dict(row)
                members = self.execute(
                    connection,
                    """SELECT i.id, i.display_name, i.class_name, m.source, m.source_ref
                         FROM memberships m JOIN identities i ON i.id = m.identity_id
                        WHERE m.group_id = ? AND m.active IS TRUE AND i.status = 'active'
                          AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE)
                          AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
                        ORDER BY i.display_name""",
                    (row["id"],),
                ).fetchall()
                assignments = self.execute(
                    connection,
                    """SELECT ta.id, ta.subject, ta.source, ta.source_ref,
                              i.id AS teacher_identity_id, i.display_name AS teacher_name
                         FROM teacher_assignments ta
                         JOIN identities i ON i.id = ta.teacher_identity_id
                        WHERE ta.group_id = ? AND ta.active IS TRUE AND i.status = 'active'
                          AND (ta.valid_from IS NULL OR ta.valid_from <= CURRENT_DATE)
                          AND (ta.valid_until IS NULL OR ta.valid_until >= CURRENT_DATE)
                        ORDER BY i.display_name""",
                    (row["id"],),
                ).fetchall()
                students_by_id: dict[Any, dict[str, Any]] = {}
                for raw_member in members:
                    member = {**dict(raw_member), "is_manual": raw_member["source"] == "admin_override"}
                    member["membership_sources"] = [member.get("source")]
                    member["source_refs"] = [member.get("source_ref")] if member.get("source_ref") else []
                    existing_member = students_by_id.get(member["id"])
                    if existing_member is None:
                        students_by_id[member["id"]] = member
                    else:
                        existing_member["is_manual"] = existing_member["is_manual"] or member["is_manual"]
                        existing_member["membership_sources"] = sorted(set(existing_member["membership_sources"] + member["membership_sources"]))
                        existing_member["source_refs"] = sorted(set(existing_member["source_refs"] + member["source_refs"]))
                group["students"] = list(students_by_id.values())
                group["teacher_assignments"] = [dict(item) for item in assignments]
                group["student_count"] = int(group.get("member_count") or len(group["students"]))
                group["teacher_count"] = int(group.get("teacher_count") or len(group["teacher_assignments"]))
                group["relationship_kind"] = (
                    "base_class" if row["group_type"] == "class"
                    else "exam_profile" if row["group_type"] == "exam_track"
                    else "instructional" if row["group_type"] in {"subject_group", "instructional_group"}
                    else "other"
                )
                result.append(group)
            return result

    def list_groups_admin_summary(self) -> list[dict[str, Any]]:
        """Return the Groups collection without loading every roster row."""
        with self.connection() as connection:
            rows = self.execute(
                connection,
                """SELECT g.id, g.name, g.display_name, g.group_type, g.subject, g.base_class_name,
                          g.subject_subgroup, g.exam_track, g.provenance_source, g.provenance_ref,
                          g.canonical,
                          COUNT(DISTINCT m.identity_id) AS member_count,
                          COUNT(DISTINCT ta.teacher_identity_id) AS teacher_count
                     FROM groups g
                     LEFT JOIN memberships m ON m.group_id = g.id AND m.active IS TRUE
                       AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE)
                       AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
                     LEFT JOIN teacher_assignments ta ON ta.group_id = g.id AND ta.active IS TRUE
                       AND (ta.valid_from IS NULL OR ta.valid_from <= CURRENT_DATE)
                       AND (ta.valid_until IS NULL OR ta.valid_until >= CURRENT_DATE)
                    WHERE g.canonical IS TRUE
                    GROUP BY g.id, g.name, g.group_type
                    ORDER BY g.group_type, g.name""",
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["student_count"] = int(item.get("member_count") or 0)
                item["teacher_count"] = int(item.get("teacher_count") or 0)
                item["relationship_kind"] = (
                    "base_class" if item["group_type"] == "class"
                    else "exam_profile" if item["group_type"] == "exam_track"
                    else "instructional" if item["group_type"] in {"subject_group", "instructional_group"}
                    else "other"
                )
                result.append(item)
            return result

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
                    """INSERT INTO memberships(group_id, identity_id, member_role, source, source_ref, active, created_by)
                       VALUES (?, ?, ?, 'admin_override', ?, TRUE, ?)
                       ON CONFLICT(group_id, identity_id, member_role, source, source_ref)
                       DO UPDATE SET active = TRUE, created_by = excluded.created_by""",
                    (group_id, identity_id, member_role, f"membership_override:{row['id']}", actor_user_id),
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
            identity = self.execute(connection, "SELECT kind, status FROM identities WHERE id = ?", (teacher_identity_id,)).fetchone()
            group = self.execute(connection, "SELECT canonical FROM groups WHERE id = ?", (group_id,)).fetchone()
            if not identity or identity["kind"] != "teacher" or identity["status"] != "active":
                raise ValueError("Teacher assignment requires an active teacher identity")
            if not group or not group["canonical"]:
                raise ValueError("Teacher assignment requires a canonical group")
            row = self.execute(
                connection,
                """INSERT INTO teacher_assignments(teacher_identity_id, group_id, subject, base_class_name, subject_subgroup, classroom_course_id, exam_track, source, active, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'admin_override', ?, ?)
                   ON CONFLICT(teacher_identity_id, group_id, subject, capability, source, source_ref)
                   DO UPDATE SET base_class_name = excluded.base_class_name, subject_subgroup = excluded.subject_subgroup,
                     classroom_course_id = excluded.classroom_course_id, exam_track = excluded.exam_track,
                     active = excluded.active, created_by = excluded.created_by
                   RETURNING id""",
                (teacher_identity_id, group_id, subject.strip(), base_class_name, subject_subgroup, classroom_course_id, exam_track, active, actor_user_id),
            ).fetchone()
            return self.execute(connection, "SELECT * FROM teacher_assignments WHERE id = ?", (row["id"],)).fetchone()

    def set_homeroom_assignment(self, teacher_identity_id: Any, group_id: Any, active: bool, actor_user_id: Any) -> Any:
        with self.connection() as connection:
            identity = self.execute(connection, "SELECT kind, status FROM identities WHERE id = ?", (teacher_identity_id,)).fetchone()
            group = self.execute(connection, "SELECT group_type, canonical FROM groups WHERE id = ?", (group_id,)).fetchone()
            if not identity or identity["kind"] != "teacher" or identity["status"] != "active":
                raise ValueError("Homeroom assignment requires an active teacher identity")
            if not group or group["group_type"] != "class" or not group["canonical"]:
                raise ValueError("Homeroom assignment requires a canonical class group")
            row = self.execute(
                connection,
                """INSERT INTO homeroom_assignments(teacher_identity_id, class_group_id, source, active, created_by)
                   VALUES (?, ?, 'admin_override', ?, ?)
                   ON CONFLICT(teacher_identity_id, class_group_id, source, source_ref)
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
                       EXISTS (SELECT 1 FROM teacher_assignments ta WHERE ta.teacher_identity_id = u.identity_id AND ta.group_id = ? AND ta.active IS TRUE
                         AND (ta.valid_from IS NULL OR ta.valid_from <= CURRENT_DATE) AND (ta.valid_until IS NULL OR ta.valid_until >= CURRENT_DATE))
                       OR EXISTS (SELECT 1 FROM homeroom_assignments h WHERE h.teacher_identity_id = u.identity_id AND h.class_group_id = ? AND h.active IS TRUE
                         AND (h.valid_from IS NULL OR h.valid_from <= CURRENT_DATE) AND (h.valid_until IS NULL OR h.valid_until >= CURRENT_DATE))
                     )""",
                 (user_id, group_id, group_id),
            ).fetchone()
            return bool(row)

    def list_teacher_groups(self, user_id: Any) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self.execute(
                connection,
                """SELECT DISTINCT g.id, g.name, g.display_name, g.group_type, COALESCE(ta.subject, '') AS subject,
                          COALESCE(ta.base_class_name, '') AS base_class_name,
                          COALESCE(ta.subject_subgroup, '') AS subject_subgroup,
                          ta.classroom_course_id AS classroom_course_id,
                          COALESCE(ta.exam_track, '') AS exam_track,
                          CASE WHEN h.id IS NULL THEN FALSE ELSE TRUE END AS is_homeroom,
                           COUNT(DISTINCT sm.identity_id) AS student_count
                     FROM users u
                     JOIN groups g ON (
                        EXISTS (SELECT 1 FROM teacher_assignments x WHERE x.teacher_identity_id = u.identity_id AND x.group_id = g.id AND x.active IS TRUE
                          AND (x.valid_from IS NULL OR x.valid_from <= CURRENT_DATE) AND (x.valid_until IS NULL OR x.valid_until >= CURRENT_DATE))
                        OR EXISTS (SELECT 1 FROM homeroom_assignments hx WHERE hx.teacher_identity_id = u.identity_id AND hx.class_group_id = g.id AND hx.active IS TRUE
                          AND (hx.valid_from IS NULL OR hx.valid_from <= CURRENT_DATE) AND (hx.valid_until IS NULL OR hx.valid_until >= CURRENT_DATE))
                     )
                     LEFT JOIN teacher_assignments ta ON ta.teacher_identity_id = u.identity_id AND ta.group_id = g.id AND ta.active IS TRUE
                       AND (ta.valid_from IS NULL OR ta.valid_from <= CURRENT_DATE) AND (ta.valid_until IS NULL OR ta.valid_until >= CURRENT_DATE)
                     LEFT JOIN homeroom_assignments h ON h.teacher_identity_id = u.identity_id AND h.class_group_id = g.id AND h.active IS TRUE
                       AND (h.valid_from IS NULL OR h.valid_from <= CURRENT_DATE) AND (h.valid_until IS NULL OR h.valid_until >= CURRENT_DATE)
                      LEFT JOIN memberships sm ON sm.group_id = g.id AND sm.active IS TRUE AND sm.member_role = 'student'
                        AND (sm.valid_from IS NULL OR sm.valid_from <= CURRENT_DATE) AND (sm.valid_until IS NULL OR sm.valid_until >= CURRENT_DATE)
                    WHERE u.id = ?
                     GROUP BY g.id, g.name, g.display_name, g.group_type, ta.subject, ta.base_class_name, ta.subject_subgroup, ta.classroom_course_id, ta.exam_track, h.id
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
                       AND (m.valid_from IS NULL OR m.valid_from <= CURRENT_DATE)
                       AND (m.valid_until IS NULL OR m.valid_until >= CURRENT_DATE)
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
                     JOIN schedule_entries se ON se.audience = ga.audience AND se.archived IS FALSE
                      WHERE u.id = ? AND ga.archived IS FALSE AND {date_range} AND (ta.subject = '' OR ta.subject = se.subject)
                       AND (ta.valid_from IS NULL OR ta.valid_from <= se.lesson_date)
                       AND (ta.valid_until IS NULL OR ta.valid_until >= se.lesson_date)
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

    def list_school_sources_health(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self.execute(
                connection,
                """SELECT s.id, s.source_type, s.external_key, s.display_name, s.location_ref,
                          s.authority_status, s.active, s.created_at, s.updated_at,
                          sr.id AS last_run_id, sr.status AS last_run_status,
                          sr.started_at AS last_refresh, sr.finished_at AS last_finished_at,
                          sr.diagnostics AS last_diagnostics,
                          ss.fingerprint AS current_fingerprint, ss.observed_at AS snapshot_observed_at,
                          (SELECT COUNT(*) FROM school_source_records r WHERE r.snapshot_id = ss.id) AS record_count,
                          (SELECT COUNT(*) FROM school_resolution_issues ri WHERE ri.sync_run_id = sr.id AND ri.status = 'open') AS unresolved_count,
                          (SELECT COUNT(*) FROM school_candidate_changes cc WHERE cc.sync_run_id = sr.id AND cc.status IN ('pending','conflict','unresolved')) AS candidate_change_count
                     FROM school_sources s
                     LEFT JOIN school_sync_runs sr ON sr.id = (SELECT id FROM school_sync_runs x WHERE x.source_id = s.id ORDER BY x.id DESC LIMIT 1)
                     LEFT JOIN school_source_snapshots ss ON ss.id = (SELECT id FROM school_source_snapshots x WHERE x.source_id = s.id AND x.is_last_known_valid IS TRUE ORDER BY x.id DESC LIMIT 1)
                    ORDER BY s.active DESC, s.display_name""",
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                for key in ("last_diagnostics",):
                    if item.get(key):
                        try:
                            item[key] = json.loads(item[key])
                        except (TypeError, json.JSONDecodeError):
                            pass
                item["health"] = "error" if item.get("last_run_status") == "failed" else "attention" if (item.get("unresolved_count") or item.get("candidate_change_count")) else "healthy"
                result.append(item)
            return result

    def get_school_source_detail(self, source_id: Any) -> dict[str, Any] | None:
        with self.connection() as connection:
            source = self.execute(connection, "SELECT * FROM school_sources WHERE id = ?", (source_id,)).fetchone()
            if not source:
                return None
            runs = self.execute(connection, "SELECT * FROM school_sync_runs WHERE source_id = ? ORDER BY id DESC LIMIT 20", (source_id,)).fetchall()
            snapshots = self.execute(connection, "SELECT id, sync_run_id, previous_snapshot_id, fingerprint, observed_at, effective_from, effective_until, status, is_last_known_valid, created_at FROM school_source_snapshots WHERE source_id = ? ORDER BY id DESC LIMIT 20", (source_id,)).fetchall()
            records = self.execute(connection, """SELECT r.id, r.snapshot_id, r.record_key, r.source_ref, r.change_kind, r.parse_status, r.created_at
                                                   FROM school_source_records r JOIN school_source_snapshots ss ON ss.id = r.snapshot_id
                                                  WHERE ss.source_id = ? ORDER BY r.id DESC LIMIT 200""", (source_id,)).fetchall()
            issues = self.execute(connection, "SELECT id, sync_run_id, source_record_id, candidate_change_id, issue_type, status, details, evidence, resolution, created_at, resolved_at FROM school_resolution_issues WHERE sync_run_id IN (SELECT id FROM school_sync_runs WHERE source_id = ?) ORDER BY id DESC LIMIT 200", (source_id,)).fetchall()
            mappings = self.execute(connection, "SELECT id, external_key, mapping_type, identity_id, group_id, classroom_course_id, canonical_value, status, manually_confirmed, valid_from, valid_until, supersedes_mapping_id, created_at, updated_at FROM school_source_mappings WHERE source_id = ? ORDER BY id DESC LIMIT 200", (source_id,)).fetchall()
            return {"source": dict(source), "runs": [dict(row) for row in runs], "snapshots": [dict(row) for row in snapshots], "records": [dict(row) for row in records], "issues": [dict(row) for row in issues], "mappings": [dict(row) for row in mappings]}

    def list_resolution_issues(
        self,
        status: str | None = None,
        issue_type: str | None = None,
        source_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if status:
            clauses.append("ri.status = ?")
            params.append(status)
        if issue_type:
            clauses.append("ri.issue_type = ?")
            params.append(issue_type)
        if source_id:
            clauses.append("sr.source_id = ?")
            params.append(source_id)
        params.append(max(1, min(limit, 500)))
        with self.connection() as connection:
            rows = self.execute(
                connection,
                f"""SELECT ri.id, ri.sync_run_id, ri.source_record_id, ri.candidate_change_id,
                              ri.issue_type, ri.status, ri.details, ri.evidence, ri.resolution,
                              ri.created_at, ri.resolved_at,
                              ss.id AS source_id, ss.display_name AS source_name,
                              sr.status AS sync_status, sr.finished_at AS sync_finished_at,
                              rec.record_key AS source_record_key,
                              cc.status AS candidate_status, cc.entity_type, cc.natural_key
                         FROM school_resolution_issues ri
                         LEFT JOIN school_sync_runs sr ON sr.id = ri.sync_run_id
                         LEFT JOIN school_sources ss ON ss.id = sr.source_id
                         LEFT JOIN school_source_records rec ON rec.id = ri.source_record_id
                         LEFT JOIN school_candidate_changes cc ON cc.id = ri.candidate_change_id
                        WHERE {' AND '.join(clauses)}
                        ORDER BY CASE ri.status WHEN 'open' THEN 0 WHEN 'resolved' THEN 1 ELSE 2 END,
                                 ri.created_at DESC, ri.id DESC LIMIT ?""",
                tuple(params),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                for key in ("details", "evidence", "resolution"):
                    item[key] = self._decode_json_value(item.get(key))
                result.append(item)
            return result

    def list_candidate_changes(
        self,
        status: str | None = None,
        entity_type: str | None = None,
        source_id: str | None = None,
        sync_run_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if status:
            clauses.append("cc.status = ?")
            params.append(status)
        if entity_type:
            clauses.append("cc.entity_type = ?")
            params.append(entity_type)
        if source_id:
            clauses.append("sr.source_id = ?")
            params.append(source_id)
        if sync_run_id:
            clauses.append("cc.sync_run_id = ?")
            params.append(sync_run_id)
        params.append(max(1, min(limit, 500)))
        with self.connection() as connection:
            rows = self.execute(
                connection,
                f"""SELECT cc.id, cc.sync_run_id, cc.source_record_id, cc.change_type,
                              cc.entity_type, cc.natural_key, cc.proposed_payload, cc.evidence,
                              cc.status, cc.manual_decision, cc.decision_at, cc.applied_at, cc.created_at,
                              ss.id AS source_id, ss.display_name AS source_name,
                              sr.status AS sync_status, sr.finished_at AS sync_finished_at,
                              rec.record_key AS source_record_key
                         FROM school_candidate_changes cc
                         LEFT JOIN school_sync_runs sr ON sr.id = cc.sync_run_id
                         LEFT JOIN school_sources ss ON ss.id = sr.source_id
                         LEFT JOIN school_source_records rec ON rec.id = cc.source_record_id
                        WHERE {' AND '.join(clauses)}
                        ORDER BY CASE cc.status WHEN 'pending' THEN 0 WHEN 'unresolved' THEN 1 WHEN 'conflict' THEN 2 ELSE 3 END,
                                 cc.created_at DESC, cc.id DESC LIMIT ?""",
                tuple(params),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                for key in ("proposed_payload", "evidence"):
                    item[key] = self._decode_json_value(item.get(key))
                result.append(item)
            return result

    def schedule_v1_overview(self, week_start: str | None = None) -> dict[str, Any]:
        with self.connection() as connection:
            source = self.execute(connection, "SELECT id, display_name, external_key, location_ref, authority_status, configuration FROM school_sources WHERE source_type='schedule' ORDER BY id DESC LIMIT 1").fetchone()
            snapshot = self.execute(connection, "SELECT id, sync_run_id, fingerprint, observed_at, status FROM school_source_snapshots WHERE source_id=? AND is_last_known_valid IS TRUE ORDER BY id DESC LIMIT 1", (source["id"],)).fetchone() if source else None
            tabs = [dict(x) for x in self.execute(connection, "SELECT sheet_id,title,classification,week_start,week_end FROM schedule_source_tabs WHERE source_id=? ORDER BY title", (source["id"],)).fetchall()] if source else []
            lesson_rows = self.execute(connection, "SELECT week_start,resolution_status,diff_status,modifiers FROM schedule_lessons WHERE source_snapshot_id=? AND version_kind='weekly'", (snapshot["id"],)).fetchall() if snapshot else []
            week_counts: dict[str, int] = {}
            for row in lesson_rows:
                modifiers = self._decode_json_value(row["modifiers"]) or {}
                if row["week_start"] and row["resolution_status"] not in {"NON_LESSON", "EXCLUDED"} and not modifiers.get("synthetic_cancelled"):
                    key = str(row["week_start"]); week_counts[key] = week_counts.get(key, 0) + 1
            weeks = [{"week_start": key, "lessons": value} for key, value in sorted(week_counts.items(), reverse=True)]
            selected_week = str(week_start or (weeks[0]["week_start"] if weeks else ""))
            selected_rows = [row for row in lesson_rows if str(row["week_start"] or "") == selected_week] if selected_week else []
            status_counts: dict[str, int] = {}
            diff_counts: dict[str, int] = {}
            for row in selected_rows:
                status_counts[str(row["resolution_status"])] = status_counts.get(str(row["resolution_status"]), 0) + 1
                diff_counts[str(row["diff_status"])] = diff_counts.get(str(row["diff_status"]), 0) + 1
            # Keep the headline issue count aligned with the selected week just
            # like the four status counters.  The underlying issue journal still
            # retains every week in the snapshot.
            issue_count = sum(status_counts.get(key, 0) for key in ("WARNING", "UNRESOLVED", "CONFLICT"))
            summary = {"lessons": week_counts.get(selected_week, 0), "issues": issue_count,
                       "resolved": status_counts.get("RESOLVED", 0), "warning": status_counts.get("WARNING", 0),
                       "unresolved": status_counts.get("UNRESOLVED", 0), "conflict": status_counts.get("CONFLICT", 0),
                       "special_event": status_counts.get("SPECIAL_EVENT", 0), "non_lesson": status_counts.get("NON_LESSON", 0),
                       "excluded": status_counts.get("EXCLUDED", 0),
                       "added": diff_counts.get("ADDED", 0), "changed": diff_counts.get("MODIFIED", 0) + diff_counts.get("REPLACED", 0),
                       "removed": diff_counts.get("CANCELLED", 0), "same": diff_counts.get("SAME_AS_BASELINE", 0),
                       "diffs": diff_counts}
            last_sync = self.execute(connection, "SELECT id,status,finished_at,diagnostics FROM school_sync_runs WHERE source_id=? ORDER BY id DESC LIMIT 1", (source["id"],)).fetchone() if source else None
            source_item = dict(source) if source else None
            if source_item:
                source_item["name"] = source_item["display_name"]
                source_item["spreadsheet_title"] = source_item["display_name"]
                template = next((tab for tab in tabs if tab["classification"] == "template"), None)
                source_item["template"] = template["title"] if template else None
                source_item["configuration"] = self._decode_json_value(source_item.get("configuration"))
            return {"source": source_item, "snapshot": dict(snapshot) if snapshot else None, "tabs": tabs, "weeks": weeks, "selected_week": selected_week or None,
                    "summary": summary, "last_sync": last_sync["finished_at"] if last_sync else None,
                    "last_sync_detail": {**dict(last_sync), "diagnostics": self._decode_json_value(last_sync["diagnostics"])} if last_sync else None,
                    "auth_state": "connected" if source else "not_configured"}

    def schedule_v1_tabs(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            source = self.execute(connection, "SELECT id FROM school_sources WHERE source_type='schedule' ORDER BY id DESC LIMIT 1").fetchone()
            return [dict(row) for row in self.execute(connection, "SELECT sheet_id,title,classification,week_start,week_end,updated_at FROM schedule_source_tabs WHERE source_id=? ORDER BY title", (source["id"],)).fetchall()] if source else []

    def schedule_v1_weeks(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            snapshot = self.execute(connection, "SELECT id FROM school_source_snapshots WHERE is_last_known_valid IS TRUE AND source_id IN (SELECT id FROM school_sources WHERE source_type='schedule') ORDER BY id DESC LIMIT 1").fetchone()
            return [dict(row) for row in self.execute(connection, "SELECT week_start,COUNT(*) AS lessons FROM schedule_lessons WHERE source_snapshot_id=? AND version_kind='weekly' GROUP BY week_start ORDER BY week_start DESC", (snapshot["id"],)).fetchall()] if snapshot else []

    def schedule_v1_lessons(self, status: str | None = None, week_start: str | None = None, teacher_id: str | None = None, group_id: str | None = None, lesson_type: str | None = None) -> list[dict[str, Any]]:
        with self.connection() as connection:
            query = "SELECT * FROM schedule_lessons WHERE version_kind='weekly' AND resolution_status <> 'EXCLUDED'"; params: list[Any] = []
            query += " AND source_snapshot_id IN (SELECT id FROM school_source_snapshots WHERE is_last_known_valid IS TRUE)"
            if status: query += " AND resolution_status=?"; params.append(status)
            if week_start: query += " AND week_start=?"; params.append(week_start)
            if lesson_type: query += " AND (lesson_kind=? OR activity_type=?)"; params.extend([lesson_type, lesson_type])
            query += " ORDER BY lesson_date, start_time, id"
            rows = [dict(row) for row in self.execute(connection, query, tuple(params)).fetchall()]
            identity_rows = self.execute(connection, "SELECT id,display_name FROM identities WHERE kind='teacher'").fetchall()
            group_rows = self.execute(connection, "SELECT id,COALESCE(display_name,name) AS name FROM groups").fetchall()
            identities = {str(row["id"]): row["display_name"] for row in identity_rows}
            groups = {str(row["id"]): row["name"] for row in group_rows}
            result = []
            for item in rows:
                for key in ("resolved_identity_ids", "resolved_group_ids", "modifiers", "evidence", "merge_data", "baseline_data", "diagnostics", "raw_payload"):
                    item[key] = self._decode_json_value(item.get(key))
                teacher_ids = [str(value) for value in (item.get("resolved_identity_ids") or [])]
                group_ids = [str(value) for value in (item.get("resolved_group_ids") or [])]
                if teacher_id and str(teacher_id) not in teacher_ids:
                    continue
                if group_id and str(group_id) not in group_ids and str(group_id).casefold().strip() != str(item.get("audience") or "").casefold().strip():
                    continue
                item.update({"status": item["resolution_status"], "teacher_id": teacher_ids[0] if len(teacher_ids) == 1 else None,
                             "teacher_name": identities.get(teacher_ids[0]) if len(teacher_ids) == 1 else item.get("teacher_hint"),
                             "teacher": identities.get(teacher_ids[0]) if len(teacher_ids) == 1 else item.get("teacher_hint"),
                             "group_id": group_ids[0] if len(group_ids) == 1 else None,
                             "group_name": groups.get(group_ids[0]) if len(group_ids) == 1 else item.get("audience"),
                             "raw_text": (item.get("raw_payload") or {}).get("raw_text", ""), "cell": item.get("source_cell"),
                             "color": item.get("source_color"), "parsed": {"subject": item.get("subject"), "teacher_hint": item.get("teacher_hint"), "room": item.get("room"), "activity_type": item.get("activity_type"), "modifiers": item.get("modifiers")},
                             "resolved": {"teacher_ids": teacher_ids, "group_ids": group_ids}, "baseline": item.get("baseline_data"),
                             "diff": item.get("diff_status"), "issue": item.get("issue_reason")})
                result.append(item)
            return result

    def schedule_v1_issues(self, issue_type: str | None = None, week_start: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        with self.connection() as connection:
            query = "SELECT * FROM school_resolution_issues WHERE (substr(issue_type,1,8)='schedule' OR issue_type='unknown_schedule_tab') AND sync_run_id IN (SELECT sync_run_id FROM school_source_snapshots WHERE is_last_known_valid IS TRUE AND source_id IN (SELECT id FROM school_sources WHERE source_type='schedule'))"; params: list[Any] = []
            if issue_type: query += " AND issue_type=?"; params.append(issue_type)
            if status: query += " AND status=?"; params.append(status)
            query += " ORDER BY id DESC LIMIT 500"
            result = []
            for row in self.execute(connection, query, tuple(params)).fetchall():
                item = dict(row)
                item["detail"] = self._decode_json_value(item.get("details"))
                item["evidence"] = self._decode_json_value(item.get("evidence"))
                if week_start and str((item["detail"] or {}).get("week_start") or "") != str(week_start):
                    continue
                item["reason"] = (item["detail"] or {}).get("reason")
                item["title"] = (item["detail"] or {}).get("subject") or item.get("issue_type")
                result.append(item)
            return result

    def schedule_allocation_qa(self, week_start: str, grade: str | None = None,
                               student_id: str | None = None, teacher_id: str | None = None) -> dict[str, Any]:
        with self.connection() as connection:
            snapshot = self.execute(connection, """SELECT id FROM school_source_snapshots
                WHERE is_last_known_valid IS TRUE AND source_id IN
                  (SELECT id FROM school_sources WHERE source_type='schedule')
                ORDER BY id DESC LIMIT 1""").fetchone()
            if not snapshot:
                return {"summary": {"slots": 0, "complete": 0, "with_unassigned": 0, "with_conflicts": 0},
                        "slots": [], "students": [], "teachers": [], "groups": [], "student_preview": [], "teacher_preview": []}
            params: list[Any] = [snapshot["id"], week_start]
            grade_clause = ""
            if grade:
                grade_clause = " AND grade_scope=?"; params.append(grade)
            audience_rows = [dict(row) for row in self.execute(connection, f"""SELECT sla.*,sl.subject,sl.teacher_hint,sl.room,
                       sl.activity_type,sl.source_cell,sl.audience,sl.resolved_identity_ids
                  FROM schedule_lesson_audiences sla JOIN schedule_lessons sl ON sl.id=sla.lesson_id
                 WHERE sla.source_snapshot_id=? AND sla.week_start=?{grade_clause}
                 ORDER BY sla.lesson_date,sla.start_time,sla.grade_scope,sl.source_cell,sl.id""", tuple(params)).fetchall()]
            group_names = {str(row["id"]): row["display_name"] for row in self.execute(connection, "SELECT id,COALESCE(display_name,name) AS display_name FROM groups WHERE canonical IS TRUE").fetchall()}
            lesson_labels: dict[str, str] = {}
            allocation_rows = [dict(row) for row in self.execute(connection, f"""SELECT sa.*,i.display_name AS student_name,
                       sl.subject,sl.teacher_hint,sl.room,sl.activity_type,sl.source_cell
                  FROM schedule_student_allocations sa JOIN identities i ON i.id=sa.student_identity_id
                  LEFT JOIN schedule_lessons sl ON sl.id=sa.lesson_id
                 WHERE sa.source_snapshot_id=? AND sa.week_start=?{grade_clause}
                 ORDER BY sa.lesson_date,sa.start_time,sa.grade_scope,i.display_name""", tuple(params)).fetchall()]
            slots: dict[tuple[str, str, str], dict[str, Any]] = {}
            for row in audience_rows:
                key = (str(row["lesson_date"]), str(row["start_time"]), str(row["grade_scope"]))
                slot = slots.setdefault(key, {"lesson_date": key[0], "start_time": key[1], "grade": key[2],
                                              "activities": [], "unassigned": [], "conflicts": [], "students": 0})
                for json_key in ("resolved_group_ids", "provenance", "resolved_identity_ids"):
                    row[json_key] = self._decode_json_value(row.get(json_key)) or ([] if json_key != "provenance" else {})
                label = str(row.get("subject") or row.get("activity_type") or "Активность")
                if row.get("audience"):
                    label = f"{label} ({row['audience']})"
                lesson_labels[str(row["lesson_id"])] = label
                slot["activities"].append({**row, "group_names": [group_names.get(str(group_id), str(group_id)) for group_id in row["resolved_group_ids"]], "students": [], "student_count": 0})
            for row in allocation_rows:
                key = (str(row["lesson_date"]), str(row["start_time"]), str(row["grade_scope"]))
                slot = slots.setdefault(key, {"lesson_date": key[0], "start_time": key[1], "grade": key[2],
                                              "activities": [], "unassigned": [], "conflicts": [], "students": 0})
                row["provenance"] = self._decode_json_value(row.get("provenance")) or {}
                student = {"id": str(row["student_identity_id"]), "name": row["student_name"], "reason": row["reason"],
                           "allocation_kind": row["allocation_kind"], "provenance": row["provenance"]}
                if row["status"] == "conflict":
                    candidate_ids = row["provenance"].get("candidate_lesson_ids") or []
                    student["conflicting_activities"] = [lesson_labels.get(str(lesson_id), str(lesson_id)) for lesson_id in candidate_ids]
                slot["students"] += 1
                if row["status"] == "unassigned":
                    slot["unassigned"].append(student)
                elif row["status"] == "conflict":
                    slot["conflicts"].append(student)
                elif row.get("lesson_id"):
                    activity = next((item for item in slot["activities"] if str(item["lesson_id"]) == str(row["lesson_id"])), None)
                    if activity:
                        activity["students"].append(student); activity["student_count"] += 1
                elif row["allocation_kind"] == "no_lesson":
                    activity = next((item for item in slot["activities"] if item.get("synthetic_kind") == row["allocation_kind"]), None)
                    if not activity:
                        activity = {"lesson_id": None, "subject": "Нет урока",
                                    "activity_type": row["allocation_kind"], "audience_kind": row["allocation_kind"], "rule_reason": row["reason"],
                                    "status": "resolved", "students": [], "student_count": 0, "synthetic_kind": row["allocation_kind"]}
                        slot["activities"].append(activity)
                    activity["students"].append(student); activity["student_count"] += 1
            slot_list = list(slots.values())
            for slot in slot_list:
                slot["status"] = "conflict" if slot["conflicts"] else "unassigned" if slot["unassigned"] else "complete"
            student_choices = [dict(row) for row in self.execute(connection, """SELECT DISTINCT i.id,i.display_name
                FROM schedule_student_allocations sa JOIN identities i ON i.id=sa.student_identity_id
                WHERE sa.source_snapshot_id=? AND sa.week_start=? ORDER BY i.display_name""", (snapshot["id"], week_start)).fetchall()]
            teacher_choices = [dict(row) for row in self.execute(connection, "SELECT id,display_name FROM identities WHERE kind='teacher' AND status='active' ORDER BY display_name").fetchall()]
            group_choices = [dict(row) for row in self.execute(connection, """SELECT id,COALESCE(display_name,name) AS display_name,
                group_type,subject,base_class_name,subject_subgroup,exam_track FROM groups
                WHERE canonical IS TRUE ORDER BY COALESCE(display_name,name)""").fetchall()]
            student_preview = []
            if student_id:
                student_preview = [dict(row) for row in self.execute(connection, """SELECT sa.lesson_date,sa.start_time,sa.end_time,sa.allocation_kind,
                           sa.status,sa.reason,sa.provenance,sl.subject,sl.teacher_hint,sl.room
                      FROM schedule_student_allocations sa LEFT JOIN schedule_lessons sl ON sl.id=sa.lesson_id
                     WHERE sa.source_snapshot_id=? AND sa.week_start=? AND sa.student_identity_id=?
                       AND sa.allocation_kind IN ('lesson','no_lesson') AND sa.status='assigned'
                     ORDER BY sa.lesson_date,sa.start_time""", (snapshot["id"], week_start, student_id)).fetchall()]
                for item in student_preview:
                    item["provenance"] = self._decode_json_value(item.get("provenance")) or {}
            teacher_preview = self.schedule_v1_lessons(week_start=week_start, teacher_id=teacher_id) if teacher_id else []
            return {"summary": {"slots": len(slot_list), "complete": sum(1 for slot in slot_list if slot["status"] == "complete"),
                                "with_unassigned": sum(1 for slot in slot_list if slot["unassigned"]),
                                "with_conflicts": sum(1 for slot in slot_list if slot["conflicts"])},
                    "slots": slot_list, "students": student_choices, "teachers": teacher_choices, "groups": group_choices,
                    "student_preview": student_preview, "teacher_preview": teacher_preview}

    def schedule_v1_mapping_context(self) -> dict[str, Any]:
        with self.connection() as connection:
            source = self.execute(connection, "SELECT id FROM school_sources WHERE source_type='schedule' ORDER BY id DESC LIMIT 1").fetchone()
            if not source:
                return {"mappings": [], "teachers": [], "groups": []}
            rows = self.execute(connection, """SELECT m.id,m.external_key,m.mapping_type,m.identity_id,m.group_id,m.canonical_value,m.status,
                       m.manually_confirmed,m.valid_from,m.valid_until,m.created_at,m.updated_at,m.supersedes_mapping_id,
                       i.display_name AS identity_name,COALESCE(g.display_name,g.name) AS group_name
                  FROM school_source_mappings m
                  LEFT JOIN identities i ON i.id=m.identity_id
                  LEFT JOIN groups g ON g.id=m.group_id
                 WHERE m.source_id=? ORDER BY (m.valid_until IS NULL) DESC,m.updated_at DESC,m.created_at DESC""", (source["id"],)).fetchall()
            teachers = self.execute(connection, "SELECT id,display_name FROM identities WHERE kind='teacher' AND status='active' ORDER BY display_name").fetchall()
            groups = self.execute(connection, "SELECT id,COALESCE(display_name,name) AS name,group_type,subject,base_class_name,subject_subgroup,exam_track FROM groups WHERE canonical IS TRUE ORDER BY COALESCE(display_name,name)").fetchall()
            return {"mappings": [dict(row) for row in rows], "teachers": [dict(row) for row in teachers], "groups": [dict(row) for row in groups]}

    def save_schedule_v1_mapping(self, mapping_type: str, external_key: str, *, target_id: Any | None = None,
                                 canonical_value: str | None = None, actor_user_id: Any | None = None) -> dict[str, Any]:
        if mapping_type not in {"identity", "group", "subject", "audience_rule"}:
            raise ValueError("Unsupported schedule mapping type")
        key = external_key.strip()
        if not key:
            raise ValueError("Schedule mapping key is required")
        target_column = "identity_id" if mapping_type == "identity" else "group_id" if mapping_type == "group" else "canonical_value"
        target_value = (canonical_value or "").strip() if mapping_type in {"subject", "audience_rule"} else target_id
        if not target_value:
            raise ValueError("Schedule mapping target is required")
        with self.connection() as connection:
            source = self.execute(connection, "SELECT id FROM school_sources WHERE source_type='schedule' ORDER BY id DESC LIMIT 1").fetchone()
            if not source:
                raise ValueError("Schedule source is not configured")
            if mapping_type == "identity" and not self.execute(connection, "SELECT id FROM identities WHERE id=? AND kind='teacher' AND status='active'", (target_value,)).fetchone():
                raise ValueError("Selected teacher was not found")
            if mapping_type == "group" and not self.execute(connection, "SELECT id FROM groups WHERE id=? AND canonical IS TRUE", (target_value,)).fetchone():
                raise ValueError("Selected group was not found")
            if mapping_type == "audience_rule":
                try:
                    rule = json.loads(str(target_value))
                except json.JSONDecodeError as error:
                    raise ValueError("Audience rule must be valid JSON") from error
                if not isinstance(rule, dict):
                    raise ValueError("Audience rule must be an object")
                decision_type = str(rule.get("decision_type") or "canonical_group")
                allowed = {"canonical_group", "groups", "group", "base_class", "parallel", "complement", "window", "no_lesson", "source_context", "ignore_source", "end_of_day", "skip", "lunch", "break"}
                if decision_type not in allowed:
                    raise ValueError("Unsupported audience decision")
                group_ids = list(rule.get("group_ids") or [])
                if rule.get("group_id") and not group_ids:
                    group_ids = [rule["group_id"]]
                if decision_type in {"canonical_group", "groups", "group", "base_class"} and not group_ids:
                    raise ValueError("Audience rule target group was not found")
                if group_ids:
                    placeholders = ",".join("?" for _ in group_ids)
                    found = self.execute(connection, f"SELECT COUNT(*) AS value FROM groups WHERE canonical IS TRUE AND id IN ({placeholders})", tuple(group_ids)).fetchone()
                    if int(found["value"] or 0) != len(set(str(value) for value in group_ids)):
                        raise ValueError("Audience rule target group was not found")
            current = self.execute(connection, "SELECT * FROM school_source_mappings WHERE source_id=? AND mapping_type=? AND LOWER(TRIM(external_key))=LOWER(?) AND valid_until IS NULL AND status<>'revoked' ORDER BY id DESC LIMIT 1", (source["id"], mapping_type, key)).fetchone()
            if current and str(current[target_column] or "") == str(target_value):
                return dict(current)
            if current:
                self.execute(connection, "UPDATE school_source_mappings SET valid_until=CURRENT_DATE,updated_at=CURRENT_TIMESTAMP WHERE id=?", (current["id"],))
            evidence = json.dumps({"source": "admin_schedule_reconciliation", "external_key": key}, ensure_ascii=False)
            evidence_sql = "?::jsonb" if self.database_url else "?"
            columns = {"identity_id": None, "group_id": None, "canonical_value": None}
            columns[target_column] = target_value
            row = self.execute(connection, f"""INSERT INTO school_source_mappings(source_id,external_key,mapping_type,identity_id,group_id,canonical_value,status,manually_confirmed,evidence,created_by,supersedes_mapping_id)
                VALUES (?,?,?,?,?,?,'confirmed',TRUE,{evidence_sql},?,?) RETURNING *""",
                (source["id"], key, mapping_type, columns["identity_id"], columns["group_id"], columns["canonical_value"], evidence, actor_user_id, current["id"] if current else None)).fetchone()
            return dict(row)

    def retire_schedule_v1_mapping(self, mapping_id: Any) -> bool:
        with self.connection() as connection:
            row = self.execute(connection, "UPDATE school_source_mappings SET valid_until=CURRENT_DATE,updated_at=CURRENT_TIMESTAMP WHERE id=? AND valid_until IS NULL RETURNING id", (mapping_id,)).fetchone()
            return bool(row)

    def get_group_admin(self, group_id: Any) -> dict[str, Any] | None:
        groups = self.list_groups_admin()
        group = next((item for item in groups if str(item.get("id")) == str(group_id)), None)
        if not group:
            return None
        group["history"] = []
        return group

    def system_health(self) -> dict[str, Any]:
        with self.connection() as connection:
            tables = ["users", "identities", "groups", "memberships", "school_sources", "school_sync_runs", "school_candidate_changes", "school_resolution_issues", "audit_log"]
            counts = {}
            for table in tables:
                counts[table] = int(self.execute(connection, f"SELECT COUNT(*) AS value FROM {table}").fetchone()["value"])
            required = {"groups": {"display_name", "canonical"}, "schedule_syncs": {"archived"}, "schedule_entries": {"archived", "resolved_audience"}}
            schema = {}
            for table, names in required.items():
                if self.database_url:
                    columns = {row["column_name"] for row in self.execute(connection, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s", (table,)).fetchall()}
                else:
                    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
                schema[table] = {"ok": names.issubset(columns), "required": sorted(names)}
            candidate_counts = {}
            for status in ("pending", "approved", "rejected", "applied", "unresolved", "conflict"):
                candidate_counts[status] = int(self.execute(connection, "SELECT COUNT(*) AS value FROM school_candidate_changes WHERE status = ?", (status,)).fetchone()["value"])
            issue_counts = {}
            for status in ("open", "resolved", "ignored"):
                issue_counts[status] = int(self.execute(connection, "SELECT COUNT(*) AS value FROM school_resolution_issues WHERE status = ?", (status,)).fetchone()["value"])
            return {"environment": "local_sqlite" if not self.database_url else "postgres", "database": "configured", "counts": counts, "semantics": {"candidate_changes": {"total": counts["school_candidate_changes"], "by_status": candidate_counts, "historical": candidate_counts["applied"], "active": candidate_counts["pending"] + candidate_counts["approved"] + candidate_counts["conflict"] + candidate_counts["unresolved"]}, "resolution_issues": {"total": counts["school_resolution_issues"], "by_status": issue_counts, "active": issue_counts["open"], "historical": issue_counts["resolved"] + issue_counts["ignored"]}}, "schema": schema, "schema_status": "current" if all(item["ok"] for item in schema.values()) else "attention"}

    def admin_overview(self) -> dict[str, Any]:
        with self.connection() as connection:
            queries = {
                "pending_claims": "SELECT COUNT(*) AS value FROM identity_claims WHERE status = 'pending'",
                "users": "SELECT COUNT(*) AS value FROM users",
                "groups": "SELECT COUNT(*) AS value FROM groups",
                "parse_issues": "SELECT COUNT(*) AS value FROM schedule_entries WHERE archived IS FALSE AND parse_status IN ('partial','ambiguous','failed')",
                "journal_results": "SELECT COUNT(*) AS value FROM journal_results",
                "unlinked_accounts": "SELECT COUNT(*) AS value FROM users WHERE identity_id IS NULL",
                "identity_conflicts": "SELECT COUNT(*) AS value FROM identity_claims WHERE status = 'identity_conflict'",
            }
            result = {name: int(self.execute(connection, sql).fetchone()["value"]) for name, sql in queries.items()}
            for key, sql in {
                "canonical_students": "SELECT COUNT(*) AS value FROM identities WHERE kind = 'student' AND status = 'active'",
                "canonical_teachers": "SELECT COUNT(*) AS value FROM identities WHERE kind = 'teacher' AND status = 'active'",
                "app_accounts": "SELECT COUNT(*) AS value FROM users",
                "active_issues": "SELECT COUNT(*) AS value FROM school_resolution_issues WHERE status = 'open'",
                "resolved_issues": "SELECT COUNT(*) AS value FROM school_resolution_issues WHERE status = 'resolved'",
                "candidate_changes_total": "SELECT COUNT(*) AS value FROM school_candidate_changes",
                "candidate_changes_historical": "SELECT COUNT(*) AS value FROM school_candidate_changes WHERE status = 'applied'",
                "candidate_changes_unresolved": "SELECT COUNT(*) AS value FROM school_candidate_changes WHERE status = 'unresolved'",
                "candidate_changes_pending": "SELECT COUNT(*) AS value FROM school_candidate_changes WHERE status IN ('pending','approved','conflict')",
                "source_count": "SELECT COUNT(*) AS value FROM school_sources",
                "sync_runs_total": "SELECT COUNT(*) AS value FROM school_sync_runs",
            }.items():
                result[key] = int(self.execute(connection, sql).fetchone()["value"])
            latest_sync = self.execute(connection, "SELECT MAX(finished_at) AS value FROM school_sync_runs WHERE status = 'applied'").fetchone()["value"]
            result["last_successful_sync"] = latest_sync
            latest_run = self.execute(connection, "SELECT status, created_at, payload FROM admin_reconciliation_runs ORDER BY id DESC LIMIT 1").fetchone()
            result["last_reconciliation_status"] = latest_run["status"] if latest_run else "not_run"
            result["last_reconciliation_at"] = latest_run["created_at"] if latest_run else None
            result["protected_cases"] = 0
            if latest_run:
                payload = self._decode_json_value(latest_run["payload"])
                summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
                result["protected_cases"] = int(summary.get("PROTECTED", 0) or 0)
            return result

    def get_teacher_profile(self, user_id: Any) -> dict[str, Any] | None:
        with self.connection() as connection:
            user = self.execute(
                connection,
                """SELECT u.id, u.identity_id, i.display_name, i.class_name
                     FROM users u JOIN identities i ON i.id = u.identity_id
                    WHERE u.id = ? AND i.kind = 'teacher'""",
                (user_id,),
            ).fetchone()
            if not user:
                return None
        return {"user": dict(user), "groups": self.list_teacher_groups(user_id)}

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
                    WHERE journal_group_mappings.source <> 'admin_override' OR excluded.source = 'admin_override'
                    RETURNING id""",
                (source_id, group_marker, group_id, base_class_name, subject_subgroup, classroom_course_id, exam_track, source, actor_user_id),
            ).fetchone()
            if row:
                return self.execute(connection, "SELECT * FROM journal_group_mappings WHERE id = ?", (row["id"],)).fetchone()
            return self.execute(connection, "SELECT * FROM journal_group_mappings WHERE source_id = ? AND group_marker = ?", (source_id, group_marker)).fetchone()

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
                           AND (ta.valid_from IS NULL OR ta.valid_from <= CURRENT_DATE)
                           AND (ta.valid_until IS NULL OR ta.valid_until >= CURRENT_DATE)
                           AND (ta.subject = '' OR lower(ta.subject) = lower(js.subject))
                           AND (
                             ta.group_id = gm.group_id
                             OR (ta.subject_subgroup IS NOT NULL AND ta.subject_subgroup = gm.subject_subgroup)
                           )
                           AND (ta.base_class_name IS NULL OR ta.base_class_name = gm.base_class_name)
                           AND (ta.classroom_course_id IS NULL OR ta.classroom_course_id = gm.classroom_course_id)
                           AND (ta.exam_track IS NULL OR ta.exam_track = gm.exam_track)
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
