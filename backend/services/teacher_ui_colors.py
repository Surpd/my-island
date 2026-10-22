"""Per-school teacher colors for schedule UI preferences.

This registry is deliberately separate from lesson, schedule-source, and identity
mapping data. A school is the database/workspace key supplied by a trusted caller.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from backend.database import Database

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
PALETTE_SIZE = 40

# Evidence-backed colors only. This is a provenance manifest, not an operational
# source mapping. The source snapshots are documented in the consolidation artifact.
APPROVED_SOURCE_COLORS: dict[str, dict[str, str]] = {
    "4aaa8a20-8bb1-49c4-b6d7-e7b2e3ae3b63": {"color": "#FF6D01", "evidence": "observed_legend"},
    "cbd8950f-5aac-4432-83a0-cff7955de049": {"color": "#4285F4", "evidence": "observed_legend"},
    "7bd9f8b3-de7a-471b-817e-21b678130380": {"color": "#FF0000", "evidence": "observed_legend"},
    "288af97a-8353-4f83-b5cd-4e4cf515139f": {"color": "#CCCCCC", "evidence": "observed_legend"},
    "d60bc7f2-6dfb-49af-aef9-413059c1c710": {"color": "#00FF00", "evidence": "observed_legend"},
    "353477ff-84c2-40b6-90c0-a4781ada1691": {"color": "#274E13", "evidence": "observed_legend"},
    "8b2e4013-171a-45d3-a63b-238d8ac89b5a": {"color": "#CC0000", "evidence": "repeated_explicit_text"},
    "761ec545-ca80-4ba3-84f3-cccd74c1b5c6": {"color": "#E06666", "evidence": "repeated_explicit_text"},
    "8c6b3aa0-b555-4edf-8a00-0501147ecdbf": {"color": "#FF00FF", "evidence": "repeated_explicit_text"},
    "b89332ce-c5ee-41d3-80b2-fbfc8bd81da0": {"color": "#A64D79", "evidence": "observed_legend_activity_filtered"},
    "d7894f0a-728b-40c3-a424-667e1c29752f": {"color": "#A4C2F4", "evidence": "observed_legend_activity_filtered"},
    "468c238b-4071-488b-88f6-91fc769e137c": {"color": "#F4CCCC", "evidence": "observed_legend_activity_filtered"},
    "358f479c-5cf7-483f-a094-47b83320c373": {"color": "#34A853", "evidence": "observed_legend_activity_filtered"},
    "8876cefa-5451-40e6-8541-f76711dc923a": {"color": "#674EA7", "evidence": "observed_legend_activity_filtered"},
}


class TeacherColorError(ValueError):
    pass


class TeacherColorConflict(TeacherColorError):
    pass


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _hex(value: Any) -> str:
    color = str(value or "")
    if not HEX.fullmatch(color):
        raise TeacherColorError("color must be a six-digit HEX value")
    return color


def _fallback(teacher_id: str) -> str:
    """Stable pure-ID fallback shared with the browser registry palette."""
    index = 0
    for character in teacher_id:
        index = (index * 31 + ord(character)) & 0xFFFFFFFF
    return _palette_color(index % PALETTE_SIZE)


def _palette_color(index: int) -> str:
    if not 0 <= index < PALETTE_SIZE:
        raise TeacherColorError("assignment index is outside the supported palette")
    # Keep the local browser palette semantics for settings migration.
    hue = (index * 137.508) % 360
    saturation, lightness = (49 if index % 2 == 0 else 57), (90 if index % 3 == 0 else 87 if index % 3 == 1 else 92)
    s, l = saturation / 100, lightness / 100
    chroma = (1 - abs(2 * l - 1)) * s
    section = hue / 60
    x = chroma * (1 - abs(section % 2 - 1))
    rgb = (chroma, x, 0) if section < 1 else (x, chroma, 0) if section < 2 else (0, chroma, x) if section < 3 else (0, x, chroma) if section < 4 else (x, 0, chroma) if section < 5 else (chroma, 0, x)
    m = l - chroma / 2
    return "#" + "".join(f"{int((part + m) * 255 + 0.5):02x}" for part in rgb)


def initialize_sqlite_schema(database: Database) -> None:
    """Create the isolated local table for an explicit local setup/test operation."""
    if database.database_url:
        return
    with database.connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS teacher_ui_colors (
            school_id TEXT NOT NULL,
            teacher_identity_id TEXT NOT NULL,
            base_color TEXT NOT NULL CHECK (length(base_color) = 7),
            manual_color TEXT CHECK (manual_color IS NULL OR length(manual_color) = 7),
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
            base_source TEXT NOT NULL DEFAULT 'fallback',
            source_evidence TEXT,
            updated_by TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (school_id, teacher_identity_id)
        )""")


