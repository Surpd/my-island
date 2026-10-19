"""Local school experiment storage with a transaction-guarded canonical source.

The normal local school workspace reads the canonical source and keeps drafts
in a separate local SQLite. No directory rows or student memberships are copied.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from dotenv import dotenv_values
import json
from threading import Lock
from time import monotonic
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from typing import Any, Iterator

from backend.database import Database
from backend.services.canonical_schedule import canonical_version, project_teacher, read_canonical_template
from backend.services.schedule_experiments import (
    _canonical_payload, create_experiment, get_experiment, list_experiments,
)


ROOT = Path(__file__).resolve().parents[2]
SCHOOL_DB = ROOT / ".local-test" / "school-experiments.sqlite3"
EXPECTED_PROJECT = "kmrcbewmxeqqxfauiwyy"
EXPECTED_SPREADSHEET = "1j5dI2VbT0RoW2A5ZkCpMYVdt7cSY4ZjNxQaMWtu--Fo"
EXPECTED_SHEET = "638874869"
SCHOOL_DRAFT_NAME = "ШКОЛА · Реальный шаблон 2026/27 · На проверке"
# Confirmed by the school on 2026-10-14. This aliases only the administrative
# class label; Math A/B/C and every instructional group retain their own IDs.
SCHOOL_CLASS_ALIASES = {"9-ан": "9-а"}
_AUDIENCE_SNAPSHOT_TTL_SECONDS = 300
_audience_snapshot_lock = Lock()
_audience_snapshot: tuple[str, str, float, dict[str, Any]] | None = None


def cached_audience_snapshot(reference_date: date, source: Database | None = None) -> dict[str, Any] | None:
    """Return the directory snapshot already loaded for the current school UI."""
    source_key = str(getattr(source, "database_url", None) or getattr(source, "path", ""))
    with _audience_snapshot_lock:
        cached = _audience_snapshot
        if (not cached or cached[0] != source_key or cached[1] != reference_date.isoformat()
                or monotonic() - cached[2] > _AUDIENCE_SNAPSHOT_TTL_SECONDS):
            return None
        return cached[3]


def _remember_audience_snapshot(reference_date: date, source: Database, snapshot: dict[str, Any]) -> None:
    global _audience_snapshot
    source_key = str(getattr(source, "database_url", None) or getattr(source, "path", ""))
    with _audience_snapshot_lock:
        _audience_snapshot = (source_key, reference_date.isoformat(), monotonic(), snapshot)


def school_class_label(label: str) -> str:
    normalized = str(label or "").strip().casefold()
    return SCHOOL_CLASS_ALIASES.get(normalized, normalized)


def _primary_subject_teacher_defaults(
    groups: list[dict[str, Any]], assignments: list[dict[str, Any]],
) -> dict[str, str]:
    """Return defaults only when a group's primary subject has one teacher."""
    teachers_by_group: dict[str, set[str]] = {}
    subjects_by_group: dict[str, str] = {}
    for group in groups:
        group_id = str(group['id'])
        primary_subject = str(group.get('subject') or '').strip().casefold()
        if primary_subject:
            subjects_by_group[group_id] = primary_subject
    for assignment in assignments:
        group_id = assignment.get('group_id')
        subject = str(assignment.get('subject') or '').strip().casefold()
        teacher_id = assignment.get('teacher_identity_id')
        if (group_id is None or teacher_id is None or
                subjects_by_group.get(str(group_id)) != subject):
            continue
        teachers_by_group.setdefault(str(group_id), set()).add(str(teacher_id))
    return {group_id: next(iter(teacher_ids))
            for group_id, teacher_ids in teachers_by_group.items()
            if len(teacher_ids) == 1}


class ReadOnlySchoolDatabase(Database):
    """Expose Database's read methods, but roll back every read-only transaction."""

    def __init__(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.hostname not in {"aws-1-eu-west-1.pooler.supabase.com", f"db.{EXPECTED_PROJECT}.supabase.co"}:
            raise ValueError("School directory host does not match the approved Supabase project")
        if not parsed.username or not parsed.username.endswith(f".{EXPECTED_PROJECT}"):
            raise ValueError("School directory credentials do not match the approved Supabase project")
        # The transaction pooler avoids an idle session timeout while reading
        # the complete canonical template. Both endpoints address this project.
        if parsed.hostname.endswith("pooler.supabase.com") and parsed.port == 5432:
            host = parsed.netloc.rsplit(":", 1)[0] + ":6543"
            url = urlunparse(parsed._replace(netloc=host))
        super().__init__(database_url=url)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=10)
        try:
            connection.execute("SET TRANSACTION READ ONLY")
            if connection.execute("SELECT current_setting('transaction_read_only') AS mode").fetchone()["mode"] != "on":
                raise RuntimeError("School source is not read only")
            yield connection
        finally:
            if not connection.closed:
                connection.rollback()
            connection.close()