def _require_teacher(database: Database, teacher_id: Any) -> str:
    identity = str(teacher_id or "").strip()
    if not identity:
        raise TeacherColorError("teacher_id is required")
    with database.connection() as connection:
        row = database.execute(connection,
            "SELECT id FROM identities WHERE id = ? AND kind = 'teacher' AND status = 'active'", (identity,)).fetchone()
    if not row:
        raise TeacherColorError("unknown or inactive teacher identity")
    return identity


def _public(row: Any, *, missing_id: str | None = None) -> dict[str, Any]:
    if row is None or not _row_dict(row).get("base_color"):
        identity = str(missing_id or (row["teacher_identity_id"] if row is not None else ""))
        return {"teacher_id": identity, "color": _fallback(identity), "base_color": _fallback(identity),
                "manual_color": None, "revision": 0, "source": "fallback"}
    value = _row_dict(row)
    return {"teacher_id": str(value["teacher_identity_id"]),
            "color": value["manual_color"] or value["base_color"],
            "base_color": value["base_color"], "manual_color": value["manual_color"],
            "revision": int(value["revision"]), "source": value["base_source"],
            "source_evidence": value["source_evidence"]}


def list_colors(database: Database, school_id: str) -> list[dict[str, Any]]:
    with database.connection() as connection:
        rows = database.execute(connection, """SELECT i.id AS teacher_identity_id, c.base_color,
            c.manual_color, c.revision, c.base_source, c.source_evidence
            FROM identities i LEFT JOIN teacher_ui_colors c
              ON c.teacher_identity_id=i.id AND c.school_id=?
            WHERE i.kind='teacher' AND i.status='active' ORDER BY i.display_name,i.id""", (school_id,)).fetchall()
    return [_public(row, missing_id=str(row["teacher_identity_id"])) for row in rows]


def get_color(database: Database, school_id: str, teacher_id: Any) -> dict[str, Any]:
    identity = _require_teacher(database, teacher_id)
    with database.connection() as connection:
        row = database.execute(connection, "SELECT * FROM teacher_ui_colors WHERE school_id = ? AND teacher_identity_id = ?", (school_id, identity)).fetchone()
    return _public(row, missing_id=identity)


def initialize_missing(database: Database, school_id: str, actor_id: Any = None) -> dict[str, Any]:
    """Persist deterministic fallbacks for active teachers; never changes existing rows."""
    initialize_sqlite_schema(database)
    with database.connection() as connection:
        teachers = database.execute(connection, "SELECT id FROM identities WHERE kind='teacher' AND status='active' ORDER BY id").fetchall()
        created = 0
        for row in teachers:
            identity = str(row["id"])
            if database.database_url:
                result = database.execute(connection, """INSERT INTO teacher_ui_colors
                    (school_id,teacher_identity_id,base_color,base_source,updated_by)
                    VALUES (?,?,?,?,?) ON CONFLICT (school_id,teacher_identity_id) DO NOTHING""",
                    (school_id, identity, _fallback(identity), "fallback", str(actor_id) if actor_id is not None else None))
                created += max(0, getattr(result, "rowcount", 0))
            else:
                result = connection.execute("""INSERT OR IGNORE INTO teacher_ui_colors
                    (school_id,teacher_identity_id,base_color,base_source,updated_by)
                    VALUES (?,?,?,?,?)""", (school_id, identity, _fallback(identity), "fallback", str(actor_id) if actor_id is not None else None))
                created += max(0, result.rowcount)
    return {"created": created, "total": len(teachers)}