def school_source() -> ReadOnlySchoolDatabase:
    # Read .env only after the local-only capability has been enabled. The
    # normal local test runner deliberately clears DATABASE_URL for its demo DB.
    url = str(dotenv_values(ROOT / ".env").get("DATABASE_URL") or "")
    if not url:
        raise RuntimeError("Approved school directory connection is not configured locally")
    return ReadOnlySchoolDatabase(url)


def school_drafts() -> Database:
    SCHOOL_DB.parent.mkdir(parents=True, exist_ok=True)
    database = Database(path=SCHOOL_DB)
    with database.connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS schedule_experiment_drafts (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, base_version_id TEXT,
            revision INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL DEFAULT '{}',
            created_by TEXT, updated_by TEXT, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""")
        # The school workspace keeps all editable schedule state locally; no
        # catalog or export-tracking writes go to the read-only school source.
        connection.execute("""CREATE TABLE IF NOT EXISTS schedule_activity_catalog (
            id TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,aliases TEXT NOT NULL DEFAULT '[]',
            description TEXT NOT NULL DEFAULT '',teacher_ids TEXT NOT NULL DEFAULT '[]',room TEXT NOT NULL DEFAULT '',
            eligible_grades TEXT NOT NULL DEFAULT '[]',revision INTEGER NOT NULL DEFAULT 1,archived INTEGER NOT NULL DEFAULT 0,
            created_by TEXT,updated_by TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(schedule_activity_catalog)")}
        for name, definition in {"teacher_ids": "TEXT NOT NULL DEFAULT '[]'", "room": "TEXT NOT NULL DEFAULT ''", "eligible_grades": "TEXT NOT NULL DEFAULT '[]'"}.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE schedule_activity_catalog ADD COLUMN {name} {definition}")
        connection.execute("""CREATE TABLE IF NOT EXISTS schedule_sheet_exports (
            export_key TEXT PRIMARY KEY,draft_id TEXT NOT NULL,revision INTEGER NOT NULL,target_id TEXT NOT NULL,
            sheet_id INTEGER NOT NULL,mapping_sheet_id INTEGER NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'prepared',
            snapshot TEXT NOT NULL,plan TEXT NOT NULL,url TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    from backend.services.schedule_activities import seed_activities
    seed_activities(database)
    return database


def school_directory(source: ReadOnlySchoolDatabase) -> dict[str, Any]:
    """Project the current canonical directory to the authenticated local UI."""
    today = date.today().isoformat()
    version = canonical_version(source)
    with source.connection() as connection:
        groups = [dict(row) for row in source.execute(connection, """SELECT id,name,display_name,group_type,subject,base_class_name,semantic_dimension
            FROM groups WHERE canonical IS TRUE ORDER BY name""").fetchall()]
        teachers = [dict(row) for row in source.execute(connection, """SELECT id,display_name FROM identities
            WHERE kind='teacher' AND status='active' ORDER BY display_name""").fetchall()]
        assignments = [dict(row) for row in source.execute(connection, """SELECT teacher_identity_id,group_id,subject
            FROM teacher_assignments WHERE active IS TRUE AND (valid_from IS NULL OR valid_from <= ?)
            AND (valid_until IS NULL OR valid_until >= ?)""", (today, today)).fetchall()]
        students = [dict(row) for row in source.execute(connection, """SELECT id,display_name,class_name FROM identities
            WHERE kind='student' AND status='active' ORDER BY display_name""").fetchall()]
        memberships = [dict(row) for row in source.execute(connection, """SELECT m.identity_id,m.group_id FROM memberships m
            JOIN identities i ON i.id=m.identity_id JOIN groups g ON g.id=m.group_id
            WHERE m.active IS TRUE AND m.member_role='student' AND i.status='active' AND g.canonical IS TRUE
            AND (m.valid_from IS NULL OR m.valid_from <= ?) AND (m.valid_until IS NULL OR m.valid_until >= ?)""", (today, today)).fetchall()]
        membership_overrides = [dict(row) for row in source.execute(connection, """SELECT identity_id,group_id,action
            FROM membership_overrides WHERE active IS TRUE AND member_role='student' AND action IN ('include','exclude')
            AND (valid_from IS NULL OR valid_from <= ?) AND (valid_until IS NULL OR valid_until >= ?)""", (today, today)).fetchall()]
        rooms = [str(row["room"]) for row in source.execute(connection, """SELECT DISTINCT a.metadata->>'room' AS room
            FROM canonical_schedule_assignments a JOIN canonical_schedule_blocks b ON b.id=a.block_id
            WHERE b.version_id=? AND COALESCE(a.metadata->>'room','') <> '' ORDER BY room""",
            (str(version["version_id"]) if version else "",)).fetchall()]
    group_by_id = {str(group["id"]): group for group in groups}
    by_teacher: dict[str, list[dict[str, str]]] = {}
    for item in assignments:
        by_teacher.setdefault(str(item["teacher_identity_id"]), []).append({
            "group_id": str(item["group_id"]), "subject": str(item.get("subject") or "")})
    excluded_pairs = {(str(row["identity_id"]), str(row["group_id"])) for row in membership_overrides if row["action"] == "exclude"}
    included_pairs = {(str(row["identity_id"]), str(row["group_id"])) for row in membership_overrides if row["action"] == "include"}
    by_student: dict[str, set[str]] = {}
    for item in memberships:
        student_id, group_id = str(item["identity_id"]), str(item["group_id"])
        if (student_id, group_id) not in excluded_pairs:
            by_student.setdefault(student_id, set()).add(group_id)
    for student_id, group_id in included_pairs - excluded_pairs:
        by_student.setdefault(student_id, set()).add(group_id)
    _remember_audience_snapshot(date.fromisoformat(today), source, {
        "students": students,
        "groups": [{"id": str(item["id"]), "name": str(item.get("display_name") or item["name"]),
                    "group_type": item["group_type"], "base_class_name": item.get("base_class_name"),
                    "semantic_dimension": item.get("semantic_dimension")} for item in groups],
        "memberships": memberships,
        "membership_overrides": membership_overrides,
    })
    return {
        "groups": [{**group, "id": str(group["id"])} for group in groups],
        "teachers": [{"id": str(item["id"]), "name": item["display_name"],
                      "assignments": by_teacher.get(str(item["id"]), [])} for item in teachers],
        "students": [{"id": str(item["id"]), "name": item["display_name"],
                      "groups": [group_id for group_id in sorted(by_student.get(str(item["id"]), set()))
                                 if group_by_id.get(group_id, {}).get("group_type") != "class"],
                      "classes": [group_id for group_id in sorted(by_student.get(str(item["id"]), set()))
                                  if group_by_id.get(group_id, {}).get("group_type") == "class"]} for item in students],
        "rooms": rooms,
    }


def school_editor_directory(source: ReadOnlySchoolDatabase) -> dict[str, Any]:
    """Read the production directory shape used by the local school editor.

    Only columns present in the established schema are selected. In particular,
    this remains usable before the additive admin-note and teacher-subject
    migrations; teacher subjects are derived from assignments instead.
    """
    today = date.today().isoformat()
    with source.connection() as connection:
        archive_column = source.execute(connection, """SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='groups' AND column_name='archived_at'""").fetchone()
        archived_select = "archived_at" if archive_column else "NULL AS archived_at"
        groups = [dict(row) for row in source.execute(connection, f"""SELECT id,name,display_name,group_type,role,semantic_dimension,subject,
                base_class_name,subject_subgroup,exam_track,canonical,{archived_select}
            FROM groups WHERE canonical IS TRUE ORDER BY name""").fetchall()]
        people = [dict(row) for row in source.execute(connection, """SELECT id,kind,display_name,class_name,status
            FROM identities WHERE status IN ('active','inactive') ORDER BY display_name""").fetchall()]
        # Active memberships are the canonical source. Exclusion overrides mask
        # them, while include overrides can supply a membership missing from the
        # source roster. Both relations use their effective date window.
        memberships = [dict(row) for row in source.execute(connection, """SELECT m.id,m.identity_id,m.group_id,
                m.source,m.source_ref,g.name AS group_name,g.display_name,g.group_type,g.role,g.semantic_dimension,g.subject,
                g.subject_subgroup,g.base_class_name,g.exam_track
            FROM memberships m JOIN identities i ON i.id=m.identity_id JOIN groups g ON g.id=m.group_id
            WHERE m.active IS TRUE AND m.member_role='student' AND i.kind='student' AND i.status='active'
              AND g.canonical IS TRUE AND (m.valid_from IS NULL OR m.valid_from <= ?)
              AND (m.valid_until IS NULL OR m.valid_until >= ?)
              AND NOT EXISTS (SELECT 1 FROM membership_overrides o WHERE o.identity_id=m.identity_id
                AND o.group_id=m.group_id AND o.member_role='student' AND o.active IS TRUE
                AND o.action='exclude' AND (o.valid_from IS NULL OR o.valid_from <= ?)
                AND (o.valid_until IS NULL OR o.valid_until >= ?))
            UNION ALL
            SELECT o.id,o.identity_id,o.group_id,'admin_override' AS source,
                'membership_override:' || o.id::text AS source_ref,g.name AS group_name,g.display_name,
                g.group_type,g.role,g.semantic_dimension,g.subject,g.subject_subgroup,g.base_class_name,g.exam_track
            FROM membership_overrides o JOIN identities i ON i.id=o.identity_id JOIN groups g ON g.id=o.group_id
            WHERE o.active IS TRUE AND o.member_role='student' AND o.action='include'
              AND i.kind='student' AND i.status='active' AND g.canonical IS TRUE
              AND (o.valid_from IS NULL OR o.valid_from <= ?) AND (o.valid_until IS NULL OR o.valid_until >= ?)
              AND NOT EXISTS (SELECT 1 FROM membership_overrides x WHERE x.identity_id=o.identity_id
                AND x.group_id=o.group_id AND x.member_role='student' AND x.active IS TRUE
                AND x.action='exclude' AND (x.valid_from IS NULL OR x.valid_from <= ?)
                AND (x.valid_until IS NULL OR x.valid_until >= ?))
              AND NOT EXISTS (SELECT 1 FROM memberships m WHERE m.identity_id=o.identity_id
                AND m.group_id=o.group_id AND m.member_role='student' AND m.active IS TRUE
                AND (m.valid_from IS NULL OR m.valid_from <= ?)
                AND (m.valid_until IS NULL OR m.valid_until >= ?))
            ORDER BY group_name""", (today,today,today,today,today,today,today,today,today,today)).fetchall()]
        # Keep an aggregate view of overlaps in the source membership rows.
        # Effective memberships below still honor overrides; this diagnostic
        # lets administrators see source collisions even when an override masks
        # them from the current roster. Student IDs never leave this service.
        source_memberships = [dict(row) for row in source.execute(connection, """SELECT DISTINCT m.identity_id,m.group_id
            FROM memberships m JOIN identities i ON i.id=m.identity_id JOIN groups g ON g.id=m.group_id
            WHERE m.active IS TRUE AND m.member_role='student' AND i.kind='student' AND i.status='active'
              AND g.canonical IS TRUE AND g.group_type <> 'class'
              AND (m.valid_from IS NULL OR m.valid_from <= ?) AND (m.valid_until IS NULL OR m.valid_until >= ?)
            ORDER BY m.group_id""", (today,today)).fetchall()]
        has_grade_assignments = bool(source.execute(connection, """SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='teacher_assignments' AND column_name='grade_level'""").fetchone())
        if has_grade_assignments:
            assignment_rows = [dict(row) for row in source.execute(connection, """SELECT a.id,a.teacher_identity_id,
                    a.group_id,a.grade_level,a.subject,a.subject_subgroup,a.exam_track,a.classroom_course_id,
                    a.active,a.valid_from,a.valid_until,a.source,a.source_ref,
                    cc.title AS course_name,g.name AS group_name,g.base_class_name,g.group_type
                FROM teacher_assignments a JOIN identities i ON i.id=a.teacher_identity_id
                LEFT JOIN groups g ON g.id=a.group_id
                LEFT JOIN classroom_courses cc ON cc.id=a.classroom_course_id
                WHERE i.kind='teacher' AND (a.group_id IS NULL OR g.canonical IS TRUE)
                ORDER BY a.created_at,a.id""").fetchall()]
        else:
            assignment_rows = [dict(row) for row in source.execute(connection, """SELECT a.id,a.teacher_identity_id,
                    a.group_id,a.subject,a.subject_subgroup,a.exam_track,a.classroom_course_id,
                    a.active,a.valid_from,a.valid_until,a.source,a.source_ref,g.name AS group_name,
                    g.base_class_name,g.group_type
                FROM teacher_assignments a JOIN identities i ON i.id=a.teacher_identity_id
                JOIN groups g ON g.id=a.group_id
                WHERE i.kind='teacher' AND g.canonical IS TRUE
                ORDER BY a.created_at,a.id""").fetchall()]
        has_teacher_subjects = bool(source.execute(connection, """SELECT 1 FROM information_schema.tables
            WHERE table_schema='public' AND table_name='teacher_subjects'""").fetchone())
        teacher_subject_rows = [dict(row) for row in source.execute(connection, """SELECT teacher_identity_id,subject
            FROM teacher_subjects WHERE active IS TRUE""").fetchall()] if has_teacher_subjects else []
        has_admin_note = bool(source.execute(connection, """SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='identities' AND column_name='admin_note'""").fetchone())
        if has_admin_note:
            notes = source.execute(connection, "SELECT id,admin_note FROM identities WHERE kind='teacher'").fetchall()
        else:
            notes = []

    group_by_id = {str(item['id']): item for item in groups}
    source_members_by_group: dict[str, set[str]] = {}
    for membership in source_memberships:
        source_members_by_group.setdefault(str(membership['group_id']), set()).add(str(membership['identity_id']))
    for group in groups:
        group_id = str(group['id'])
        group['source_overlaps'] = []
        if group.get('group_type') in {'class', 'exam_track'} or not group.get('subject'):
            continue
        own_members = source_members_by_group.get(group_id, set())
        for other in groups:
            other_id = str(other['id'])
            if other_id == group_id or other.get('group_type') in {'class', 'exam_track'}:
                continue
            if str(other.get('subject') or '').strip().casefold() != str(group.get('subject') or '').strip().casefold():
                continue
            overlap_count = len(own_members & source_members_by_group.get(other_id, set()))
            if overlap_count:
                group['source_overlaps'].append({
                    'group_id': other_id,
                    'group_name': str(other.get('display_name') or other.get('name') or 'Другая группа'),
                    'count': overlap_count,
                })
        group['source_overlaps'].sort(key=lambda item: item['group_name'].casefold())
    student_rows = [item for item in people if item['kind'] == 'student' and item['status'] == 'active']
    students_by_id = {str(item['id']): {**item, 'id': str(item['id']), 'memberships': [],
                                      'instructional_memberships': [], 'base_classes': [],
                                      'exam_profile_memberships': []} for item in student_rows}
    for membership in memberships:
        identity_id = str(membership['identity_id'])
        student = students_by_id.get(identity_id)
        if not student:
            continue
        group_id = str(membership['group_id'])
        row = {**membership, 'id': group_id, 'group_id': group_id,
               'identity_id': identity_id, 'group_name': membership['group_name'],
               'name': membership['group_name']}
        if membership['group_type'] == 'class':
            student['base_classes'].append({**group_by_id.get(group_id, {}), 'id': group_id,
                                            'name': membership['group_name']})
        elif membership['group_type'] == 'exam_track':
            student['exam_profile_memberships'].append(row)
            student['memberships'].append(row)
        else:
            student['instructional_memberships'].append(row)
            student['memberships'].append(row)

    teachers_by_id = {str(item['id']): {**item, 'id': str(item['id']), 'subjects': [],
                                       'teacher_assignments': []} for item in people if item['kind'] == 'teacher'}
    subjects_by_teacher: dict[str, set[str]] = {}
    effective_assignments: list[dict[str, Any]] = []
    for assignment in assignment_rows:
        teacher_id = str(assignment['teacher_identity_id'])
        teacher = teachers_by_id.get(teacher_id)
        if not teacher:
            continue
        group_id = str(assignment['group_id']) if assignment.get('group_id') is not None else None
        valid = (assignment['active'] is True and
                 (assignment['valid_from'] is None or str(assignment['valid_from']) <= today) and
                 (assignment['valid_until'] is None or str(assignment['valid_until']) >= today))
        row = {**assignment, 'id': str(assignment['id']), 'teacher_identity_id': teacher_id,
               'group_id': group_id, 'active': valid}
        teacher['teacher_assignments'].append(row)
        if valid and assignment['subject']:
            subjects_by_teacher.setdefault(teacher_id, set()).add(str(assignment['subject']))
            effective_assignments.append(row)
    for subject_row in teacher_subject_rows:
        subjects_by_teacher.setdefault(str(subject_row['teacher_identity_id']), set()).add(str(subject_row['subject']))
    note_by_teacher = {str(row['id']): row['admin_note'] for row in notes}
    for teacher_id, teacher in teachers_by_id.items():
        teacher['subjects'] = sorted(subjects_by_teacher.get(teacher_id, set()))
        teacher['comment'] = note_by_teacher.get(teacher_id)

    defaults = _primary_subject_teacher_defaults(groups, effective_assignments)
    for group in groups:
        group['id'] = str(group['id'])
        group['default_teacher_identity_id'] = defaults.get(group['id'])
    return {'groups': groups, 'students': list(students_by_id.values()),
            'teachers': list(teachers_by_id.values())}


def school_teacher_schedule(source: ReadOnlySchoolDatabase, teacher_id: str, week_start: str | None = None) -> dict[str, Any]:
    """Read a canonical template or an already materialized dated week."""
    version = canonical_version(source)
    if not version:
        return {'status': 'canonical_not_imported', 'available_weeks': [], 'items': []}
    with source.connection() as connection:
        weeks = [str(row['week_start']) for row in source.execute(connection, """SELECT DISTINCT ew.week_start
            FROM canonical_effective_weeks ew
            JOIN canonical_schedule_versions cv ON cv.version_id=ew.version_id
            WHERE cv.status <> 'superseded' ORDER BY ew.week_start DESC""").fetchall()]
        groups = {str(row['id']): str(row['display_name'] or row['name']) for row in source.execute(connection,
            "SELECT id,name,display_name FROM groups WHERE canonical IS TRUE").fetchall()}
    if week_start:
        if week_start not in weeks:
            return {'status': 'week_not_available', 'available_weeks': weeks, 'week_start': week_start, 'items': []}
        projection = project_teacher(source, teacher_id, week_start)
        # Projected canonical assignments may appear once per class. Fold the
        # same lesson back together before exposing it to the teacher card.
        folded: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for item in projection.get('items') or []:
            key = (str(item.get('date')), str(item.get('start_time')), str(item.get('end_time')), str(item.get('activity') or ''))
            current = folded.setdefault(key, {**item, 'audience_group_ids': [], 'audiences': []})
            current['audience_group_ids'] = sorted(set(current['audience_group_ids']) | {str(value) for value in item.get('audience_group_ids') or []})
            current['audiences'] = [groups.get(value, value) for value in current['audience_group_ids']]
        return {'status': projection.get('status'), 'source': 'published_week', 'week_start': week_start,
                'available_weeks': weeks, 'items': list(folded.values()), 'issues': projection.get('issues') or []}

    template = read_canonical_template(source, str(version['version_id'])) or {}
    folded_template: dict[tuple[int, str, str, str], dict[str, Any]] = {}
    for block_key, block in (template.get('blocks') or {}).items():
        for index, assignment in enumerate(block.get('assignments') or []):
            teacher_ids = [str(value) for value in assignment.get('teacher_ids') or []]
            if str(teacher_id) not in teacher_ids:
                continue
            start, end = str(block.get('start_time') or ''), str(block.get('end_time') or '')
            subject = str(assignment.get('activity') or '')
            weekday = int(block.get('weekday')) if block.get('weekday') is not None else -1
            key = (weekday, start, end, subject)
            current = folded_template.setdefault(key, {'weekday': weekday, 'start_time': start, 'end_time': end,
                'activity': subject, 'teacher_ids': teacher_ids, 'audience_group_ids': [], 'audiences': [],
                'source_keys': []})
            current['audience_group_ids'] = sorted(set(current['audience_group_ids']) |
                {str(value) for value in assignment.get('canonical_group_ids') or []})
            current['audiences'] = [groups.get(value, value) for value in current['audience_group_ids']]
            current['source_keys'].append(f'{block_key}:{index}')
    return {'status': version.get('status'), 'source': 'canonical_template', 'version_id': str(version['version_id']),
            'available_weeks': weeks, 'items': sorted(folded_template.values(), key=lambda item:
                (item['weekday'], item['start_time'], item['end_time'], item['activity']))}


def _safe_lessons(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep source references, but never persist pupil identities or rosters."""
    for lesson in payload["lessons"]:
        canonical = lesson.get("canonical") or {}
        assignment = canonical.get("assignment") or {}
        block = canonical.get("block") or {}
        metadata = assignment.get("metadata") or {}
        rule = metadata.get("audience_rule") or {}
        filtered_rule = {key: rule[key] for key in
                         ("kind", "class_group_id", "grade", "partition_group_ids", "partition_dimension") if key in rule}
        if any(rule.get(key) for key in ("include_student_ids", "exclude_student_ids", "student_ids")):
            lesson["needsClarification"] = True
            lesson["clarificationReason"] = "Индивидуальный состав задан в canonical; проверьте его в защищённом справочнике."
        lesson["canonical"] = {
            "block_key": canonical.get("block_key"),
            "assignment_index": canonical.get("assignment_index"),
            "assignment": {"metadata": {"audience_rule": filtered_rule},
                           "source_cells": assignment.get("source_cells") or []},
            "block": {"block_key": block.get("block_key")},
        }
    return payload


def _collapse_repeated_group_lessons(payload: dict[str, Any]) -> int:
    """One cross-class instructional group can appear in two visual columns."""
    class_ids = set(payload["classes"])
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    retained = []
    collapsed = 0
    for lesson in payload["lessons"]:
        instructional = tuple(sorted(set(lesson.get("groupIds") or []) - class_ids))
        teachers = tuple(sorted(lesson.get("teacherIds") or []))
        key = (lesson.get("day"), lesson.get("start"), lesson.get("end"), instructional, teachers)
        if not instructional or not teachers or key not in seen:
            retained.append(lesson)
            if instructional and teachers:
                seen[key] = lesson
            continue
        prior = seen[key]
        prior["classIds"] = sorted(set(prior.get("classIds") or []) | set(lesson.get("classIds") or []))
        prior["classId"] = prior["classIds"][0] if len(prior["classIds"]) == 1 else ""
        prior.setdefault("source_duplicates", []).append({
            "block_key": lesson.get("canonical", {}).get("block_key"),
            "assignment_index": lesson.get("canonical", {}).get("assignment_index"),
            "source_cells": lesson.get("canonical", {}).get("assignment", {}).get("source_cells") or [],
        })
        prior["needsClarification"] = bool(prior.get("needsClarification") or lesson.get("needsClarification"))
        collapsed += 1
    payload["lessons"] = retained
    return collapsed


def _separate_legacy_no_lesson(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain generated absence claims for review, outside the lesson grid.

    The old canonical artifact uses NO_LESSON for final routing states as well
    as source claims. Only a direct, current source mark can become an explicit
    no-lesson mark in the constructor.
    """
    retained = []
    review = []
    for lesson in payload["lessons"]:
        if str(lesson.get("subject") or "").strip().upper() != "NO_LESSON":
            retained.append(lesson)
            continue
        canonical = lesson.get("canonical") or {}
        review.append({
            "canonical_block_key": canonical.get("block_key"),
            "assignment_index": canonical.get("assignment_index"),
            "source_cells": (canonical.get("assignment") or {}).get("source_cells") or [],
            "day": lesson.get("day"), "start": lesson.get("start"), "end": lesson.get("end"),
            "class_ids": lesson.get("classIds") or [],
            "reason": "NO_LESSON в прежнем canonical может быть вычисленным остатком; явной отметки в текущем листе не подтверждено.",
        })
    payload["lessons"] = retained
    return review


def import_school_template(source: ReadOnlySchoolDatabase, drafts: Database) -> dict[str, Any]:
    version = canonical_version(source)
    if not version:
        raise ValueError("No approved canonical template was found")
    version_id = str(version["version_id"])
    for item in list_experiments(drafts):
        if item["draft_kind"] != "school":
            continue
        existing = get_experiment(drafts, item["id"])
        provenance = existing["payload"].get("school_source") or {}
        if provenance.get("canonical_version_id") == version_id:
            existing["already_exists"] = True
            existing["transfer_result"] = existing["payload"].get("transfer_result") or {}
            return existing
    payload = _safe_lessons(_canonical_payload(source, version_id))
    legacy_absences = _separate_legacy_no_lesson(payload)
    collapsed = _collapse_repeated_group_lessons(payload)
    manifest_path = ROOT / ".local-test" / "private" / "live-template-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if manifest and (manifest.get("spreadsheet_id") != EXPECTED_SPREADSHEET or
                     str(manifest.get("sheet_id")) != EXPECTED_SHEET):
        raise ValueError("Observed template source does not match the approved spreadsheet tab")
    snapshot_path = ROOT / ".local-test" / "private" / "confirmed-template-snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.exists() else {}
    snapshot_structure = snapshot.get("structural_payload") or {}
    if snapshot_structure and snapshot_structure.get("confirmed_version_id") != version_id:
        raise ValueError("Local confirmed source snapshot refers to a different canonical version")
    source_mapping = snapshot_structure.get("source_to_canonical") or {}
    with source.connection() as connection:
        class_rows = source.execute(connection, """SELECT id,name,display_name FROM groups
            WHERE canonical IS TRUE AND group_type='class'""").fetchall()
    class_ids: dict[str, str] = {}
    for row in class_rows:
        for label in (str(row["name"] or ""), str(row["display_name"] or "")):
            visible = label.split("·", 1)[0].strip().casefold().removesuffix(" класс")
            class_ids[visible] = str(row["id"])
    slot_indexes = {(slot["start"], slot["end"]): index for index, slot in enumerate(payload["slots"])}
    imported_marks = 0
    unlocated_marks = []
    for marker in manifest.get("explicit_markers") or []:
        if not isinstance(marker, dict) or marker.get("kind") not in {"lunch", "no_lesson"}:
            continue
        class_id = class_ids.get(school_class_label(str(marker.get("class_label") or "")))
        day = marker.get("day")
        slot_key = (marker.get("start"), marker.get("end"))
        if not class_id or not isinstance(day, int) or day not in payload["days"] or slot_key not in slot_indexes:
            unlocated_marks.append({"source_cell": marker.get("coordinate"), "reason": "Класс или звонок не сопоставлен"})
            continue
        kind = marker["kind"]
        payload["lessons"].append({
            "id": f"sheet-mark:{marker['coordinate']}", "day": day, "period": slot_indexes[slot_key],
            "classId": class_id, "classIds": [class_id], "groupId": "", "groupIds": [],
            "subject": "Обед" if kind == "lunch" else "Нет урока",
            "specialMark": kind, "canonicalRole": "lunch" if kind == "lunch" else "explicit_no_lesson",
            "teacherId": "", "teacherIds": [], "room": "", "start": slot_key[0], "end": slot_key[1],
            "needsClarification": True,
            "clarificationReason": "Уточните, относится отметка ко всему классу или к отдельной учебной группе.",
            "source": {"sheet_id": EXPECTED_SHEET, "sheet_title": "2026/27 шаблон",
                       "cell": marker["coordinate"]},
        })
        imported_marks += 1
    payload["draft_kind"] = "school"
    payload["school_source"] = {
        "kind": "confirmed_canonical_template", "canonical_version_id": version_id,
        "canonical_fingerprint": str(version.get("source_fingerprint") or ""),
        "spreadsheet_id": EXPECTED_SPREADSHEET, "sheet_id": EXPECTED_SHEET,
        "sheet_title": "2026/27 шаблон",
    }
    payload["import_review"] = {
        "status": "requires_source_reconciliation",
        "message": "Изменения Google-таблицы после подтверждения canonical требуют отдельной сверки.",
        "observed_at": manifest.get("observed_at"),
        "changed_cell_count": manifest.get("changed_cell_count"),
        "changed_cells": manifest.get("changed_cells") or [],
        "unlocated_special_marks": unlocated_marks,
        "source_bell_slots": manifest.get("bell_slots") or [],
        "canonical_bell_slots": [f"{slot['start']}-{slot['end']}" for slot in payload["slots"]],
        "bell_discrepancies": sorted(set(manifest.get("bell_slots") or []) ^
                                     {f"{slot['start']}-{slot['end']}" for slot in payload["slots"]}),
        "source_snapshot_block_count": len(snapshot_structure.get("rows") or {}),
        "source_snapshot_unmapped_blocks": sum(not value for value in source_mapping.values()),
        "collapsed_cross_class_duplicates": collapsed,
        "legacy_no_lesson_claims": legacy_absences,
        "items": [],
    }
    original = payload["transfer_result"]
    payload["transfer_result"] = {
        "canonical_assignments": original["transferred"] + original["needs_clarification"] + original["import_errors"],
        "collapsed_cross_class_duplicates": collapsed,
        "legacy_no_lesson_claims": len(legacy_absences),
        "transferred": max(0, original["transferred"] - collapsed - len(legacy_absences)),
        "needs_clarification": original["needs_clarification"] + imported_marks,
        "import_errors": original["import_errors"] + len(unlocated_marks),
        "explicit_lunch": sum(lesson.get("specialMark") == "lunch" for lesson in payload["lessons"]),
        "explicit_no_lesson": sum(lesson.get("specialMark") == "no_lesson" for lesson in payload["lessons"]),
    }
    return create_experiment(drafts, name=SCHOOL_DRAFT_NAME, source="import", payload=payload)