def seed_approved(database: Database, school_id: str, actor_id: Any = None) -> dict[str, Any]:
    """Seed missing records and replace only generated fallback bases, preserving overrides."""
    initialize_sqlite_schema(database)
    # Validate the whole manifest before opening the write transaction.
    missing = []
    for identity in APPROVED_SOURCE_COLORS:
        try:
            _require_teacher(database, identity)
        except TeacherColorError:
            missing.append(identity)
    if missing:
        raise TeacherColorError("approved manifest has unknown/inactive teacher IDs: " + ", ".join(missing))
    inserted = updated = 0
    with database.connection() as connection:
        for identity, evidence in APPROVED_SOURCE_COLORS.items():
            color = evidence["color"]
            current = database.execute(connection, "SELECT base_source FROM teacher_ui_colors WHERE school_id=? AND teacher_identity_id=?", (school_id, identity)).fetchone()
            if database.database_url:
                result = database.execute(connection, """INSERT INTO teacher_ui_colors
                    (school_id,teacher_identity_id,base_color,base_source,source_evidence,updated_by)
                    VALUES (?,?,?,?,?,?) ON CONFLICT (school_id,teacher_identity_id) DO UPDATE SET
                        base_color=excluded.base_color, base_source='approved_source',
                        source_evidence=excluded.source_evidence, revision=teacher_ui_colors.revision+1,
                        updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP
                    WHERE teacher_ui_colors.base_source='fallback'""",
                    (school_id, identity, color, "approved_source", evidence["evidence"], str(actor_id) if actor_id is not None else None))
            else:
                result = connection.execute("""INSERT INTO teacher_ui_colors
                    (school_id,teacher_identity_id,base_color,base_source,source_evidence,updated_by)
                    VALUES (?,?,?,?,?,?) ON CONFLICT (school_id,teacher_identity_id) DO UPDATE SET
                        base_color=excluded.base_color, base_source='approved_source',
                        source_evidence=excluded.source_evidence, revision=teacher_ui_colors.revision+1,
                        updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP
                    WHERE teacher_ui_colors.base_source='fallback'""",
                    (school_id, identity, color, "approved_source", evidence["evidence"], str(actor_id) if actor_id is not None else None))
            applied = max(0, getattr(result, "rowcount", 0))
            if current is None and applied:
                inserted += 1
            elif current is not None and applied:
                updated += 1
    return {"inserted": inserted, "updated": updated,
            "preserved": len(APPROVED_SOURCE_COLORS) - inserted - updated}


def set_manual_color(database: Database, school_id: str, teacher_id: Any, color: Any,
                     expected_revision: int, actor_id: Any = None) -> dict[str, Any]:
    identity, value = _require_teacher(database, teacher_id), _hex(color)
    initialize_sqlite_schema(database)
    with database.connection() as connection:
        existing = database.execute(connection, "SELECT revision FROM teacher_ui_colors WHERE school_id=? AND teacher_identity_id=?", (school_id, identity)).fetchone()
        if existing is None and expected_revision != 0:
            raise TeacherColorConflict("teacher color revision is stale")
        row = database.execute(connection, """INSERT INTO teacher_ui_colors
            (school_id,teacher_identity_id,base_color,manual_color,revision,base_source,updated_by)
            VALUES (?,?,?,?,1,'fallback',?)
            ON CONFLICT (school_id,teacher_identity_id) DO UPDATE SET
                manual_color=excluded.manual_color, revision=teacher_ui_colors.revision+1,
                updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP
            WHERE teacher_ui_colors.revision=?
            RETURNING *""", (school_id, identity, _fallback(identity), value,
                              str(actor_id) if actor_id is not None else None, expected_revision)).fetchone()
        if row is None:
            raise TeacherColorConflict("teacher color revision is stale")
    return _public(row)


def reset_manual_color(database: Database, school_id: str, teacher_id: Any,
                       expected_revision: int, actor_id: Any = None) -> dict[str, Any]:
    identity = _require_teacher(database, teacher_id)
    initialize_sqlite_schema(database)
    with database.connection() as connection:
        existing = database.execute(connection, "SELECT revision FROM teacher_ui_colors WHERE school_id=? AND teacher_identity_id=?", (school_id, identity)).fetchone()
        if existing is None and expected_revision != 0:
            raise TeacherColorConflict("teacher color revision is stale")
        row = database.execute(connection, """INSERT INTO teacher_ui_colors
            (school_id,teacher_identity_id,base_color,manual_color,revision,base_source,updated_by)
            VALUES (?,?,?,NULL,1,'fallback',?)
            ON CONFLICT (school_id,teacher_identity_id) DO UPDATE SET
                manual_color=NULL, revision=teacher_ui_colors.revision+1,
                updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP
            WHERE teacher_ui_colors.revision=?
            RETURNING *""", (school_id, identity, _fallback(identity),
                              str(actor_id) if actor_id is not None else None, expected_revision)).fetchone()
        if row is None:
            raise TeacherColorConflict("teacher color revision is stale")
    return _public(row)


def _legacy_plan(database: Database, entries: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    plan, issues = [], []
    for raw_id, raw_value in entries.items():
        identity = str(raw_id).strip()
        try:
            _require_teacher(database, identity)
            color = _hex(raw_value)
        except TeacherColorError as error:
            issues.append({"teacher_id": identity, "error": str(error)})
            continue
        plan.append({"teacher_id": identity, "color": color})
    return plan, issues


def preview_settings_migration(database: Database, school_id: str, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Preview local registry overrides and assignment indices; writes nothing."""
    assignments = settings.get("assignments") if isinstance(settings.get("assignments"), dict) else {}
    overrides = settings.get("overrides") if isinstance(settings.get("overrides"), dict) else {}
    colors: dict[str, Any] = {}
    issues: list[dict[str, str]] = []
    for teacher_id, index in assignments.items():
        try:
            colors[str(teacher_id)] = _palette_color(int(index))
        except (ValueError, TypeError, TeacherColorError) as error:
            issues.append({"teacher_id": str(teacher_id), "error": str(error)})
    colors.update(overrides)
    plan, identity_issues = _legacy_plan(database, colors)
    issues.extend(identity_issues)
    current = {item["teacher_id"]: item for item in list_colors(database, school_id)}
    for item in plan:
        existing = current.get(item["teacher_id"], {})
        item["expected_revision"] = int(existing.get("revision", 0))
        item["current_color"] = existing.get("color")
        if not existing or not item["expected_revision"]:
            item["status"] = "will_add"
        elif existing.get("manual_color"):
            item["status"] = "replace_manual"
        else:
            item["status"] = "replace_shared"
    return {"ready": not issues, "plan": plan, "issues": issues}


def confirm_settings_migration(database: Database, school_id: str, settings: Mapping[str, Any],
                               expected_revisions: Mapping[str, int], actor_id: Any = None) -> dict[str, Any]:
    preview = preview_settings_migration(database, school_id, settings)
    if preview["issues"]:
        raise TeacherColorError("settings import contains unknown IDs or invalid colors")
    plan_ids = {item["teacher_id"] for item in preview["plan"]}
    if set(expected_revisions) != plan_ids or any(
        int(expected_revisions[identity]) != item["expected_revision"]
        for item in preview["plan"] for identity in [item["teacher_id"]]
    ):
        raise TeacherColorConflict("settings preview is stale; preview the import again")
    initialize_sqlite_schema(database)
    changed = []
    with database.connection() as connection:
        for item in preview["plan"]:
            identity, expected = item["teacher_id"], item["expected_revision"]
            teacher = database.execute(connection, "SELECT id FROM identities WHERE id=? AND kind='teacher' AND status='active'", (identity,)).fetchone()
            if not teacher:
                raise TeacherColorError(f"unknown or inactive teacher identity: {identity}")
            row = database.execute(connection, """INSERT INTO teacher_ui_colors
                (school_id,teacher_identity_id,base_color,manual_color,revision,base_source,updated_by)
                VALUES (?,?,?,?,1,'fallback',?)
                ON CONFLICT (school_id,teacher_identity_id) DO UPDATE SET
                    manual_color=excluded.manual_color, revision=teacher_ui_colors.revision+1,
                    updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP
                WHERE teacher_ui_colors.revision=?
                RETURNING *""", (school_id, identity, _fallback(identity), item["color"],
                                  str(actor_id) if actor_id is not None else None, expected)).fetchone()
            if row is None:
                raise TeacherColorConflict(f"teacher color revision is stale for {identity}")
            changed.append(_public(row))
    return {"imported": len(changed), "records": changed}
