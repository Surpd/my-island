"""Persistence and read-side projections for the canonical schedule.

Canonical assignments store audience semantics and provenance only. Student sets
are derived at read time from the canonical directory memberships, so a
canonical version remains immutable while projections stay date-aware.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from backend.database import Database
from backend.services.schedule_subjects import program_key, subject_label
from backend.services.schedule_rooms import display_room


PUBLISHED_STATUSES = {"authoritative", "approved_baseline", "approved_with_exceptions"}
CANONICAL_STATUSES = {"draft", "approved_baseline", "approved_with_exceptions", "authoritative", "superseded"}
_NO_LESSON = "NO_LESSON"
_OPTIONAL_TEACHER_ACTIVITIES = {"Творчество", "Тренинг", "Курс по выбору", "Цифровой трек", "SDEP"}
_RESIDUAL_ROLES = {"default", "residual", "final_state", "final_unassigned", "explicit_no_lesson"}


@dataclass
class PublishedWeek:
    """A pinned runtime row, its checked source, and school-safe edition metadata."""

    metadata: dict[str, Any]
    status: str
    effective: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    source_publication_id: str | None = None


@dataclass
class StudentProjectionContext:
    """Fresh directory snapshot shared by every student projection in one request."""

    effective: dict[str, Any]
    students: dict[str, dict[str, Any]]
    groups: dict[str, dict[str, Any]]
    memberships: list[dict[str, Any]]
    membership_overrides: list[dict[str, Any]]
    blocks: list[dict[str, Any]]
    membership_cache: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    membership_rows_by_student: dict[str, list[dict[str, Any]]] = field(init=False)
    override_rows_by_student: dict[str, list[dict[str, Any]]] = field(init=False)
    blocks_by_weekday: dict[Any, list[dict[str, Any]]] = field(init=False)
    overlap_blocks_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = field(default_factory=dict)
    slot_assignments_cache: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    student_grade_cache: dict[str, str] = field(default_factory=dict)
    class_context_cache: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    group_dimension_cache: dict[str, tuple[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.membership_rows_by_student = defaultdict(list)
        for row in self.memberships:
            self.membership_rows_by_student[str(row["identity_id"])].append(row)
        self.override_rows_by_student = defaultdict(list)
        for row in self.membership_overrides:
            self.override_rows_by_student[str(row["identity_id"])].append(row)
        self.blocks_by_weekday = defaultdict(list)
        for block in self.blocks:
            if block.get("change_kind") != "cancelled":
                self.blocks_by_weekday[block.get("weekday")].append(block)


def _json(database: Database, value: Any) -> Any:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if database.database_url:
        from psycopg.types.json import Jsonb
        return Jsonb(value, dumps=lambda payload: json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    return serialized


def _decode(database: Database, value: Any, fallback: Any) -> Any:
    decoded = database._decode_json_value(value)
    return fallback if decoded is None or decoded == "" else decoded


def _assignment_metadata(value: Any) -> dict[str, Any]:
    metadata = dict(value) if isinstance(value, Mapping) else {}
    nested = metadata.pop("metadata", None)
    if isinstance(nested, Mapping):
        metadata.update(nested)
    return metadata


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _assignment_record(database: Database, assignment: Mapping[str, Any]) -> tuple[Any, ...]:
    audience = assignment.get("audience") if isinstance(assignment.get("audience"), Mapping) else {}
    group_ids = [str(value) for value in audience.get("canonical_group_ids") or assignment.get("canonical_group_ids") or []]
    if not group_ids and str(audience.get("type") or "") == "source_only_audience":
        label = str(audience.get("label") or assignment.get("source_audience") or "").strip()
        if label:
            rows = _single_fetchall(
                database,
                "SELECT id FROM groups WHERE canonical IS TRUE AND role = 'base_class' AND base_class_name = ? ORDER BY id",
                (label,),
            )
            if len(rows) == 1:
                group_ids = [str(rows[0]["id"])]
                audience = {"type": "canonical_groups", "canonical_group_ids": group_ids, "source_label": label}
    role = str(assignment.get("role") or assignment.get("kind") or "primary")
    if not group_ids and role in {"default", "residual", "final_state", "final_unassigned"}:
        audience_kind = "complement" if role not in {"final_state", "final_unassigned"} else "remaining"
    elif role == "explicit_no_lesson":
        audience_kind = "canonical_groups" if group_ids else "remaining"
    else:
        audience_kind = str(audience.get("type") or "canonical_groups")
    metadata = {key: value for key, value in assignment.items() if key not in {"student_ids", "student_count", "activity", "role", "kind", "audience", "metadata"}}
    if isinstance(assignment.get("metadata"), Mapping):
        metadata.update(assignment["metadata"])
    metadata["audience"] = {"type": audience_kind, "canonical_group_ids": group_ids}
    teacher_ids = [str(value) for value in assignment.get("teacher_ids") or []]
    return (
        str(assignment.get("activity") or ""), role, audience_kind,
        _json(database, group_ids), _json(database, assignment.get("source_cells") or []),
        _json(database, teacher_ids), _json(database, metadata),
    )


def _single_fetchone(database: Database, query: str, params: tuple[Any, ...] = ()) -> Any:
    with database.connection() as connection:
        return database.execute(connection, query, params).fetchone()


def _single_fetchall(database: Database, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
    with database.connection() as connection:
        return database.execute(connection, query, params).fetchall()


def _single_execute(database: Database, query: str, params: tuple[Any, ...] = ()) -> None:
    with database.connection() as connection:
        database.execute(connection, query, params)


def _artifact_blocks(artifact: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Normalize the persisted artifact's mapping and candidate-list forms."""
    raw = artifact.get("blocks")
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items() if isinstance(value, Mapping)}
    if isinstance(raw, list):
        result: dict[str, Mapping[str, Any]] = {}
        for block in raw:
            if not isinstance(block, Mapping):
                continue
            key = str(block.get("block_key") or "").strip()
            if not key:
                raise ValueError("Canonical block in list form must contain block_key")
            if key in result:
                raise ValueError(f"Duplicate canonical block key: {key}")
            result[key] = block
        return result
    return {}


def _normalized_block(block: Mapping[str, Any], artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt the Grade 7 candidate shape without changing its semantics."""
    normalized = dict(block)
    if not normalized.get("mode") and normalized.get("row_routing_dimension"):
        normalized["mode"] = normalized["row_routing_dimension"]
    weekday = normalized.get("weekday")
    if isinstance(weekday, str):
        weekday_map = {"Пн": 0, "Вт": 1, "Ср": 2, "Чт": 3, "Пт": 4,
                       "Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4}
        if weekday.strip() in weekday_map:
            normalized["weekday"] = weekday_map[weekday.strip()]
        elif weekday.strip().isdigit():
            normalized["weekday"] = int(weekday.strip())
    slot = normalized.get("slot")
    if isinstance(slot, Mapping):
        slot = dict(slot)
        start = str(slot.get("start") or "").strip()
        time_range = re.match(r"^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$", start)
        if time_range:
            slot["start"], slot["end"] = time_range.groups()
        for key in ("start", "end"):
            value = str(slot.get(key) or "").strip()
            match = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
            if match:
                slot[key] = f"{int(match.group(1)):02d}:{match.group(2)}"
        normalized["slot"] = slot
    if not isinstance(normalized.get("derived_from"), Mapping):
        source = artifact.get("source_snapshot") if isinstance(artifact.get("source_snapshot"), Mapping) else {}
        normalized["derived_from"] = {
            "sheet": source.get("sheet_name"),
            "source_cells": normalized.get("source_cells") or [],
            "structural_fingerprint": _hash(normalized.get("source_cells") or []),
        }
    return normalized


def _import_canonical_artifact_reconnect(database: Database, artifact: Mapping[str, Any], *, status: str | None,
                                         approved_at: str | None, version_id: str, source_snapshot_id: str,
                                         source_fingerprint: str, selected_status: str,
                                         blocks: Mapping[str, Any]) -> dict[str, Any]:
    """Pooler-safe importer: each operation uses a fresh short-lived connection."""
    existing = _single_fetchone(database, "SELECT * FROM canonical_schedule_versions WHERE version_id = ?", (version_id,))
    if existing:
        if str(existing["source_fingerprint"]) != source_fingerprint or str(existing["source_snapshot_id"]) != source_snapshot_id:
            raise ValueError("Canonical version id already exists with different source identity")
    else:
        _single_execute(database, """INSERT INTO canonical_schedule_versions(
                version_id,source_snapshot_id,source_fingerprint,status,parent_version_id,
                artifact_schema_version,metadata,approved_at)
                VALUES (?,?,?,?,?,?,?,?)""", (
            version_id, source_snapshot_id, source_fingerprint, selected_status,
            str(artifact.get("parent_version_id") or "") or None,
            str(artifact.get("schema_version") or ""),
            _json(database, {"summary": artifact.get("summary") or {}, "status": artifact.get("status"),
                             "authoritative": False}), approved_at,
        ))
    block_params: list[tuple[Any, ...]] = []
    for block_key, raw_block in blocks.items():
        block = _normalized_block(raw_block, artifact)
        if not isinstance(block, Mapping):
            continue
        slot = block.get("slot") if isinstance(block.get("slot"), Mapping) else {}
        derived = block.get("derived_from") if isinstance(block.get("derived_from"), Mapping) else {}
        block_params.append((
            version_id, str(block_key), block.get("weekday"), slot.get("start"), slot.get("end"),
            str(block.get("grade_scope") or ""), str(block.get("status") or "unresolved"),
            str(block.get("mode") or ""), str(derived.get("structural_fingerprint") or ""),
            _json(database, {"derived_from": derived, "source_cells": derived.get("source_cells") or []}),
            _json(database, block.get("evidence") or {}), _json(database, block.get("unresolved") or []),
        ))
    for offset in range(0, len(block_params), 5):
        chunk = block_params[offset:offset + 5]
        placeholders = ",".join("(" + ",".join("?" for _ in row) + ")" for row in chunk)
        _single_execute(database, f"""INSERT INTO canonical_schedule_blocks(
                version_id,block_key,weekday,start_time,end_time,grade_scope,status,mode,
                structural_fingerprint,source_provenance,evidence,unresolved)
                VALUES {placeholders} ON CONFLICT(version_id,block_key) DO NOTHING""",
            tuple(value for row in chunk for value in row))
    persisted_blocks: list[Any] = []
    for offset in range(0, max(len(block_params), 1), 5):
        persisted_blocks.extend(_single_fetchall(
            database,
            "SELECT id,block_key FROM canonical_schedule_blocks WHERE version_id=? ORDER BY id LIMIT ? OFFSET ?",
            (version_id, 5, offset),
        ))
    block_ids = {str(row["block_key"]): row["id"] for row in persisted_blocks}
    assignment_params: list[tuple[Any, ...]] = []
    for block_key, raw_block in blocks.items():
        block = _normalized_block(raw_block, artifact)
        if not isinstance(block, Mapping) or str(block_key) not in block_ids:
            continue
        for ordinal, assignment in enumerate(block.get("assignments") or []):
            if isinstance(assignment, Mapping):
                assignment_params.append((block_ids[str(block_key)], ordinal, *_assignment_record(database, assignment)))
    for offset in range(0, len(assignment_params), 2):
        chunk = assignment_params[offset:offset + 2]
        placeholders = ",".join("(" + ",".join("?" for _ in row) + ")" for row in chunk)
        _single_execute(database, f"""INSERT INTO canonical_schedule_assignments(
                block_id,ordinal,activity,assignment_kind,audience_kind,canonical_group_ids,
                source_cells,teacher_ids,metadata) VALUES {placeholders}
                ON CONFLICT(block_id,ordinal) DO NOTHING""", tuple(value for row in chunk for value in row))
    inserted_blocks = len(persisted_blocks)
    inserted_assignments = int(_single_fetchone(database, """SELECT COUNT(*) AS value
        FROM canonical_schedule_assignments a JOIN canonical_schedule_blocks b ON b.id=a.block_id
        WHERE b.version_id=?""", (version_id,))["value"])
    if not _single_fetchone(database, "SELECT 1 FROM audit_log WHERE action='canonical_schedule.imported' AND details->>'version_id' = ? LIMIT 1", (version_id,)):
        _single_execute(database, """INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details)
            VALUES (NULL,'canonical_schedule.imported','canonical_schedule_version',NULL,?)""", (
            _json(database, {"version_id": version_id, "status": selected_status,
                              "blocks": inserted_blocks, "assignments": inserted_assignments,
                              "idempotent": False, "connection_mode": "pooler_autocommit"}),
        ))
    return {"version_id": version_id, "status": selected_status, "blocks": inserted_blocks,
            "assignments": inserted_assignments, "idempotent": bool(existing)}


def import_canonical_artifact(database: Database, artifact: Mapping[str, Any], *, status: str | None = None,
                              approved_at: str | None = None) -> dict[str, Any]:
    """Import one canonical artifact exactly once and preserve its version id."""
    version_id = str(artifact.get("version_id") or "").strip()
    if not version_id:
        raise ValueError("Canonical artifact must contain version_id")
    source = artifact.get("source_snapshot") if isinstance(artifact.get("source_snapshot"), Mapping) else {}
    source_snapshot_id = str(source.get("id") or source.get("spreadsheet_id") or "").strip()
    source_fingerprint = str(source.get("fingerprint") or "").strip()
    if not source_snapshot_id or not source_fingerprint:
        raise ValueError("Canonical artifact must contain source snapshot id and fingerprint")
    selected_status = status or ("approved_with_exceptions" if artifact.get("unresolved") else "approved_baseline")
    if selected_status not in CANONICAL_STATUSES:
        raise ValueError(f"Unsupported canonical status: {selected_status}")
    blocks = _artifact_blocks(artifact)
    if database.autocommit:
        return _import_canonical_artifact_reconnect(
            database, artifact, status=status, approved_at=approved_at, version_id=version_id,
            source_snapshot_id=source_snapshot_id, source_fingerprint=source_fingerprint,
            selected_status=selected_status, blocks=blocks,
        )
    with database.connection() as connection:
        existing = database.execute(connection, "SELECT * FROM canonical_schedule_versions WHERE version_id = ?", (version_id,)).fetchone()
        if existing:
            if str(existing["source_fingerprint"]) != source_fingerprint or str(existing["source_snapshot_id"]) != source_snapshot_id:
                raise ValueError("Canonical version id already exists with different source identity")
        else:
            row = database.execute(connection, """INSERT INTO canonical_schedule_versions(
                    version_id,source_snapshot_id,source_fingerprint,status,parent_version_id,
                    artifact_schema_version,metadata,approved_at)
                    VALUES (?,?,?,?,?,?,?,?) RETURNING version_id""", (
                version_id, source_snapshot_id, source_fingerprint, selected_status,
                str(artifact.get("parent_version_id") or "") or None,
                str(artifact.get("schema_version") or ""),
                _json(database, {"summary": artifact.get("summary") or {}, "status": artifact.get("status"),
                                 "authoritative": False}), approved_at,
            )).fetchone()
            if not row:
                raise RuntimeError("Canonical version insert did not return a row")
        block_params: list[tuple[Any, ...]] = []
        for block_key, raw_block in blocks.items():
            block = _normalized_block(raw_block, artifact)
            if not isinstance(block, Mapping):
                continue
            slot = block.get("slot") if isinstance(block.get("slot"), Mapping) else {}
            derived = block.get("derived_from") if isinstance(block.get("derived_from"), Mapping) else {}
            source_provenance = {"derived_from": derived, "source_cells": derived.get("source_cells") or []}
            block_params.append((
                version_id, str(block_key), block.get("weekday"), slot.get("start"), slot.get("end"),
                str(block.get("grade_scope") or ""), str(block.get("status") or "unresolved"),
                str(block.get("mode") or ""), str(derived.get("structural_fingerprint") or ""),
                _json(database, source_provenance), _json(database, block.get("evidence") or {}),
                _json(database, block.get("unresolved") or []),
            ))
        if block_params:
            for offset in range(0, len(block_params), 5):
                chunk = block_params[offset:offset + 5]
                placeholders = ",".join("(" + ",".join("?" for _ in row) + ")" for row in chunk)
                database.execute(connection, f"""INSERT INTO canonical_schedule_blocks(
                        version_id,block_key,weekday,start_time,end_time,grade_scope,status,mode,
                        structural_fingerprint,source_provenance,evidence,unresolved)
                        VALUES {placeholders}
                        ON CONFLICT(version_id,block_key) DO NOTHING""",
                    tuple(value for row in chunk for value in row))
        persisted_blocks = database.execute(connection, "SELECT id,block_key FROM canonical_schedule_blocks WHERE version_id=?", (version_id,)).fetchall()
        block_ids = {str(row["block_key"]): row["id"] for row in persisted_blocks}
        assignment_params: list[tuple[Any, ...]] = []
        for block_key, raw_block in blocks.items():
            block = _normalized_block(raw_block, artifact)
            if not isinstance(block, Mapping) or str(block_key) not in block_ids:
                continue
            for ordinal, assignment in enumerate(block.get("assignments") or []):
                if not isinstance(assignment, Mapping):
                    continue
                assignment_params.append((
                    block_ids[str(block_key)], ordinal, *_assignment_record(database, assignment),
                ))
        if assignment_params:
            for offset in range(0, len(assignment_params), 10):
                chunk = assignment_params[offset:offset + 10]
                placeholders = ",".join("(" + ",".join("?" for _ in row) + ")" for row in chunk)
                database.execute(connection, f"""INSERT INTO canonical_schedule_assignments(
                            block_id,ordinal,activity,assignment_kind,audience_kind,canonical_group_ids,
                            source_cells,teacher_ids,metadata) VALUES {placeholders}
                            ON CONFLICT(block_id,ordinal) DO NOTHING""",
                    tuple(value for row in chunk for value in row))
        inserted_blocks = len(persisted_blocks)
        inserted_assignments = int(database.execute(connection, """SELECT COUNT(*) AS value
            FROM canonical_schedule_assignments a JOIN canonical_schedule_blocks b ON b.id=a.block_id
            WHERE b.version_id=?""", (version_id,)).fetchone()["value"])
        database.execute(connection, """INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details)
                VALUES (NULL,'canonical_schedule.imported','canonical_schedule_version',NULL,?)""", (
            _json(database, {"version_id": version_id, "status": selected_status,
                              "blocks": inserted_blocks, "assignments": inserted_assignments,
                              "idempotent": False}),
            )) if not database.execute(connection, "SELECT 1 FROM audit_log WHERE action='canonical_schedule.imported' AND details->>'version_id' = ? LIMIT 1", (version_id,)).fetchone() else None
        return {"version_id": version_id, "status": selected_status, "blocks": inserted_blocks,
                "assignments": inserted_assignments, "idempotent": bool(existing)}


def publication_head(database: Database, scope_key: str) -> dict[str, Any] | None:
    """Optional manual pointer; old installations remain readable pre-migration.

    Detect the table before querying it (a missing-table exception would abort a
    PostgreSQL transaction). Explicit source artifacts stay immutable/available.
    """
    with database.connection() as connection:
        exists = database.execute(connection,
            "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='schedule_publication_heads'"
            if database.database_url else
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_publication_heads'").fetchone()
        if not exists:
            return None
        row = database.execute(connection, "SELECT * FROM schedule_publication_heads WHERE scope_key=?", (scope_key,)).fetchone()
        return dict(row) if row else None


def canonical_version(database: Database, version_id: str | None = None) -> dict[str, Any] | None:
    if version_id is None:
        head = publication_head(database, "template")
        if head:
            version_id = head["version_id"]
    with database.connection() as connection:
        row = database.execute(connection, "SELECT * FROM canonical_schedule_versions WHERE version_id = ?", (version_id,)).fetchone() if version_id else database.execute(connection, """SELECT * FROM canonical_schedule_versions
            WHERE status <> 'superseded' ORDER BY CASE status WHEN 'authoritative' THEN 0 WHEN 'approved_with_exceptions' THEN 1 WHEN 'approved_baseline' THEN 2 ELSE 3 END, COALESCE(approved_at,created_at) DESC, created_at DESC, version_id DESC LIMIT 1""").fetchone()
        if not row:
            return None
        item = dict(row)
        item["metadata"] = _decode(database, item.get("metadata"), {})
        return item


def read_canonical_template(database: Database, version_id: str | None = None) -> dict[str, Any] | None:
    """Read the persisted semantic template without reconstructing student sets."""
    version = canonical_version(database, version_id)
    if not version:
        return None
    with database.connection() as connection:
        blocks = database.execute(
            connection,
            "SELECT * FROM canonical_schedule_blocks WHERE version_id=? ORDER BY weekday,start_time,id",
            (version["version_id"],),
        ).fetchall()
        assignments_by_block: dict[str, list[Any]] = {}
        if blocks:
            placeholders = ",".join("?" for _ in blocks)
            assignment_rows = database.execute(
                connection,
                f"SELECT * FROM canonical_schedule_assignments WHERE block_id IN ({placeholders}) ORDER BY block_id,ordinal",
                tuple(row["id"] for row in blocks),
            ).fetchall()
            for assignment_row in assignment_rows:
                assignments_by_block.setdefault(str(assignment_row["block_id"]), []).append(assignment_row)
        result_blocks: dict[str, Any] = {}
        for row in blocks:
            block = dict(row)
            block["source_provenance"] = _decode(database, block.get("source_provenance"), {})
            block["evidence"] = _decode(database, block.get("evidence"), {})
            block["unresolved"] = _decode(database, block.get("unresolved"), [])
            assignments = assignments_by_block.get(str(row["id"]), [])
            normalized = []
            for assignment_row in assignments:
                assignment = dict(assignment_row)
                assignment["canonical_group_ids"] = _decode(database, assignment.get("canonical_group_ids"), [])
                assignment["source_cells"] = _decode(database, assignment.get("source_cells"), [])
                assignment["teacher_ids"] = _decode(database, assignment.get("teacher_ids"), [])
                assignment["metadata"] = _assignment_metadata(_decode(database, assignment.get("metadata"), {}))
                normalized.append(assignment)
            block["assignments"] = normalized
            result_blocks[block["block_key"]] = block
        parent_version_id = str(version.get("parent_version_id") or "").strip()
        if parent_version_id and result_blocks:
            parent_rows = database.execute(
                connection,
                "SELECT block_key,start_time,end_time FROM canonical_schedule_blocks WHERE version_id=?",
                (parent_version_id,),
            ).fetchall()
            parent_times = {str(row["block_key"]): row for row in parent_rows}
            for block_key, block in result_blocks.items():
                parent = parent_times.get(str(block_key))
                if not parent:
                    continue
                recovered = False
                if not block.get("start_time") and parent["start_time"]:
                    block["start_time"] = parent["start_time"]
                    recovered = True
                if not block.get("end_time") and parent["end_time"]:
                    block["end_time"] = parent["end_time"]
                    recovered = True
                if recovered:
                    block["time_recovered_from_parent"] = True
    return {
        "schema_version": version.get("artifact_schema_version") or "",
        "version_id": version["version_id"],
        "parent_version_id": version.get("parent_version_id"),
        "status": version["status"],
        "source_snapshot": {
            "id": version["source_snapshot_id"],
            "fingerprint": version["source_fingerprint"],
        },
        "metadata": version.get("metadata") or {},
        "blocks": result_blocks,
    }


def materialize_effective_week(database: Database, version_id: str, week_start: str,
                               patches: Sequence[Mapping[str, Any]] = (), *,
                               overlay_source_snapshot_id: str | None = None,
                               overlay_fingerprint: str | None = None,
                               overlay_observed_at: str | None = None) -> dict[str, Any]:
    """Create an immutable effective week from canonical blocks and patches."""
    try:
        start = date.fromisoformat(week_start)
    except ValueError as error:
        raise ValueError("week_start must be an ISO date") from error
    patch_by_key = {str(item.get("block_key")): dict(item) for item in patches if item.get("block_key")}
    # A source week can be corrected after it was first materialized.  Keep each
    # observed overlay immutable instead of overwriting the earlier effective
    # week.  The deterministic fingerprint also makes retrying the same source
    # idempotent.
    revision = str(overlay_fingerprint or _hash({
        "overlay_source_snapshot_id": overlay_source_snapshot_id or "",
        "patches": patch_by_key,
    }))
    revision_at = str(overlay_observed_at or datetime.now(timezone.utc).isoformat())
    effective_id = _hash({"version_id": version_id, "week_start": week_start, "overlay_fingerprint": revision})
    if database.autocommit:
        return _materialize_effective_week_reconnect(
            database, version_id, start.isoformat(), effective_id, patch_by_key,
            overlay_source_snapshot_id=overlay_source_snapshot_id, overlay_fingerprint=revision,
            overlay_observed_at=revision_at,
        )
    with database.connection() as connection:
        version = database.execute(connection, "SELECT version_id FROM canonical_schedule_versions WHERE version_id = ?", (version_id,)).fetchone()
        if not version:
            raise ValueError("Canonical version was not found")
        existing = database.execute(connection, "SELECT * FROM canonical_effective_weeks WHERE effective_week_id = ?", (effective_id,)).fetchone()
        if existing:
            return {**dict(existing), "idempotent": True}
        database.execute(connection, """INSERT INTO canonical_effective_weeks(
                effective_week_id,version_id,week_start,overlay_fingerprint,revision_at,status,overlay_source_snapshot_id,provenance)
                VALUES (?,?,?,?,?,?,?,?)""", (effective_id, version_id, start.isoformat(), revision, revision_at, "materialized",
                overlay_source_snapshot_id, _json(database, {"patch_count": len(patch_by_key), "overlay_fingerprint": revision})))
        blocks = database.execute(connection, "SELECT id,block_key FROM canonical_schedule_blocks WHERE version_id=? ORDER BY weekday,start_time,id", (version_id,)).fetchall()
        known = {str(row["block_key"]): row for row in blocks}
        for block_key, row in known.items():
            patch = patch_by_key.get(block_key)
            change_kind = str((patch or {}).get("change_kind") or "unchanged")
            if change_kind not in {"unchanged", "metadata", "cancelled", "replaced", "added", "moved", "weekly_only"}:
                raise ValueError(f"Unsupported effective block change kind: {change_kind}")
            database.execute(connection, """INSERT INTO canonical_effective_blocks(
                    effective_week_id,canonical_block_id,block_key,change_kind,patch)
                    VALUES (?,?,?,?,?)""", (effective_id, row["id"], block_key, change_kind, _json(database, patch or {})))
        for block_key, patch in patch_by_key.items():
            if block_key in known:
                continue
            change_kind = str(patch.get("change_kind") or "weekly_only")
            if change_kind not in {"added", "moved", "replaced", "weekly_only", "metadata", "cancelled", "unchanged"}:
                raise ValueError(f"Unsupported effective block change kind: {change_kind}")
            database.execute(connection, """INSERT INTO canonical_effective_blocks(
                    effective_week_id,canonical_block_id,block_key,change_kind,patch)
                    VALUES (?,?,?,?,?)""", (effective_id, None, block_key, change_kind, _json(database, patch)))
        return {"effective_week_id": effective_id, "version_id": version_id, "week_start": start.isoformat(),
                "patch_count": len(patch_by_key), "idempotent": False}


def _materialize_effective_week_reconnect(database: Database, version_id: str, week_start: str,
                                          effective_id: str, patch_by_key: Mapping[str, Mapping[str, Any]], *,
                                          overlay_source_snapshot_id: str | None,
                                          overlay_fingerprint: str,
                                          overlay_observed_at: str) -> dict[str, Any]:
    """Pooler-safe effective-week materialization using short-lived connections."""
    if not _single_fetchone(database, "SELECT version_id FROM canonical_schedule_versions WHERE version_id=?", (version_id,)):
        raise ValueError("Canonical version was not found")
    existing = _single_fetchone(database, "SELECT * FROM canonical_effective_weeks WHERE effective_week_id=?", (effective_id,))
    if existing:
        return {**dict(existing), "idempotent": True}
    _single_execute(database, """INSERT INTO canonical_effective_weeks(
            effective_week_id,version_id,week_start,overlay_fingerprint,revision_at,status,overlay_source_snapshot_id,provenance)
            VALUES (?,?,?,?,?,?,?,?)""", (
        effective_id, version_id, week_start, overlay_fingerprint, overlay_observed_at, "materialized", overlay_source_snapshot_id,
        _json(database, {"patch_count": len(patch_by_key), "connection_mode": "pooler_autocommit", "overlay_fingerprint": overlay_fingerprint}),
    ))
    blocks: list[Any] = []
    offset = 0
    while True:
        page = _single_fetchall(database, """SELECT id,block_key FROM canonical_schedule_blocks
            WHERE version_id=? ORDER BY weekday,start_time,id LIMIT ? OFFSET ?""", (version_id, 5, offset))
        if not page:
            break
        blocks.extend(page)
        if len(page) < 5:
            break
        offset += len(page)
    known = {str(row["block_key"]): row for row in blocks}
    effective_rows: list[tuple[Any, ...]] = []
    for block_key, row in known.items():
        patch = patch_by_key.get(block_key)
        change_kind = str((patch or {}).get("change_kind") or "unchanged")
        if change_kind not in {"unchanged", "metadata", "cancelled", "replaced", "added", "moved", "weekly_only"}:
            raise ValueError(f"Unsupported effective block change kind: {change_kind}")
        effective_rows.append((effective_id, row["id"], block_key, change_kind, _json(database, patch or {})))
    for block_key, patch in patch_by_key.items():
        if block_key in known:
            continue
        change_kind = str(patch.get("change_kind") or "weekly_only")
        if change_kind not in {"added", "moved", "replaced", "weekly_only", "metadata", "cancelled", "unchanged"}:
            raise ValueError(f"Unsupported effective block change kind: {change_kind}")
        effective_rows.append((effective_id, None, block_key, change_kind, _json(database, patch)))
    for offset in range(0, len(effective_rows), 2):
        chunk = effective_rows[offset:offset + 2]
        placeholders = ",".join("(" + ",".join("?" for _ in row) + ")" for row in chunk)
        _single_execute(database, f"""INSERT INTO canonical_effective_blocks(
                effective_week_id,canonical_block_id,block_key,change_kind,patch)
                VALUES {placeholders}""", tuple(value for row in chunk for value in row))
    return {"effective_week_id": effective_id, "version_id": version_id, "week_start": week_start,
            "patch_count": len(patch_by_key), "idempotent": False}


def _membership_sets(database: Database, day: str) -> dict[str, set[str]]:
    membership_query = """SELECT m.group_id,m.identity_id,m.valid_from,m.valid_until
        FROM memberships m JOIN identities i ON i.id=m.identity_id JOIN groups g ON g.id=m.group_id
        WHERE m.active IS TRUE AND m.member_role='student' AND i.kind='student' AND i.status='active' AND g.canonical IS TRUE
        ORDER BY m.id LIMIT ? OFFSET ?"""
    override_query = """SELECT identity_id,group_id FROM membership_overrides
        WHERE active IS TRUE AND action='exclude' AND (valid_from IS NULL OR valid_from <= ?) AND (valid_until IS NULL OR valid_until >= ?)
        ORDER BY id LIMIT ? OFFSET ?"""
    if database.autocommit:
        rows: list[Any] = []
        offset = 0
        while True:
            page = _single_fetchall(database, membership_query, (50, offset))
            rows.extend(page)
            if len(page) < 50:
                break
            offset += len(page)
        excluded_rows: list[Any] = []
        offset = 0
        while True:
            page = _single_fetchall(database, override_query, (day, day, 50, offset))
            excluded_rows.extend(page)
            if len(page) < 50:
                break
            offset += len(page)
    else:
        with database.connection() as connection:
            rows = database.execute(connection, """SELECT m.group_id,m.identity_id,m.valid_from,m.valid_until
                FROM memberships m JOIN identities i ON i.id=m.identity_id
                WHERE m.active IS TRUE AND m.member_role='student' AND i.kind='student' AND i.status='active'""").fetchall()
            excluded_rows = database.execute(connection, """SELECT identity_id,group_id FROM membership_overrides
                WHERE active IS TRUE AND action='exclude' AND (valid_from IS NULL OR valid_from <= ?) AND (valid_until IS NULL OR valid_until >= ?)""", (day, day)).fetchall()
    excluded = {(str(row["identity_id"]), str(row["group_id"])) for row in excluded_rows}
    result: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        identity_id, group_id = str(row["identity_id"]), str(row["group_id"])
        if (identity_id, group_id) in excluded:
            continue
        start, end = str(row["valid_from"] or ""), str(row["valid_until"] or "")
        if (not start or start <= day) and (not end or end >= day):
            result[group_id].add(identity_id)
    return result


def _active_students(database: Database) -> dict[str, dict[str, Any]]:
    if database.autocommit:
        rows: list[Any] = []
        offset = 0
        while True:
            page = _single_fetchall(database, "SELECT id,display_name,class_name FROM identities WHERE kind='student' AND status='active' ORDER BY id LIMIT ? OFFSET ?", (50, offset))
            rows.extend(page)
            if len(page) < 50:
                break
            offset += len(page)
        return {str(row["id"]): dict(row) for row in rows}
    with database.connection() as connection:
        return {str(row["id"]): dict(row) for row in database.execute(connection, "SELECT id,display_name,class_name FROM identities WHERE kind='student' AND status='active'").fetchall()}


def _canonical_group_ids(database: Database) -> set[str]:
    if database.autocommit:
        rows: list[Any] = []
        offset = 0
        while True:
            page = _single_fetchall(database, "SELECT id FROM groups WHERE canonical IS TRUE ORDER BY id LIMIT ? OFFSET ?", (50, offset))
            rows.extend(page)
            if len(page) < 50:
                break
            offset += len(page)
        return {str(row["id"]) for row in rows}
    with database.connection() as connection:
        return {str(row["id"]) for row in database.execute(connection, "SELECT id FROM groups WHERE canonical IS TRUE").fetchall()}


def _all_rows(database: Database, query: str, *, page_size: int = 1000) -> list[Any]:
    if not database.autocommit:
        with database.connection() as connection:
            return list(database.execute(connection, query).fetchall())
    rows: list[Any] = []
    offset = 0
    while True:
        page = _single_fetchall(database, f"{query} LIMIT ? OFFSET ?", (page_size, offset))
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += len(page)


def build_student_projection_context(
    database: Database,
    week_start: str,
    *,
    version_id: str | None = None,
    publication: PublishedWeek | None = None,
) -> StudentProjectionContext | None:
    """Load mutable directory state once; never retain it on ``Database``."""
    effective = publication.effective if publication else _effective_week(database, version_id, week_start)
    if not effective:
        return None
    students = {
        str(row["id"]): dict(row)
        for row in _all_rows(
            database,
            "SELECT id,display_name,class_name,status FROM identities WHERE kind='student' ORDER BY id",
        )
    }
    groups = {
        str(row["id"]): dict(row)
        for row in _all_rows(
            database,
            """SELECT id,name,display_name,group_type,subject,base_class_name,subject_subgroup,
                      exam_track,role,semantic_dimension,canonical FROM groups ORDER BY id""",
        )
    }
    memberships = [
        dict(row)
        for row in _all_rows(
            database,
            """SELECT m.id,m.group_id,m.identity_id,m.member_role,m.source,m.source_ref,m.active,
                      m.valid_from,m.valid_until,i.status AS identity_status,i.kind AS identity_kind,
                      g.canonical AS group_canonical
                 FROM memberships m
                 LEFT JOIN identities i ON i.id=m.identity_id
                 LEFT JOIN groups g ON g.id=m.group_id
                ORDER BY m.id""",
        )
    ]
    membership_overrides = [
        dict(row)
        for row in _all_rows(
            database,
            """SELECT id,identity_id,group_id,member_role,action,active,valid_from,valid_until
                 FROM membership_overrides
                WHERE active IS TRUE AND action IN ('include','exclude') AND member_role='student'
                ORDER BY id""",
        )
    ]
    return StudentProjectionContext(
        effective=dict(effective),
        students=students,
        groups=groups,
        memberships=memberships,
        membership_overrides=membership_overrides,
        blocks=_block_rows(database, str(effective["effective_week_id"])),
    )


def _date_in_period(day: str, start: Any, end: Any) -> bool:
    start_text, end_text = str(start or ""), str(end or "")
    return (not start_text or start_text <= day) and (not end_text or end_text >= day)


def _student_memberships(context: StudentProjectionContext, student_id: str, day: str) -> set[str]:
    cache_key = (student_id, day)
    cached = context.membership_cache.get(cache_key)
    if cached is not None:
        return cached
    overrides: dict[str, set[str]] = defaultdict(set)
    for row in context.override_rows_by_student.get(student_id, []):
        if _date_in_period(day, row.get("valid_from"), row.get("valid_until")):
            overrides[str(row["action"])].add(str(row["group_id"]))
    result = {
        str(row["group_id"])
        for row in context.membership_rows_by_student.get(student_id, [])
        if _active_student_membership_on(row, day)
    }
    if (context.students.get(student_id) or {}).get("status") == "active":
        result.update(group_id for group_id in overrides["include"]
                      if (context.groups.get(group_id) or {}).get("canonical"))
    result.difference_update(overrides["exclude"])
    context.membership_cache[cache_key] = result
    return result


def _active_student_membership_on(row: Mapping[str, Any], day: str) -> bool:
    return (bool(row.get("active")) and row.get("member_role") == "student"
            and row.get("identity_status") == "active" and row.get("identity_kind") == "student"
            and bool(row.get("group_canonical"))
            and _date_in_period(day, row.get("valid_from"), row.get("valid_until")))


def _projection_grade(context: StudentProjectionContext, student: Mapping[str, Any]) -> str:
    student_id = str(student.get("id") or "")
    if student_id not in context.student_grade_cache:
        context.student_grade_cache[student_id] = _grade(student.get("class_name"))
    return context.student_grade_cache[student_id]


def _projection_class_context(context: StudentProjectionContext, grade: str, class_group_id: str) -> set[str]:
    key = (grade, class_group_id)
    if key not in context.class_context_cache:
        context.class_context_cache[key] = class_context_group_ids(context.groups, grade, class_group_id)
    return context.class_context_cache[key]


def _projection_group_dimension(context: StudentProjectionContext, group_id: str) -> tuple[str, str]:
    if group_id not in context.group_dimension_cache:
        group = context.groups.get(group_id) or {}
        context.group_dimension_cache[group_id] = _group_dimension(group)
    return context.group_dimension_cache[group_id]


def _group_dimension(group: Mapping[str, Any]) -> tuple[str, str]:
    explicit = str(group.get("semantic_dimension") or "").strip()
    if explicit:
        return explicit, "directory.semantic_dimension"
    role = str(group.get("role") or "").strip()
    group_type = str(group.get("group_type") or "").strip()
    name = " ".join(str(group.get(key) or "") for key in ("name", "display_name", "subject", "base_class_name")).lower()
    grade = _grade(group.get("base_class_name") or group.get("name")) or "unknown"
    if role == "base_class" or group_type == "class":
        return f"base_class:{grade}", "fallback:group_role_or_type"
    subject = str(group.get("subject") or "").strip().lower()
    if not subject:
        if "math" in name or "матем" in name:
            subject = "math"
        elif "english" in name or "англ" in name:
            subject = "english"
    if role == "instructional_partition" or group_type in {"instructional", "instructional_group", "subject_group"}:
        if subject:
            return f"instructional:{grade}:{subject}", "fallback:instructional_subject"
        return f"instructional:{grade}:unknown", "fallback:instructional_unknown"
    if role == "elective_or_special" or group_type == "exam_track":
        subject = subject or str(group.get("exam_track") or "unknown").strip().lower()
        return f"elective:{grade}:{subject}", "fallback:elective_subject"
    return "unknown", "fallback:insufficient_directory_metadata"


def _assignment_group_ids(assignment: Mapping[str, Any]) -> list[str]:
    audience = assignment.get("audience") if isinstance(assignment.get("audience"), Mapping) else {}
    return [str(value) for value in assignment.get("canonical_group_ids") or audience.get("canonical_group_ids") or []]


def _assignment_audience_rule(assignment: Mapping[str, Any]) -> dict[str, Any]:
    metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
    rule = metadata.get("audience_rule") if isinstance(metadata.get("audience_rule"), Mapping) else {}
    return dict(rule)


def _assignment_role(assignment: Mapping[str, Any]) -> str:
    return str(assignment.get("assignment_kind") or assignment.get("role") or assignment.get("kind") or "primary")


def _assignment_id(block: Mapping[str, Any], assignment: Mapping[str, Any], index: int) -> str:
    return str(assignment.get("id") or f"{block.get('block_key')}:assignment:{index}")


def _grade(value: Any) -> str:
    text = str(value or "").strip()
    match = re.match(r"(\d+)", text)
    return match.group(1) if match else ""


def canonical_group_grade(group: Mapping[str, Any]) -> str:
    """Return the canonical grade carried by a class group, never a display guess."""
    if group.get("group_type") != "class" and group.get("role") != "base_class":
        return ""
    for field in ("base_class_name", "display_name", "name"):
        grade = _grade(group.get(field))
        if grade:
            return grade
    technical = re.match(r"^grade(\d{1,2}):base:", str(group.get("name") or ""))
    return technical.group(1) if technical else ""


def resolve_grade_day_mode(payload: Mapping[str, Any], grade: str, weekday: int,
                           class_grades: Mapping[str, str]) -> tuple[str | None, list[str]]:
    explicit = (payload.get("grade_modes") or {}).get(str(grade)) or {}
    day = str(weekday)
    if day in explicit:
        return str(explicit[day]), []
    legacy = payload.get("day_modes") or {}
    class_ids = sorted(class_id for class_id, value in class_grades.items() if value == str(grade))
    values = {str((legacy.get(class_id) or {}).get(day) or "normal") for class_id in class_ids}
    if len(values) > 1:
        return None, class_ids
    return (next(iter(values)) if values else "normal"), []


def class_context_group_ids(groups: Mapping[str, Mapping[str, Any]], grade: str, class_group_id: str = "") -> set[str]:
    """Resolve a single grid's base-class cohort from canonical directory groups."""
    if class_group_id:
        group = groups.get(class_group_id)
        return {class_group_id} if group and group.get("canonical") and group.get("group_type") == "class" and (not grade or _grade(group.get("base_class_name") or group.get("name")) == grade) else set()
    if not grade or not grade.isdigit():
        return set()
    return {
        group_id for group_id, group in groups.items()
        if group.get("canonical") and group.get("group_type") == "class"
        and _grade(group.get("base_class_name") or group.get("name")) == grade
    }


def _block_rows(database: Database, effective_week_id: str) -> list[dict[str, Any]]:
    cache = getattr(database, "_canonical_block_cache", None)
    if cache is None:
        cache = {}
        setattr(database, "_canonical_block_cache", cache)
    if effective_week_id in cache:
        return cache[effective_week_id]
    base_query = """SELECT eb.block_key,eb.change_kind,eb.patch,
                   cb.id AS canonical_block_id,cb.weekday,cb.start_time,cb.end_time,cb.grade_scope,
                   cb.status,cb.mode,cb.source_provenance,cb.evidence,cb.unresolved
              FROM canonical_effective_blocks eb
              LEFT JOIN canonical_schedule_blocks cb ON cb.id=eb.canonical_block_id
             WHERE eb.effective_week_id=? ORDER BY COALESCE(cb.weekday,99),cb.start_time,eb.id"""
    if database.autocommit:
        rows: list[Any] = []
        offset = 0
        while True:
            page = _single_fetchall(database, base_query + " LIMIT ? OFFSET ?", (effective_week_id, 500, offset))
            rows.extend(page)
            if len(page) < 500:
                break
            offset += len(page)
    else:
        with database.connection() as connection:
            rows = database.execute(connection, base_query, (effective_week_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["patch"] = _decode(database, item.get("patch"), {})
        item["source_provenance"] = _decode(database, item.get("source_provenance"), {})
        item["evidence"] = _decode(database, item.get("evidence"), {})
        item["unresolved"] = _decode(database, item.get("unresolved"), [])
        patch = item["patch"] if isinstance(item["patch"], Mapping) else {}
        slot = patch.get("slot") if isinstance(patch.get("slot"), Mapping) else {}
        if patch.get("weekday") is not None:
            item["weekday"] = patch["weekday"]
        if slot.get("start") is not None:
            item["start_time"] = slot["start"]
        if slot.get("end") is not None:
            item["end_time"] = slot["end"]
        if patch.get("grade_scope") is not None:
            item["grade_scope"] = patch["grade_scope"]
        if patch.get("source_provenance") is not None:
            item["source_provenance"] = patch["source_provenance"]
        result.append(item)
    _prefetch_block_assignments(database, result)
    cache[effective_week_id] = result
    return result


def _load_block_assignments(database: Database, block_ids: list[Any]) -> dict[str, list[dict[str, Any]]]:
    assignments: dict[str, list[dict[str, Any]]] = {str(block_id): [] for block_id in block_ids}
    if not block_ids:
        return assignments
    with database.connection() as connection:
        for offset in range(0, len(block_ids), 500):
            batch = block_ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = database.execute(connection,
                f"SELECT * FROM canonical_schedule_assignments WHERE block_id IN ({placeholders}) ORDER BY block_id,ordinal",
                tuple(batch)).fetchall()
            for row in rows:
                item = dict(row)
                for key in ("canonical_group_ids", "source_cells", "teacher_ids", "metadata"):
                    decoded = _decode(database, item.get(key), [] if key != "metadata" else {})
                    item[key] = _assignment_metadata(decoded) if key == "metadata" else decoded
                assignments[str(row["block_id"])].append(item)
    return assignments


def _assignment_cache(database: Database) -> dict[str, list[dict[str, Any]]]:
    cache = getattr(database, "_canonical_assignment_cache", None)
    if cache is None:
        cache = {}
        setattr(database, "_canonical_assignment_cache", cache)
    return cache


def _prefetch_block_assignments(database: Database, blocks: list[dict[str, Any]]) -> None:
    cache = _assignment_cache(database)
    # Only the selected week's baseline blocks can supply assignments and
    # teacher inheritance. Building a cache from every version scales with
    # publication history, not the schedule the user opened.
    block_ids = list(dict.fromkeys(block["canonical_block_id"] for block in blocks
        if block.get("change_kind") != "cancelled" and block.get("canonical_block_id") is not None
        and str(block["canonical_block_id"]) not in cache))
    cache.update(_load_block_assignments(database, block_ids))


def _assignments(database: Database, block_id: Any, patch: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    if patch and isinstance(patch.get("assignments"), list):
        result = [dict(item) for item in patch["assignments"] if isinstance(item, Mapping)]
        # A weekly patch replaces the assignment payload, but an omitted teacher
        # is not evidence that a reviewed baseline teacher was removed. Preserve
        # that proof when the source cell and canonical audience are unchanged.
        if block_id is not None and any(not item.get("teacher_ids") and item.get("source_cells") for item in result):
            baseline = _assignments(database, block_id)

            def source_cells(assignment: Mapping[str, Any]) -> set[str]:
                values = assignment.get("source_cells") or []
                result: set[str] = set()
                for item in values:
                    value = (item.get("coordinate") or item.get("cell")) if isinstance(item, Mapping) else item
                    if value:
                        result.add(str(value))
                return result

            def group_ids(assignment: Mapping[str, Any]) -> set[str]:
                audience = assignment.get("audience") if isinstance(assignment.get("audience"), Mapping) else {}
                return {str(value) for value in audience.get("canonical_group_ids") or assignment.get("canonical_group_ids") or []}

            for item in result:
                if item.get("teacher_ids"):
                    continue
                item_cells = source_cells(item)
                item_groups = group_ids(item)
                candidates = [
                    candidate for candidate in baseline
                    if item_cells
                    and item_cells.intersection(source_cells(candidate))
                    and item_groups
                    and item_groups == group_ids(candidate)
                    and candidate.get("teacher_ids")
                ]
                if len(candidates) != 1:
                    continue
                candidate = candidates[0]
                item["teacher_ids"] = list(candidate.get("teacher_ids") or [])
                metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), Mapping) else {}
                baseline_metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), Mapping) else {}
                if not metadata.get("teachers") and baseline_metadata.get("teachers"):
                    metadata["teachers"] = list(baseline_metadata["teachers"])
                metadata["teacher_inheritance"] = {
                    "source": "canonical_baseline",
                    "reason": "weekly patch omitted teacher while source cell and canonical audience remained unchanged",
                }
                item["metadata"] = metadata
        return result
    if block_id is None:
        return []
    cache = _assignment_cache(database)
    cache_key = str(block_id)
    if cache_key not in cache:
        cache.update(_load_block_assignments(database, [block_id]))
    return cache[cache_key]


def _effective_week(database: Database, version_id: str | None, week_start: str) -> dict[str, Any] | None:
    # A manually published week is an independent immutable edition, pinned to
    # its own canonical base even after another template/source is imported.
    head = publication_head(database, f"week:{week_start}")
    if head:
        with database.connection() as connection:
            row = database.execute(connection, """SELECT ew.*,cv.status AS canonical_status,cv.source_snapshot_id,cv.source_fingerprint
                FROM canonical_effective_weeks ew JOIN canonical_schedule_versions cv ON cv.version_id=ew.version_id
                WHERE ew.effective_week_id=? AND ew.week_start=?""", (head["published_id"], week_start)).fetchone()
            if row:
                return dict(row)
    with database.connection() as connection:
        query = """SELECT ew.*,cv.status AS canonical_status,cv.source_snapshot_id,cv.source_fingerprint
                   FROM canonical_effective_weeks ew JOIN canonical_schedule_versions cv ON cv.version_id=ew.version_id
                  WHERE ew.week_start=?"""
        params: tuple[Any, ...] = (week_start,)
        if version_id:
            query += " AND ew.version_id=?"
            params += (version_id,)
        query += " ORDER BY ew.revision_at DESC, ew.created_at DESC LIMIT 1"
        row = database.execute(connection, query, params).fetchone()
        return dict(row) if row else None


def resolve_published_week(database: Database, day: str) -> PublishedWeek:
    """Select a school-visible edition independently of the current template and drafts."""
    monday = _week_start(day)
    scope = f"week:{monday}"
    metadata = {"week_start": monday, "origin": "missing", "published": False,
                "publication_id": None, "effective_week_id": None, "base_version_id": None,
                "revision": None, "coverage": "unknown"}
    with database.connection() as connection:
        tables = {row["name"] for row in database.execute(connection,
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema=current_schema() "
            "AND table_name IN ('schedule_publication_heads','schedule_constructor_publications')"
            if database.database_url else
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('schedule_publication_heads','schedule_constructor_publications')").fetchall()}
        # Read the head and its immutable target in the same SQL snapshot.
        join = ("LEFT JOIN schedule_publication_heads h ON h.scope_key=?"
                if "schedule_publication_heads" in tables else
                "LEFT JOIN (SELECT NULL AS published_id, NULL AS version_id, NULL AS revision) h ON 1=0")
        params = (scope, monday) if "schedule_publication_heads" in tables else (monday,)
        row = database.execute(connection, f"""
            SELECT h.published_id AS head_target,h.revision AS head_revision,
                   ew.*,cv.status AS canonical_status,cv.source_snapshot_id,cv.source_fingerprint
            FROM (SELECT 1 AS anchor) anchor {join}
            LEFT JOIN canonical_effective_weeks ew ON ew.week_start=?
                AND (h.published_id IS NULL OR (ew.effective_week_id=h.published_id AND ew.version_id=h.version_id))
            LEFT JOIN canonical_schedule_versions cv ON cv.version_id=ew.version_id
            ORDER BY CASE WHEN cv.status IN ('authoritative','approved_baseline','approved_with_exceptions')
                          THEN 0 ELSE 1 END,ew.revision_at DESC,ew.created_at DESC,ew.effective_week_id DESC LIMIT 1
            """, params).fetchone()
        effective = dict(row)
        manual = effective.pop("head_target") is not None
        revision = effective.pop("head_revision")
        broken = PublishedWeek({**metadata, "origin": "broken_head", "revision": revision}, "broken_publication")
        if manual and effective["canonical_status"] not in PUBLISHED_STATUSES:
            return broken
        if not effective["effective_week_id"]:
            template = canonical_version(database)
            if template and template["status"] in PUBLISHED_STATUSES:
                metadata.update(origin="template_only", base_version_id=template["version_id"])
            return PublishedWeek(metadata, "canonical_not_materialized")
        if effective["canonical_status"] not in PUBLISHED_STATUSES:
            return PublishedWeek(metadata, effective["canonical_status"])
        if manual:
            if "schedule_constructor_publications" not in tables:
                return broken
            archive = database.execute(connection, """SELECT id,payload_fingerprint,metadata
                FROM schedule_constructor_publications WHERE published_id=? AND version_id=?
                AND scope_key=? AND scope_kind='week' ORDER BY created_at DESC,id DESC LIMIT 1""",
                (effective["effective_week_id"], effective["version_id"], scope)).fetchone()
            if not archive:
                return broken
            from backend.services.constructor_publication import _checked_published_source
            try:
                source = _checked_published_source(json.loads(archive["metadata"]), archive["payload_fingerprint"])
            except (AttributeError, TypeError, ValueError, KeyError):
                return broken
            publication_id = archive["id"]
        else:
            source, publication_id = _published_source_for_effective(database, effective)
    coverage = "unknown"
    if source:
        coverage = "partial" if any(r.get("status") == "unresolved" for r in source["records"]) else "complete"
    elif effective["canonical_status"] == "approved_with_exceptions":
        coverage = "with_exceptions"
    metadata.update(origin="manual_week" if manual else "approved_materialization", published=True,
                    publication_id=publication_id if manual else None, effective_week_id=effective["effective_week_id"],
                    base_version_id=effective["version_id"], revision=revision or effective["overlay_fingerprint"],
                    coverage=coverage)
    return PublishedWeek(metadata, effective["canonical_status"], effective, source, publication_id)


def effective_status(database: Database, week_start: str | None = None) -> dict[str, Any]:
    version = canonical_version(database)
    result = {"canonical": version, "effective_week": None, "status": "canonical_not_imported" if not version else version["status"]}
    if week_start:
        # Historical weeks keep their own approved base after a newer template
        # is saved. Match the runtime selector rather than hiding that week.
        publication = resolve_published_week(database, week_start)
        result["publication"] = publication.metadata
        effective = publication.effective
        if publication.status == "broken_publication":
            result["status"] = publication.status
        result["effective_week"] = effective
        if effective:
            result["canonical"] = canonical_version(database, effective["version_id"])
            result["status"] = effective["canonical_status"]
    return result


def _admin_week_history(database: Database) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read effective-week versions and actual constructor publications separately."""
    with database.connection() as connection:
        effective = [dict(row) for row in database.execute(connection, """SELECT effective_week_id,version_id,
            week_start,status,created_at,revision_at,overlay_source_snapshot_id
            FROM canonical_effective_weeks ORDER BY week_start DESC,created_at DESC,effective_week_id""").fetchall()]
        table_query = ("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() "
                       "AND table_name IN ('schedule_publication_heads','schedule_constructor_publications')"
                       if database.database_url else
                       "SELECT name AS table_name FROM sqlite_master WHERE type='table' "
                       "AND name IN ('schedule_publication_heads','schedule_constructor_publications')")
        tables = {str(row["table_name"]) for row in database.execute(connection, table_query).fetchall()}
        heads = {}
        if "schedule_publication_heads" in tables:
            heads = {str(row["scope_key"]): dict(row) for row in database.execute(connection,
                "SELECT scope_key,published_id,version_id,revision,updated_at FROM schedule_publication_heads "
                "WHERE scope_key LIKE ?", ("week:%",)).fetchall()}
        publications = []
        if "schedule_constructor_publications" in tables:
            publications = [dict(row) for row in database.execute(connection, """SELECT id,draft_id,draft_revision,
                scope_key,base_version_id,previous_published_id,published_id,version_id,created_at
                FROM schedule_constructor_publications WHERE scope_kind='week' ORDER BY created_at DESC,id""").fetchall()]
    for item in effective:
        head = heads.get(f"week:{item['week_start']}")
        item["is_current_publication"] = bool(head and str(head["published_id"]) == str(item["effective_week_id"]))
    return effective, publications


def schedule_admin_observability(database: Database, week_start: str | None = None, *, include_student_projection: bool = True, effective_week_id: str | None = None) -> dict[str, Any]:
    """Return a read-only, human-oriented view of persisted canonical schedule state."""
    effective_weeks, week_publications = _admin_week_history(database)
    version = canonical_version(database)
    if not version:
        return {"status": "canonical_not_imported", "weeks": [], "effective_weeks": effective_weeks,
                "week_publications": week_publications, "blocks": []}
    version_id = str(version["version_id"])
    available_weeks = _single_fetchall(
        database,
        "SELECT week_start FROM canonical_effective_weeks WHERE version_id=? ORDER BY week_start DESC",
        (version_id,),
    )
    requested_week = week_start or _week_start(date.today().isoformat())
    available_values = [str(row["week_start"]) for row in available_weeks]
    selected_week = requested_week if requested_week in available_values else (available_values[0] if available_values else requested_week)
    effective = _effective_week(database, version_id, selected_week) or _effective_week(database, None, selected_week)
    if effective_week_id:
        effective = _single_fetchone(database, """SELECT ew.*,cv.status AS canonical_status,
            cv.source_snapshot_id,cv.source_fingerprint FROM canonical_effective_weeks ew
            JOIN canonical_schedule_versions cv ON cv.version_id=ew.version_id WHERE ew.effective_week_id=?""", (effective_week_id,))
        if not effective:
            raise ValueError("Effective week version not found")
        effective = dict(effective)
        selected_week = str(effective["week_start"])
        version_id = str(effective["version_id"])
        version = canonical_version(database, version_id)
    canonical = read_canonical_template(database, version_id) or {}
    canonical_blocks = canonical.get("blocks") if isinstance(canonical.get("blocks"), Mapping) else {}
    if effective:
        effective_blocks = _block_rows(database, effective["effective_week_id"])
    else:
        # The canonical template is authoritative independently of whether a
        # weekly overlay has been materialized. With no overlay, the week
        # inherits the canonical baseline without creating a database record.
        effective = {"effective_week_id": None, "version_id": version_id, "overlay_source_snapshot_id": None}
        effective_blocks = [
            {
                "block_key": str(key), "canonical_block_id": block.get("id"),
                "weekday": block.get("weekday"), "start_time": block.get("start_time"),
                "end_time": block.get("end_time"), "grade_scope": block.get("grade_scope"),
                "mode": block.get("mode"), "change_kind": "unchanged", "patch": {},
                "source_provenance": block.get("source_provenance") or {}, "unresolved": [],
            }
            for key, block in canonical_blocks.items()
        ]
    group_rows = _single_fetchall(database, "SELECT id,name,display_name,role FROM groups WHERE canonical IS TRUE ORDER BY id")
    group_names = {str(row["id"]): str(row["display_name"] or row["name"]) for row in group_rows}
    teacher_rows = _single_fetchall(database, "SELECT id,display_name FROM identities WHERE kind='teacher'")
    teacher_names = {str(row["id"]): str(row["display_name"] or row["id"]) for row in teacher_rows}
    memberships = _membership_sets(database, selected_week)
    active_students = _active_students(database) if include_student_projection else {}
    projection_context = build_student_projection_context(database, selected_week, version_id=version_id) if include_student_projection else None

    def assignment_group_ids(assignment: Mapping[str, Any]) -> list[str]:
        audience = assignment.get("audience") if isinstance(assignment.get("audience"), Mapping) else {}
        return [str(value) for value in audience.get("canonical_group_ids") or assignment.get("canonical_group_ids") or []]

    def assignment_role(assignment: Mapping[str, Any]) -> str:
        return str(assignment.get("assignment_kind") or assignment.get("role") or assignment.get("kind") or "primary")

    def assignment_view(assignment: Mapping[str, Any]) -> dict[str, Any]:
        metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
        group_ids = assignment_group_ids(assignment)
        teacher_ids = [str(value) for value in assignment.get("teacher_ids") or []]
        audience_kind = str(assignment.get("audience_kind") or (assignment.get("audience") or {}).get("type") or "canonical_groups")
        affected = set()
        for group_id in group_ids:
            affected.update(memberships.get(group_id, set()))
        return {
            "activity": str(assignment.get("activity") or ""),
            "role": assignment_role(assignment),
            "audience_kind": audience_kind,
            "canonical_group_ids": group_ids,
            "audience_rule": metadata.get("audience_rule") or {},
            "groups": [group_names.get(group_id, group_id) for group_id in group_ids],
            "teacher_ids": teacher_ids,
            "teachers": [teacher_names.get(teacher_id, teacher_id) for teacher_id in teacher_ids],
            "room": display_room(metadata.get("room")),
            "note": metadata.get("note") or "",
            "source_cells": assignment.get("source_cells") or [],
            "student_count": len(affected) if group_ids else None,
            "teacher_issue": metadata.get("teacher_issue") or ("teacher_id=null" if not teacher_ids and str(assignment.get("activity") or "") not in {_NO_LESSON, *_OPTIONAL_TEACHER_ACTIVITIES} else ""),
        }

    def source_coordinates(provenance: Any) -> list[str]:
        if not isinstance(provenance, Mapping):
            return []
        result = []
        for item in provenance.get("source_cells") or []:
            if isinstance(item, Mapping):
                coordinate = item.get("coordinate") or item.get("cell")
            else:
                coordinate = item
            if coordinate:
                result.append(str(coordinate))
        return result

    block_rollups: dict[str, dict[str, Any]] = defaultdict(lambda: {"students": 0, "states": defaultdict(int), "sample_students": [], "issues": []})
    projection_states: defaultdict[str, int] = defaultdict(int)
    projection_samples: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    projection_issues: list[dict[str, Any]] = []
    block_by_key = {str(block.get("block_key")): block for block in effective_blocks}
    for student_id, student in active_students.items():
        projection = project_student(database, student_id, selected_week, context=projection_context)
        for item in projection.get("items") or []:
            state = str(item.get("state") or "UNRESOLVED")
            projection_states[state] += 1
            if len(projection_samples[state]) < 80:
                projection_samples[state].append({"student_id": student_id, "student_name": student.get("display_name"), "class_name": student.get("class_name"), "date": item.get("date"), "start_time": item.get("start_time"), "block_key": item.get("block_key"), "activity": item.get("activity"), "reason": item.get("reason")})
            key = str(item.get("block_key") or "")
            rollup = block_rollups[key]
            rollup["students"] += 1
            rollup["states"][state] += 1
            if len(rollup["sample_students"]) < 8:
                rollup["sample_students"].append({"id": student_id, "name": student.get("display_name"), "class_name": student.get("class_name"), "state": state})
            if state == "UNRESOLVED":
                rollup["issues"].append({"student_id": student_id, "student_name": student.get("display_name"), "reason": item.get("reason")})
        for issue in projection.get("issues") or []:
            block = block_by_key.get(str(issue.get("block_key") or ""), {})
            projection_issues.append({**dict(issue), "student_id": student_id, "student_name": student.get("display_name"), "class_name": student.get("class_name"), "date": issue.get("date") or ((date.fromisoformat(selected_week) + timedelta(days=int(block.get("weekday") or 0))).isoformat() if block.get("weekday") is not None else None), "start_time": issue.get("start_time") or block.get("start_time"), "block_key": issue.get("block_key"), "reason": issue.get("reason")})

    teacher_unresolved: list[dict[str, Any]] = []
    teacher_orphans: list[dict[str, Any]] = []
    teacher_resolved = 0
    occupied: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for block in effective_blocks:
        patch = block.get("patch") if isinstance(block.get("patch"), Mapping) else {}
        assignments = _assignments(database, block.get("canonical_block_id"), patch)
        for assignment in assignments:
            activity = str(assignment.get("activity") or "")
            if activity == _NO_LESSON:
                continue
            role = assignment_role(assignment)
            teacher_ids = [str(value) for value in assignment.get("teacher_ids") or []]
            if not teacher_ids:
                if activity in _OPTIONAL_TEACHER_ACTIVITIES:
                    continue
                issue = {"block_key": block.get("block_key"), "activity": activity, "source_cells": assignment.get("source_cells") or [], "reason": (assignment.get("metadata") or {}).get("teacher_issue") if isinstance(assignment.get("metadata"), Mapping) else "teacher_id=null"}
                issue["reason"] = issue["reason"] or "teacher_id=null"
                teacher_unresolved.append(issue)
                if role not in {"special", "residual", "default"}:
                    teacher_orphans.append(issue)
            else:
                teacher_resolved += 1
                day = str(block.get("weekday") or "")
                start_time = str(block.get("start_time") or "")
                for teacher_id in teacher_ids:
                    occupied[(day, start_time, teacher_id)].append({"block_key": block.get("block_key"), "activity": activity})
                    if teacher_id not in teacher_names:
                        teacher_unresolved.append({"block_key": block.get("block_key"), "activity": activity, "source_cells": assignment.get("source_cells") or [], "reason": "teacher identity not found", "teacher_id": teacher_id})
    teacher_conflicts = [{"weekday": key[0], "start_time": key[1], "teacher_id": key[2], "assignments": value} for key, value in occupied.items() if len(value) > 1]

    snapshot = None
    if effective.get("overlay_source_snapshot_id"):
        row = _single_fetchone(database, "SELECT id,fingerprint,effective_from,effective_until,raw_payload,structural_payload FROM school_source_snapshots WHERE id=?", (effective["overlay_source_snapshot_id"],))
        if row:
            raw_payload = _decode(database, row["raw_payload"], {})
            structural_payload = _decode(database, row["structural_payload"], {})
            snapshot = {"id": str(row["id"]), "fingerprint": row["fingerprint"], "effective_from": str(row["effective_from"] or ""), "effective_until": str(row["effective_until"] or ""), "sheet_title": raw_payload.get("sheet_title"), "range": raw_payload.get("range"), "raw_rows": len(raw_payload.get("values") or []), "structured_cells": len(structural_payload.get("structured_cells") or [])}

    rendered_blocks = []
    diff_counts: defaultdict[str, int] = defaultdict(int)
    for block in effective_blocks:
        patch = block.get("patch") if isinstance(block.get("patch"), Mapping) else {}
        raw_classification = str(patch.get("change_classification") or "")
        change_kind = str(block.get("change_kind") or "unchanged")
        classification = raw_classification or {"unchanged": "UNCHANGED", "cancelled": "CANCELLED", "replaced": "REPLACED", "metadata": "METADATA_ONLY", "moved": "MOVED", "added": "ADDED", "weekly_only": "ADDED"}.get(change_kind, change_kind.upper())
        diff_counts[classification] += 1
        baseline = canonical_blocks.get(str(block.get("block_key"))) if isinstance(canonical_blocks, Mapping) else None
        baseline_assignments = (baseline or {}).get("assignments") or []
        effective_assignments = _assignments(database, block.get("canonical_block_id"), patch)
        rollup = block_rollups.get(str(block.get("block_key")), {"students": 0, "states": {}, "sample_students": [], "issues": []})
        rendered_blocks.append({
            "block_key": block.get("block_key"), "canonical_block_id": block.get("canonical_block_id"),
            "weekday": block.get("weekday"), "date": (date.fromisoformat(selected_week) + timedelta(days=int(block.get("weekday") or 0))).isoformat() if block.get("weekday") is not None else None,
            "start_time": block.get("start_time"), "end_time": block.get("end_time"), "grade_scope": block.get("grade_scope"), "routing_dimension": block.get("mode"),
            "baseline_weekday": (baseline or {}).get("weekday"),
            "baseline_start_time": (baseline or {}).get("start_time"),
            "baseline_end_time": (baseline or {}).get("end_time"),
            "baseline_grade_scope": (baseline or {}).get("grade_scope"),
            "change_kind": change_kind, "change_classification": classification, "source_cells": source_coordinates(block.get("source_provenance")),
            "source_provenance": block.get("source_provenance") or {}, "baseline_assignments": [assignment_view(item) for item in baseline_assignments],
            "weekly_change": patch if change_kind != "unchanged" else None, "effective_assignments": [assignment_view(item) for item in effective_assignments],
            "affected_students": {"count": rollup.get("students", 0), "states": dict(rollup.get("states", {})), "sample": rollup.get("sample_students", [])},
            "issues": list(block.get("unresolved") or []) + [{"reason": issue["reason"], "activity": issue["activity"], "source_cells": issue["source_cells"]} for issue in teacher_unresolved if issue.get("block_key") == block.get("block_key")],
        })

    rendered_keys = {str(block["block_key"]) for block in rendered_blocks}
    for key, baseline in canonical_blocks.items():
        if str(key) in rendered_keys:
            continue
        slot = baseline.get("slot") or {}
        rendered_blocks.append({
            "block_key": key, "canonical_block_id": None, "weekday": baseline.get("weekday"),
            "date": None, "start_time": slot.get("start"), "end_time": slot.get("end"),
            "grade_scope": baseline.get("grade_scope"), "baseline_weekday": baseline.get("weekday"),
            "baseline_start_time": slot.get("start"), "baseline_end_time": slot.get("end"),
            "baseline_grade_scope": baseline.get("grade_scope"), "change_kind": "template_only",
            "change_classification": "TEMPLATE_ONLY", "source_cells": [], "source_provenance": {},
            "baseline_assignments": [assignment_view(item) for item in baseline.get("assignments") or []],
            "effective_assignments": [], "affected_students": {"count": 0, "states": {}, "sample": []}, "issues": [],
        })

    effective_assignment_count = sum(len(_assignments(database, block.get("canonical_block_id"), block.get("patch") if isinstance(block.get("patch"), Mapping) else {})) for block in effective_blocks)
    return {
        "status": version.get("status") if effective.get("effective_week_id") else "canonical_not_materialized", "selected_week": selected_week,
        "weeks": [{"week_start": value, "current": value == selected_week, "historical": value != selected_week} for value in available_values],
        "effective_weeks": effective_weeks, "week_publications": week_publications,
        "canonical": {"version_id": version_id, "status": version.get("status"), "authoritative": bool((version.get("metadata") or {}).get("authoritative", False)), "source_snapshot_id": version.get("source_snapshot_id"), "source_fingerprint": version.get("source_fingerprint"), "blocks": len(canonical_blocks), "assignments": sum(len(item.get("assignments") or []) for item in canonical_blocks.values())},
        "effective_week": {"effective_week_id": effective.get("effective_week_id"), "version_id": effective.get("version_id"), "week_start": selected_week, "source_snapshot": snapshot, "overlay_patch_count": sum(value for key, value in diff_counts.items() if key != "UNCHANGED"), "effective_blocks": len(rendered_blocks), "diff_counts": dict(diff_counts)},
        "student_projection": {"active_students": len(active_students), "states": dict(projection_states), "conflicts": sum(1 for item in projection_issues if item.get("code") in {"INCOMPATIBLE_MEMBERSHIP_OVERLAP", "MULTIPLE_ACTIVITY_CLAIMS"}), "unresolved": len(projection_issues), "unresolved_by_reason": dict(Counter(str(item.get("reason") or "") for item in projection_issues)), "issues": projection_issues, "samples": dict(projection_samples), "skipped": not include_student_projection},
        "teacher_projection": {"resolved": teacher_resolved, "unresolved": len(teacher_unresolved), "orphan": len(teacher_orphans), "conflicts": len(teacher_conflicts), "unresolved_items": teacher_unresolved, "orphan_items": teacher_orphans, "conflict_items": teacher_conflicts},
        "blocks": rendered_blocks,
    }


def _projection_issue(
    code: str,
    reason: str,
    *,
    student: Mapping[str, Any],
    block: Mapping[str, Any],
    day: str,
    trace: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "code": code,
        "reason": reason,
        "student_id": str(student.get("id")),
        "student_name": student.get("display_name"),
        "class_name": student.get("class_name"),
        "date": day,
        "start_time": block.get("start_time"),
        "end_time": block.get("end_time"),
        "block_key": block.get("block_key"),
        "canonical_block_id": block.get("canonical_block_id"),
        "source_cells": trace.get("source_cells") or [],
        "assignment_ids": trace.get("assignment_ids") or [],
        "group_ids": trace.get("group_ids") or [],
        "group_names": trace.get("group_names") or [],
        "membership_ids": trace.get("membership_ids") or [],
        "dimension": trace.get("dimension"),
        "dimension_evidence": trace.get("dimension_evidence") or [],
        "final_state": trace.get("final_state"),
        "trace": dict(trace),
    }


def _claim_priority(claim: Mapping[str, Any]) -> tuple[int, int]:
    metadata = claim.get("assignment", {}).get("metadata") if isinstance(claim.get("assignment"), Mapping) else {}
    if isinstance(metadata, Mapping) and metadata.get("activity_type") == "event":
        return (100, 100)  # A confirmed event takes display priority for its audience.
    role = str(claim.get("role") or "primary")
    role_priority = {"primary": 40, "secondary": 30, "special": 20}.get(role, 10)
    group_roles = set(claim.get("group_roles") or [])
    specificity = 30 if "instructional_partition" in group_roles else 20 if "elective_or_special" in group_roles else 0
    return role_priority, specificity


def _is_club_assignment(assignment: Mapping[str, Any]) -> bool:
    metadata = _assignment_metadata(assignment.get("metadata") or {})
    details = metadata.get("constructor_details")
    return (metadata.get("activity_type") == "club"
            or isinstance(details, Mapping) and details.get("activityType") == "club")


def _other_assignment_occupies_slot(
    context: StudentProjectionContext, student: Mapping[str, Any], membership_groups: set[str],
    assignment: Mapping[str, Any], grade_scope: str,
) -> bool:
    if str(assignment.get("activity") or "") == _NO_LESSON or _is_club_assignment(assignment):
        return False
    rule = _assignment_audience_rule(assignment)
    kind = str(rule.get("kind") or assignment.get("audience_kind") or "groups")
    student_id = str(student["id"])
    if student_id in {str(value) for value in rule.get("exclude_student_ids") or []}:
        return False
    if student_id in {str(value) for value in rule.get("include_student_ids") or []}:
        return True
    grade = str(rule.get("grade") or grade_scope or "")
    class_id = str(rule.get("class_group_id") or "")
    class_ids = _projection_class_context(context, grade_scope if "," not in grade_scope else "", class_id)
    in_class = (not class_id and not class_ids) or bool(membership_groups.intersection(class_ids))
    in_grade = _projection_grade(context, student) in grade.split(",")
    if kind == "whole_grade":
        return in_grade and in_class
    if kind == "students":
        return False
    if kind == "available_slot":
        return False
    if kind == "remaining":
        partition_ids = {str(value) for value in rule.get("partition_group_ids") or []}
        dimension = str(rule.get("partition_dimension") or "")
        if dimension:
            partition_ids.update(group_id for group_id in context.groups if _projection_group_dimension(context, group_id)[0] == dimension)
        return (not grade or in_grade) and in_class and not bool(membership_groups.intersection(partition_ids))
    return in_class and bool(membership_groups.intersection(_assignment_group_ids(assignment)))


def _overlapping_slot_blocks(context: StudentProjectionContext, block: Mapping[str, Any],
                             exact_intervals: bool) -> list[dict[str, Any]]:
    key = (block.get("weekday"), str(block.get("start_time")), str(block.get("end_time")), exact_intervals)
    if key not in context.overlap_blocks_cache:
        bell_starts = (540, 595, 655, 710, 765, 820, 875, 930, 985)

        def bell(minutes: int) -> int:
            return min(bell_starts, key=lambda start: abs(minutes - start))

        start, end = _clock_minutes(block.get("start_time")), _clock_minutes(block.get("end_time"))
        matches = []
        for other in context.blocks_by_weekday.get(block.get("weekday"), []):
            other_start = _clock_minutes(other.get("start_time"))
            other_end = _clock_minutes(other.get("end_time"))
            if exact_intervals:
                if None not in (start, end, other_start, other_end) and not (start < other_end and other_start < end):
                    continue
            elif start is None or other_start is None or bell(start) != bell(other_start):
                continue
            matches.append(other)
        context.overlap_blocks_cache[key] = matches
    return context.overlap_blocks_cache[key]


def _student_busy_in_slot(
    database: Database, context: StudentProjectionContext, student: Mapping[str, Any],
    membership_groups: set[str], block: Mapping[str, Any], assignment_index: int,
    *, exact_intervals: bool = False, include_available_slot: bool = False,
) -> bool:
    for other_block in _overlapping_slot_blocks(context, block, exact_intervals):
        block_key = other_block["block_key"]
        if block_key not in context.slot_assignments_cache:
            patch = other_block.get("patch") if isinstance(other_block.get("patch"), Mapping) else {}
            context.slot_assignments_cache[block_key] = _assignments(database, other_block.get("canonical_block_id"), patch)
        for other_index, other_assignment in enumerate(context.slot_assignments_cache[block_key]):
            if other_block.get("block_key") == block.get("block_key") and other_index == assignment_index:
                continue
            other_metadata = _assignment_metadata(other_assignment.get("metadata") or {})
            if other_metadata.get("audience_coverage") == "explicit" and other_metadata.get("special_mark") == "lunch":
                continue  # A fallback lunch marker never consumes an available-slot audience.
            other_rule = _assignment_audience_rule(other_assignment)
            if include_available_slot and other_rule.get("kind") == "available_slot":
                class_id = str(other_rule.get("class_group_id") or "")
                grade_scope = str(other_block.get("grade_scope") or "")
                grade = str(other_rule.get("grade") or grade_scope)
                class_ids = _projection_class_context(context, grade_scope, class_id)
                in_class = (not class_id and not class_ids) or bool(membership_groups.intersection(class_ids))
                included = str(student["id"]) in {str(i) for i in other_rule.get("include_student_ids") or []}
                if ((included or _projection_grade(context, student) == grade and in_class)
                        and str(student["id"]) not in {str(i) for i in other_rule.get("exclude_student_ids") or []}
                        and not _student_busy_in_slot(database, context, student, membership_groups, other_block, other_index, exact_intervals=exact_intervals)):
                    return True
            if _assignment_metadata(other_assignment.get("metadata") or {}).get("activity_type") == "consultation":
                continue
            if _other_assignment_occupies_slot(context, student, membership_groups, other_assignment, str(other_block.get("grade_scope") or "")):
                return True
    return False


def _activity_choices(claims):
    """Collapse spelling aliases only when they refer to the same assignment."""
    raw = {claim["activity"] for claim in claims}
    if len(raw) <= 1 and len({program_key(claim["assignment"]) for claim in claims}) <= 1:
        return raw
    return {(subject_label(claim["activity"]).casefold(), tuple(sorted(claim["group_ids"])),
             tuple(sorted(map(str, claim["assignment"].get("teacher_ids") or []))),
             claim.get("activity_type"), program_key(claim["assignment"])) for claim in claims}


def _project_student_block(
    database: Database,
    context: StudentProjectionContext,
    student: Mapping[str, Any],
    block: Mapping[str, Any],
    day: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    student_id = str(student["id"])
    student_grade = _projection_grade(context, student)
    membership_groups = _student_memberships(context, student_id, day)
    memberships_by_group: defaultdict[str, list[str]] = defaultdict(list)
    for membership in context.membership_rows_by_student.get(student_id, []):
        group_id = str(membership.get("group_id"))
        if group_id in membership_groups and _active_student_membership_on(membership, day):
            memberships_by_group[group_id].append(str(membership.get("id")))

    patch = block.get("patch") if isinstance(block.get("patch"), Mapping) else {}
    assignments = [] if block.get("change_kind") == "cancelled" else _assignments(database, block.get("canonical_block_id"), patch)
    claims: list[dict[str, Any]] = []
    residual_claims: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    assignment_traces: list[dict[str, Any]] = []

    for index, assignment in enumerate(assignments):
        if _is_club_assignment(assignment):
            continue
        assignment_id = _assignment_id(block, assignment, index)
        activity = str(assignment.get("activity") or "").strip()
        role = _assignment_role(assignment)
        group_ids = _assignment_group_ids(assignment)
        audience_rule = _assignment_audience_rule(assignment)
        audience_kind = str(audience_rule.get("kind") or assignment.get("audience_kind") or "groups")
        scope = str(block.get("grade_scope") or "")
        class_group_id = str(audience_rule.get("class_group_id") or "")
        class_ids = _projection_class_context(context, scope if "," not in scope else "", class_group_id)
        in_class_context = (not class_group_id and not class_ids) or bool(membership_groups.intersection(class_ids))
        include_student_ids = {str(value) for value in audience_rule.get("include_student_ids") or []}
        exclude_student_ids = {str(value) for value in audience_rule.get("exclude_student_ids") or []}
        if student_id in exclude_student_ids:
            continue
        source_cells = assignment.get("source_cells") or []
        unknown_group_ids = [group_id for group_id in group_ids if group_id not in context.groups or not bool(context.groups[group_id].get("canonical"))]
        matched_group_ids = sorted(set(group_ids).intersection(membership_groups))
        explicit_match = student_id in include_student_ids
        if audience_kind == "whole_grade":
            grade = str(audience_rule.get("grade") or block.get("grade_scope") or "")
            explicit_match = explicit_match or (bool(grade) and in_class_context and student_grade == grade)
        elif audience_kind == "students":
            explicit_match = student_id in include_student_ids
        elif audience_kind == "remaining":
            grade = str(audience_rule.get("grade") or block.get("grade_scope") or "")
            partition_ids = {str(value) for value in audience_rule.get("partition_group_ids") or []}
            dimension = str(audience_rule.get("partition_dimension") or "")
            if dimension:
                partition_ids.update(group_id for group_id in context.groups if _projection_group_dimension(context, group_id)[0] == dimension)
            explicit_match = explicit_match or ((not grade or student_grade == grade)
                and in_class_context and not membership_groups.intersection(partition_ids))
        elif audience_kind == "available_slot":
            grade = str(audience_rule.get("grade") or block.get("grade_scope") or "")
            explicit_match = (explicit_match or bool(grade and student_grade == grade) and in_class_context) and not _student_busy_in_slot(
                database, context, student, membership_groups, block, index,
                exact_intervals=(assignment.get("metadata") or {}).get("audience_coverage") == "explicit")
        if not in_class_context and audience_kind not in {"students"}:
            matched_group_ids = []
            if student_id not in include_student_ids:
                explicit_match = False
        metadata = _assignment_metadata(assignment.get("metadata") or {})
        if (metadata.get("audience_coverage") == "explicit" and metadata.get("special_mark") == "lunch"
                and (matched_group_ids or explicit_match)
                and _student_busy_in_slot(database, context, student, membership_groups, block, index, exact_intervals=True, include_available_slot=True)):
            matched_group_ids = []
            explicit_match = False
        dimensions = []
        dimension_evidence = []
        group_roles = []
        for group_id in matched_group_ids:
            group = context.groups[group_id]
            dimension, evidence = _projection_group_dimension(context, group_id)
            dimensions.append(dimension)
            dimension_evidence.append({"group_id": group_id, "dimension": dimension, "source": evidence})
            group_role = str(group.get("role") or "")
            if not group_role:
                group_role = "base_class" if group.get("group_type") == "class" else "instructional_partition" if group.get("group_type") in {"instructional", "instructional_group", "subject_group"} else "unknown"
            group_roles.append(group_role)
        trace = {
            "assignment_id": assignment_id,
            "activity": activity,
            "note": (assignment.get("metadata") or {}).get("note") if isinstance(assignment.get("metadata"), Mapping) else "",
            "role": role,
            "audience_group_ids": group_ids,
            "matched_group_ids": matched_group_ids,
            "matched_membership_ids": sorted({value for group_id in matched_group_ids for value in memberships_by_group[group_id]}),
            "dimensions": dimensions,
            "dimension_evidence": dimension_evidence,
            "source_cells": source_cells,
            "audience_rule": audience_rule,
            "matched": bool(matched_group_ids or explicit_match),
        }
        assignment_traces.append(trace)
        common = {
            "assignment_id": assignment_id,
            "activity": activity,
            "role": role,
            "group_ids": matched_group_ids,
            "group_roles": group_roles,
            "dimensions": dimensions,
            "dimension_evidence": dimension_evidence,
            "membership_ids": trace["matched_membership_ids"],
            "source_cells": source_cells,
            "assignment": assignment,
            "activity_type": (assignment.get("metadata") or {}).get("activity_type", "lesson"),
        }
        if unknown_group_ids:
            issue_trace = {
                "assignment_ids": [assignment_id], "group_ids": unknown_group_ids,
                "group_names": [str(context.groups.get(value, {}).get("display_name") or context.groups.get(value, {}).get("name") or value) for value in unknown_group_ids],
                "source_cells": source_cells, "final_state": "UNRESOLVED", "assignments": assignment_traces,
            }
            issues.append(_projection_issue("UNKNOWN_CANONICAL_GROUP", f"canonical group not found or inactive: {', '.join(unknown_group_ids)}", student=student, block=block, day=day, trace=issue_trace))
            continue
        is_residual = role in _RESIDUAL_ROLES or str(assignment.get("audience_kind") or "") in {"complement", "remaining"} or audience_kind in {"remaining", "available_slot"}
        if explicit_match and audience_kind not in {"remaining", "available_slot"}:
            claims.append(common)
            continue
        if explicit_match and audience_kind in {"remaining", "available_slot"}:
            residual_claims.append(common)
            continue
        if audience_kind in {"remaining", "available_slot", "whole_grade", "students"}:
            continue
        if not group_ids:
            if is_residual:
                residual_claims.append(common)
            else:
                issue_trace = {"assignment_ids": [assignment_id], "source_cells": source_cells, "final_state": "UNRESOLVED", "assignments": assignment_traces}
                issues.append(_projection_issue("GROUPLESS_PRIMARY_ASSIGNMENT", "non-residual assignment has no canonical audience", student=student, block=block, day=day, trace=issue_trace))
            continue
        if matched_group_ids:
            claims.append(common)

    partition_groups: defaultdict[str, set[str]] = defaultdict(set)
    partition_evidence: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in assignments:
        if (_is_club_assignment(assignment)
                or _assignment_metadata(assignment.get("metadata") or {}).get("audience_coverage") == "explicit"):
            # Selecting canonical groups is not a declaration that those groups
            # partition every pupil in the block's parallel. Historical artifacts
            # retain their legacy coverage contract unless explicitly reauthored.
            continue
        for group_id in _assignment_group_ids(assignment):
            group = context.groups.get(group_id)
            if not group or not bool(group.get("canonical")):
                continue
            role = str(group.get("role") or "")
            group_type = str(group.get("group_type") or "")
            if role not in {"base_class", "instructional_partition"} and group_type not in {"class", "instructional", "instructional_group", "subject_group"}:
                continue
            dimension, evidence = _projection_group_dimension(context, group_id)
            if dimension != "unknown":
                partition_groups[dimension].add(group_id)
                partition_evidence[dimension].append({"group_id": group_id, "source": evidence})
    uncovered_dimensions = [
        dimension for dimension, group_ids in partition_groups.items()
        if len(group_ids) >= 2 and not membership_groups.intersection(group_ids)
    ]
    for dimension in uncovered_dimensions:
        group_ids = sorted(partition_groups[dimension])
        issue_trace = {
            "group_ids": group_ids,
            "group_names": [str(context.groups[value].get("display_name") or context.groups[value].get("name") or value) for value in group_ids],
            "dimension": dimension,
            "dimension_evidence": partition_evidence[dimension],
            "source_cells": sorted({str(cell) for item in assignments for cell in item.get("source_cells") or []}),
            "final_state": "UNRESOLVED", "assignments": assignment_traces,
        }
        issues.append(_projection_issue("UNCOVERED_PARTITION_MEMBER", f"student is not covered by active partition {dimension}", student=student, block=block, day=day, trace=issue_trace))

    dimension_groups: defaultdict[str, set[str]] = defaultdict(set)
    dimension_claims: defaultdict[str, list[str]] = defaultdict(list)
    dimension_evidence: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        for group_id, dimension, evidence in zip(claim["group_ids"], claim["dimensions"], claim["dimension_evidence"]):
            if dimension == "unknown" or dimension.endswith(":unknown"):
                continue
            dimension_groups[dimension].add(group_id)
            dimension_claims[dimension].append(claim["assignment_id"])
            dimension_evidence[dimension].append(evidence)
    for dimension, group_ids in dimension_groups.items():
        if len(group_ids) <= 1:
            continue
        issue_trace = {
            "assignment_ids": sorted(set(dimension_claims[dimension])), "group_ids": sorted(group_ids),
            "group_names": [str(context.groups[value].get("display_name") or context.groups[value].get("name") or value) for value in sorted(group_ids)],
            "membership_ids": sorted({value for group_id in group_ids for value in memberships_by_group[group_id]}),
            "dimension": dimension, "dimension_evidence": dimension_evidence[dimension],
            "source_cells": sorted({str(cell) for claim in claims for cell in claim["source_cells"]}),
            "final_state": "UNRESOLVED", "assignments": assignment_traces,
        }
        issues.append(_projection_issue("INCOMPATIBLE_MEMBERSHIP_OVERLAP", f"student belongs to multiple mutually exclusive groups in {dimension}", student=student, block=block, day=day, trace=issue_trace))

    for raw_issue in block.get("unresolved") or []:
        if student_id in {str(value) for value in raw_issue.get("student_ids") or []}:
            trace = {"final_state": "UNRESOLVED", "assignments": assignment_traces, "source_cells": raw_issue.get("source_cells") or []}
            issues.append(_projection_issue("CANONICAL_BLOCK_UNRESOLVED", str(raw_issue.get("reason") or "canonical block unresolved"), student=student, block=block, day=day, trace=trace))

    blocking = [issue for issue in issues if issue["code"] in {"UNKNOWN_CANONICAL_GROUP", "GROUPLESS_PRIMARY_ASSIGNMENT", "UNCOVERED_PARTITION_MEMBER", "INCOMPATIBLE_MEMBERSHIP_OVERLAP", "CANONICAL_BLOCK_UNRESOLVED"}]
    provenance = {"block_key": block["block_key"], "source_provenance": block.get("source_provenance") or {}}
    result: dict[str, Any] | None = None
    if blocking:
        result = {"state": "UNRESOLVED", "activity": None, "reason": blocking[0]["reason"], "issue_codes": sorted({issue["code"] for issue in blocking}), "provenance": provenance}
    else:
        activity_claims = [claim for claim in claims if claim["activity"] and claim["activity"] != _NO_LESSON and claim["role"] not in {"final_state", "final_unassigned", "explicit_no_lesson"}]
        selected: dict[str, Any] | None = None
        if activity_claims:
            best_priority = max(_claim_priority(claim) for claim in activity_claims)
            best = [claim for claim in activity_claims if _claim_priority(claim) == best_priority]
            elective_evidence = []
            for claim in best:
                metadata = _assignment_metadata(claim["assignment"].get("metadata") or {})
                details = metadata.get("constructor_details") if isinstance(metadata.get("constructor_details"), Mapping) else {}
                elective_evidence.append((metadata.get("activity_type") or details.get("activityType"),
                                          str(details.get("blockId") or ""),
                                          str(details.get("blockName") or "").strip(),
                                          str(details.get("electiveDirection") or metadata.get("electiveDirection") or "").strip().casefold()))
            if (best and all(kind == "elective" and block_id for kind, block_id, _, _ in elective_evidence)
                    and len({block_id for _, block_id, _, _ in elective_evidence}) == 1):
                # An explicitly linked elective block is one student-facing
                # class activity. The concrete lane, teacher and room remain
                # intact on the source assignments and teacher projection.
                names = {name for _, _, name, _ in elective_evidence if name}
                result = {"state": "ACTIVITY", "activity": next(iter(names), "Курс по выбору"),
                          "activity_type": "elective",
                          "provenance": {**provenance, "source_cells": sorted({cell for claim in best for cell in claim["source_cells"]})}}
                best = []
                activity_claims = []
                selected = None
            elif (best and all(kind == "elective" and not block_id for kind, block_id, _, _ in elective_evidence)):
                # Historical publications predate stable elective block ids.
                # Keep their already-confirmed canonical audience visible as a
                # neutral card; do not reveal a lane or infer which option the
                # pupil chose. The marker prompts an administrator to map the
                # archived source when it is next edited and republished.
                digital = all(direction == "digital" for _, _, _, direction in elective_evidence)
                result = {"state": "ACTIVITY", "activity": "Цифровой трек" if digital else "Курс по выбору",
                          "activity_type": "elective",
                          "provenance": {**provenance,
                              "source_cells": sorted({cell for claim in best for cell in claim["source_cells"]}),
                              "requires_administrative_block_mapping": True}}
                best = []
                activity_claims = []
                selected = None
            else:
                selected = None
            activities = _activity_choices(best)
            if result is not None:
                pass
            elif len(activities) > 1:
                if best and all(_assignment_metadata(claim["assignment"].get("metadata") or {}).get("day_plan_mode") == "sdep" for claim in best):
                    # A self-study plan can list multiple independent tasks in
                    # one time slot. Preserve every task without treating the
                    # student as double-booked or selecting one arbitrarily.
                    ordered = sorted(best, key=lambda claim: (claim["activity"], claim["assignment_id"]))
                    result = {"state": "ACTIVITY", "activity": " · ".join(claim["activity"] for claim in ordered),
                              "activity_type": "sdep_plan", "activities": [
                                  {"activity": claim["activity"], "activity_type": claim.get("activity_type") or "lesson",
                                   "provenance": {"source_cells": claim["source_cells"]}} for claim in ordered],
                              "provenance": {**provenance, "source_cells": sorted({cell for claim in ordered for cell in claim["source_cells"]})}}
                else:
                    trace = {
                    "assignment_ids": sorted(claim["assignment_id"] for claim in best),
                    "group_ids": sorted({value for claim in best for value in claim["group_ids"]}),
                    "membership_ids": sorted({value for claim in best for value in claim["membership_ids"]}),
                    "source_cells": sorted({str(cell) for claim in best for cell in claim["source_cells"]}),
                    "final_state": "UNRESOLVED", "assignments": assignment_traces,
                    }
                    issue = _projection_issue("MULTIPLE_ACTIVITY_CLAIMS", "multiple equally applicable incompatible activities", student=student, block=block, day=day, trace=trace)
                    issues.append(issue)
                    result = {"state": "UNRESOLVED", "activity": None, "reason": issue["reason"], "issue_codes": [issue["code"]], "provenance": provenance}
            else:
                selected = sorted(best, key=lambda claim: (
                    int(not bool(claim["assignment"].get("teacher_ids"))) if _assignment_metadata(claim["assignment"].get("metadata") or {}).get("audience_coverage")=="explicit" else 0,
                    claim["activity"], claim["assignment_id"]))[0]
        if selected is not None:
            assignment = selected["assignment"]
            metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
            result = {
                "state": "ACTIVITY", "activity": selected["activity"],
                "activity_type": selected.get("activity_type") or "lesson",
                "teacher_ids": assignment.get("teacher_ids") or [], "teacher_names": metadata.get("teachers") or [],
                "room": display_room(metadata.get("room")),
                "provenance": {**provenance, "source_cells": selected["source_cells"]},
            }
            if metadata.get("day_plan_mode") == "online":
                result.update(delivery_mode="online", room="", connection_info=(metadata.get("constructor_details") or {}).get("connectionInfo", ""))
            if metadata.get("audience_coverage") == "explicit" and metadata.get("joint_activity_id"):
                result["provenance"]["joint_activity_id"] = metadata["joint_activity_id"]
            if metadata.get('activity_format')=='independent_practice':
                result['activity_format']='independent_practice'
                result['responsible_teacher_ids']=metadata.get('responsible_teacher_ids') or []
                result['responsible_teachers']=metadata.get('responsible_teachers') or []
        elif result is None:
            non_no_lesson = [claim for claim in residual_claims if claim["activity"] and claim["activity"] != _NO_LESSON]
            residual_activities = _activity_choices(non_no_lesson)
            if len(residual_activities) > 1:
                trace = {"assignment_ids": sorted(claim["assignment_id"] for claim in non_no_lesson), "source_cells": sorted({str(cell) for claim in non_no_lesson for cell in claim["source_cells"]}), "final_state": "UNRESOLVED", "assignments": assignment_traces}
                issue = _projection_issue("MULTIPLE_ACTIVITY_CLAIMS", "multiple residual activities apply", student=student, block=block, day=day, trace=trace)
                issues.append(issue)
                result = {"state": "UNRESOLVED", "activity": None, "reason": issue["reason"], "issue_codes": [issue["code"]], "provenance": provenance}
            elif non_no_lesson:
                selected = sorted(non_no_lesson, key=lambda claim: (claim["activity"], claim["assignment_id"]))[0]
                assignment = selected["assignment"]
                metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
                result = {"state": "ACTIVITY", "activity": selected["activity"], "activity_type": selected.get("activity_type") or "lesson", "teacher_ids": assignment.get("teacher_ids") or [], "teacher_names": metadata.get("teachers") or [], "room": display_room(metadata.get("room")), "provenance": {**provenance, "source_cells": selected["source_cells"]}}
            else:
                note = next((str(trace.get("note") or "") for trace in assignment_traces if trace["matched"] and trace["activity"] == _NO_LESSON and trace.get("note")), "")
                result = {"state": "NO_LESSON", "activity": _NO_LESSON, "reason": "no applicable canonical activity", "note": note, "provenance": provenance}
    assert result is not None
    if result["state"] == "UNRESOLVED" and set(result.get("issue_codes") or []) & {
            "MULTIPLE_ACTIVITY_CLAIMS", "INCOMPATIBLE_MEMBERSHIP_OVERLAP"}:
        personal = [claim for claim in claims if claim["activity"] != _NO_LESSON and claim["role"] == "primary"]
        if len(personal) > 1:
            result["activities"] = [{"activity": c["activity"], "activity_type": c["activity_type"]} for c in personal]
    result["trace"] = {"memberships": sorted(membership_groups), "assignments": assignment_traces, "final_state": result["state"], "selected_activity": result.get("activity")}
    if result["state"] == "NO_LESSON" and any(trace["matched"] and trace["activity"] != _NO_LESSON for trace in assignment_traces):
        trace = {"source_cells": sorted({str(cell) for item in assignment_traces for cell in item["source_cells"]}), "final_state": "NO_LESSON", "assignments": assignment_traces}
        issue = _projection_issue("UNEXPECTED_NO_LESSON", "applicable activity claim ended as NO_LESSON", student=student, block=block, day=day, trace=trace)
        issues.append(issue)
        result = {**result, "state": "UNRESOLVED", "activity": None, "reason": issue["reason"], "issue_codes": [issue["code"]]}
    return result, issues


def _published_unresolved_items(source: Mapping[str, Any] | None, publication_id: Any,
                                identity_id: str, week_start: str, kind: str) -> list[dict[str, Any]]:
    """Own-identity conflict markers from the exact immutable publication only."""
    records=(source or {}).get('records') or []
    grouped={}
    lessons = {lesson.get("id"): lesson for lesson in ((source or {}).get("payload") or {}).get("lessons", [])}
    for record in records:
        if kind == 'student':
            owns_marker = identity_id in {str(i) for i in record.get('conflict_student_ids') or []}
            owns_marker = owns_marker or (record.get('status') == 'unresolved' and identity_id in {str(i) for i in record.get('known_student_ids') or []})
        else:
            owns_marker = record.get('status') == 'unresolved' and identity_id in {str(i) for i in record.get('known_teacher_ids') or []}
        if not owns_marker:continue
        if record.get('day') not in range(7) or not record.get('start') or not record.get('end'):continue
        key=(record['day'],record['start'],record['end'])
        entry=grouped.setdefault(key,{'date':(date.fromisoformat(week_start)+timedelta(days=key[0])).isoformat(),'weekday':key[0],'start_time':key[1],'end_time':key[2], 'state':'UNRESOLVED','activity':None,'reason':'Занятие ожидает уточнения администратора','issue_codes':[],'provenance':{'publication_id':publication_id,'unresolved_record_ids':[]}})
        if kind == "student" and identity_id in {str(i) for i in record.get("conflict_student_ids") or []}:
            lesson = lessons.get(record["lesson_id"]) or {}
            if lesson.get("subject") and identity_id in {str(i) for i in record.get("known_student_ids") or []}:
                entry.setdefault("activities", []).append({"activity": lesson["subject"], "activity_type": lesson.get("activityType", "lesson")})
        entry['issue_codes']=sorted(set(entry['issue_codes'])|set(record.get('diagnostic_codes') or []))
        entry['provenance']['unresolved_record_ids'].append(record['lesson_id'])
    return list(grouped.values())


def _published_source_for_effective(database: Database, effective: Mapping[str, Any]) -> tuple[dict[str, Any] | None, Any]:
    """Read the integrity-checked authored source paired with the selected runtime version."""
    with database.connection() as connection:
        query=("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='schedule_constructor_publications'" if database.database_url else "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_constructor_publications'")
        if not database.execute(connection,query).fetchone():
            return None, None
        row=database.execute(connection,"SELECT id,metadata,payload_fingerprint FROM schedule_constructor_publications WHERE published_id=? ORDER BY created_at DESC LIMIT 1",(effective.get('effective_week_id'),)).fetchone()
        # The template archive applies only to a default week with no changes.
        if not row:
            patched=database.execute(connection,"SELECT 1 FROM canonical_effective_blocks WHERE effective_week_id=? AND change_kind NOT IN ('unchanged','base') LIMIT 1",(effective.get('effective_week_id'),)).fetchone()
            if not patched:
                row=database.execute(connection,"SELECT id,metadata,payload_fingerprint FROM schedule_constructor_publications WHERE published_id=? AND scope_kind='template' ORDER BY created_at DESC LIMIT 1",(effective.get('version_id'),)).fetchone()
    if not row:
        return None, None
    from backend.services.constructor_publication import _checked_published_source
    try:
        metadata=row['metadata'] if isinstance(row['metadata'],Mapping) else json.loads(row['metadata'])
        if not isinstance(metadata, Mapping):
            return None, None
        return _checked_published_source(metadata,row['payload_fingerprint']), row["id"]
    except (TypeError, ValueError):
        return None, None


def _student_day_modes(context: StudentProjectionContext, student: Mapping[str, Any], source: Mapping[str, Any] | None, week_start: str) -> list[dict[str, Any]]:
    payload=(source or {}).get('payload') or {}
    class_ids={str(value) for value in payload.get('classes') or []}
    if not class_ids:
        return []
    student_id=str(student.get('id') or '')
    class_grades={class_id:canonical_group_grade(context.groups.get(class_id) or {}) for class_id in class_ids}
    grades={grade for grade in class_grades.values() if grade}
    fallback_grade=_grade(student.get('class_name'))
    start=date.fromisoformat(week_start)
    result=[]
    for weekday in range(7):
        day=(start+timedelta(days=weekday)).isoformat()
        memberships=_student_memberships(context,student_id,day)
        member_grades={canonical_group_grade(context.groups.get(class_id) or {}) for class_id in class_ids & memberships}
        member_grades.discard('')
        if not member_grades and fallback_grade in grades:
            member_grades={fallback_grade}
        if len(member_grades)!=1:
            continue
        grade=next(iter(member_grades))
        mode,_=resolve_grade_day_mode(payload,grade,weekday,class_grades)
        if mode in {'online','sdep'}:
            result.append({'date':day,'mode':mode})
    return result


def _teacher_day_modes(database: Database, source: Mapping[str, Any] | None, teacher_id: str, week_start: str) -> list[dict[str, Any]]:
    payload=(source or {}).get('payload') or {}
    class_ids={str(value) for value in payload.get('classes') or []}
    if not class_ids:
        return []
    rows=[lesson for lesson in payload.get('lessons') or [] if isinstance(lesson,Mapping)]
    placeholders=','.join('?' for _ in class_ids)
    with database.connection() as connection:
        found=database.execute(connection,f"SELECT id,name,display_name,base_class_name,group_type,role FROM groups WHERE id IN ({placeholders})",tuple(sorted(class_ids))).fetchall()
    class_grades={str(row['id']):canonical_group_grade(dict(row)) for row in found}
    start=date.fromisoformat(week_start)
    result=set()
    for lesson in rows:
        teachers={str(value) for value in lesson.get('teacherIds') or []}
        if lesson.get('teacherId'):
            teachers.add(str(lesson['teacherId']))
        teachers.update(str(value) for value in lesson.get('responsibleTeacherIds') or lesson.get('responsible_teacher_ids') or [])
        if teacher_id not in teachers:
            continue
        try:
            weekday=int(lesson.get('day',lesson.get('weekday')))
        except (TypeError,ValueError):
            continue
        if weekday not in range(7):
            continue
        for class_id in lesson.get('classIds') or ([lesson.get('classId')] if lesson.get('classId') else []):
            class_id=str(class_id)
            grade=class_grades.get(class_id,'')
            if not grade:
                continue
            mode,_=resolve_grade_day_mode(payload,grade,weekday,class_grades)
            if mode in {'online','sdep'}:
                result.add(((start+timedelta(days=weekday)).isoformat(),mode,grade,f'{grade} класс'))
    return [{'date':day,'mode':mode,'class_id':class_id,'class_name':class_name}
            for day,mode,class_id,class_name in sorted(result)]


def _clock_minutes(value: Any) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::[0-5]\d(?:\.\d+)?)?", str(value or ""))
    if not match:
        return None
    hour, minute = map(int, match.groups())
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def _event_priority_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clip only the student's rendered lessons; never rewrite published blocks."""
    events = [item for item in items if item.get("state") == "ACTIVITY" and item.get("activity_type") == "event"]
    result: list[dict[str, Any]] = []
    for item in items:
        if item in events or item.get("state") != "ACTIVITY" or item.get("activity_type") in {"club", "elective", "consultation"}:
            result.append(item)
            continue
        start, end = _clock_minutes(item.get("start_time")), _clock_minutes(item.get("end_time"))
        if start is None or end is None:
            result.append(item)
            continue
        segments = [(start, end)]
        for event in events:
            if event.get("date") != item.get("date"):
                continue
            event_start, event_end = _clock_minutes(event.get("start_time")), _clock_minutes(event.get("end_time"))
            if event_start is None or event_end is None:
                continue
            remaining = []
            for left, right in segments:
                if left >= event_end or event_start >= right:
                    remaining.append((left, right))
                else:
                    if left < event_start:
                        remaining.append((left, event_start))
                    if event_end < right:
                        remaining.append((event_end, right))
            segments = remaining
        result.extend({**item, "start_time": f"{left // 60:02d}:{left % 60:02d}",
                       "end_time": f"{right // 60:02d}:{right % 60:02d}"}
                      for left, right in segments if left < right)
    return sorted(result, key=lambda item: (str(item.get("date")), str(item.get("start_time")),
                                             0 if item.get("activity_type") == "event" else 1))


def _published_optional_activities(database: Database, effective: Mapping[str, Any], student: Mapping[str, Any],
                                   context: StudentProjectionContext, week_start: str) -> list[dict[str, Any]]:
    # Clubs are intentionally retained in the immutable admin source only.
    # Electives are already projected as a shared, non-personal canonical item.
    return []


def project_student(
    database: Database,
    identity_id: Any,
    week_start: str,
    *,
    context: StudentProjectionContext | None = None,
) -> dict[str, Any]:
    week_start = _week_start(week_start)
    publication = None if context else resolve_published_week(database, week_start)
    context = context or build_student_projection_context(database, week_start, publication=publication)
    edition = {"publication": publication.metadata} if publication else {}
    student_id = str(identity_id)
    if not context:
        return {**edition, "identity_id": student_id, "week_start": week_start, "status": publication.status if publication else "canonical_not_materialized", "items": [], "issues": []}
    effective = context.effective
    student = context.students.get(student_id)
    if not student or student.get("status") != "active":
        return {"identity_id": student_id, "week_start": week_start, "status": "student_not_found", "items": [], "issues": []}
    published_source, publication_id = ((publication.source, publication.source_publication_id) if publication
                                        else _published_source_for_effective(database, effective))
    day_modes=_student_day_modes(context,student,published_source,week_start)
    day_modes_by_date={item['date']:item['mode'] for item in day_modes}
    items, issues = [], []
    seen_joint_activities: set[tuple[str,str,str,str]] = set()
    start = date.fromisoformat(week_start)
    for block in context.blocks:
        grade = str(block.get("grade_scope") or "")
        if _grade(student.get("class_name")) not in {value for value in grade.split(",") if value}:
            continue
        if block.get("weekday") is None:
            continue
        day = (start + timedelta(days=int(block["weekday"]))).isoformat()
        state, block_issues = _project_student_block(database, context, student, block, day)
        issues.extend(block_issues)
        if state.get("activity_type") == "elective":
            # Direction-level source traces include course names and staffing;
            # those are not part of a student's shared-block projection.
            state.pop("trace", None)
        day_mode = day_modes_by_date.get(day)
        assignments = _assignments(database, block.get("canonical_block_id"), block.get("patch") or {})
        if state["state"] != "ACTIVITY" and assignments and all(
            _assignment_metadata(a.get("metadata") or {}).get("activity_type") == "consultation" for a in assignments
        ):
            continue
        sdep_plan = bool(assignments) and all(
            _assignment_metadata(assignment.get("metadata") or {}).get("day_plan_mode") == "sdep"
            for assignment in assignments
        )
        if state.get("activity_type") != "consultation" and (day_mode == "sdep" or sdep_plan):
            state = {key: value for key, value in state.items() if key not in {"teacher_ids", "teacher_names", "room"}}
        joint_id = (state.get("provenance") or {}).get("joint_activity_id")
        if state["state"] == "ACTIVITY" and joint_id:
            joint_key = (day,str(block.get("start_time")),str(block.get("end_time")),str(joint_id))
            if joint_key in seen_joint_activities:
                continue
            seen_joint_activities.add(joint_key)
        items.append({"block_key": block["block_key"], "date": day, "weekday": int(block["weekday"]), "start_time": block.get("start_time"), "end_time": block.get("end_time"), **({"day_mode":day_modes_by_date[day]} if day in day_modes_by_date else {}), **state})
    unresolved=_published_unresolved_items(published_source,publication_id,student_id,week_start,'student')
    if unresolved:
        intervals={(i['date'],i['start_time'],i['end_time']) for i in unresolved}
        items=[i for i in items if i.get("activity_type") == "consultation" or (i['date'],i['start_time'],i['end_time']) not in intervals]
        items.extend(unresolved)
    items = _event_priority_items(items)
    optional_activities = _published_optional_activities(database, effective, student, context, week_start)
    return {**edition, "identity_id": student_id, "week_start": week_start, "status": effective["canonical_status"], "source_snapshot_id": effective["source_snapshot_id"], "items": items, "issues": issues, "day_modes":day_modes, "optional_activities":optional_activities}


def _week_start(day: str) -> str:
    current = date.fromisoformat(day)
    return (current - timedelta(days=current.weekday())).isoformat()


def project_student_range(database: Database, identity_id: Any, start_day: str, end_day: str | None = None) -> dict[str, Any]:
    finish = end_day or start_day
    first, last = date.fromisoformat(start_day), date.fromisoformat(finish)
    if last < first:
        raise ValueError("end_day must not precede start_day")
    projections: dict[str, dict[str, Any]] = {}
    cursor = first
    while cursor <= last:
        week = _week_start(cursor.isoformat())
        if week not in projections:
            projections[week] = project_student(database, identity_id, week)
        cursor += timedelta(days=1)
    approved = {"approved_baseline", "approved_with_exceptions", "authoritative"}
    items = [item for projection in projections.values() if projection["status"] in approved
             for item in projection["items"] if start_day <= str(item.get("date")) <= finish]
    day_modes = [item for projection in projections.values() if projection["status"] in approved
                 for item in projection.get("day_modes", []) if start_day <= str(item.get("date")) <= finish]
    optional_activities = [item for projection in projections.values() if projection["status"] in approved
                           for item in projection.get("optional_activities", []) if start_day <= str(item.get("date")) <= finish]
    issues = [issue for projection in projections.values() if projection["status"] in approved for issue in projection["issues"]]
    statuses = [projection["status"] for projection in projections.values()]
    status = next((item for item in statuses if item not in approved), statuses[0] if statuses else "canonical_not_materialized")
    if status in {"draft", "superseded"}:
        status = "not_published"
    return {"identity_id": str(identity_id), "start_day": start_day, "end_day": finish, "status": status, "items": items, "issues": issues, "day_modes":day_modes, "optional_activities":optional_activities,
            "publications": [p["publication"] for p in projections.values() if "publication" in p]}


def student_campus_projection(database: Database, identity_id: Any, start_day: str,
                              end_day: str | None = None) -> dict[str, Any]:
    """Expose only student-facing fields of the already personal schedule."""
    result = project_student_range(database, identity_id, start_day, end_day)
    display_fields = ("date", "weekday", "start_time", "end_time", "state", "activity", "activity_type",
                      "day_mode", "delivery_mode", "connection_info", "teacher_names", "room", "activity_format", "responsible_teachers")
    items = []
    for row in result["items"]:
        item = {key: row[key] for key in display_fields if key in row}
        if row.get("activities"):
            item["activities"] = [{key: activity[key] for key in ("activity", "activity_type") if key in activity}
                                  for activity in row["activities"]]
        items.append(item)
    finish = end_day or start_day
    issues = [{key: issue[key] for key in ("code", "date", "start_time", "end_time") if key in issue}
              for issue in result["issues"] if start_day <= str(issue.get("date") or "") <= finish]
    return {**result, "items": items, "issues": issues, "optional_activities": []}


def project_teacher(database: Database, identity_id: Any, week_start: str) -> dict[str, Any]:
    week_start = _week_start(week_start)
    publication = resolve_published_week(database, week_start)
    effective = publication.effective
    teacher_id = str(identity_id)
    if not effective:
        return {"publication": publication.metadata, "identity_id": teacher_id, "week_start": week_start, "status": publication.status, "items": [], "issues": []}
    if database.autocommit:
        group_rows: list[Any] = []
        offset = 0
        while True:
            page = _single_fetchall(database, "SELECT id,name,display_name FROM groups WHERE canonical IS TRUE ORDER BY id LIMIT ? OFFSET ?", (50, offset))
            group_rows.extend(page)
            if len(page) < 50:
                break
            offset += len(page)
        groups = {str(row["id"]): str(row["display_name"] or row["name"]) for row in group_rows}
        teacher = _single_fetchone(database, "SELECT id,display_name FROM identities WHERE id=? AND kind='teacher'", (teacher_id,))
    else:
        with database.connection() as connection:
            groups = {str(row["id"]): str(row["display_name"] or row["name"]) for row in database.execute(connection, "SELECT id,name,display_name FROM groups WHERE canonical IS TRUE").fetchall()}
            teacher = database.execute(connection, "SELECT id,display_name FROM identities WHERE id=? AND kind='teacher'", (teacher_id,)).fetchone()
    if not teacher:
        return {"identity_id": teacher_id, "week_start": week_start, "status": "teacher_not_found", "items": [], "issues": []}
    start = date.fromisoformat(week_start)
    items, issues = [], []
    occupied: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen_assignments: set[tuple[str, str, str, str, str, tuple[str, ...]]] = set()
    joint_items: dict[tuple[str,str,str,str], dict[str, Any]] = {}
    for block in _block_rows(database, effective["effective_week_id"]):
        if block.get("weekday") is None or block.get("change_kind") == "cancelled":
            continue
        day = (start + timedelta(days=int(block["weekday"]))).isoformat()
        for assignment in _assignments(database, block.get("canonical_block_id"), block.get("patch") or {}):
            teacher_ids = [str(value) for value in assignment.get("teacher_ids") or []]
            metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
            if (metadata.get("day_plan_mode") == "sdep" or metadata.get("special_mark") in {"lunch", "no_lesson"}
                    or metadata.get("activity_type") == "lunch" or assignment.get("activity") == _NO_LESSON):
                continue
            responsible_ids=[str(value) for value in metadata.get('responsible_teacher_ids') or []] if metadata.get('activity_format')=='independent_practice' else []
            if teacher_id not in teacher_ids and teacher_id not in responsible_ids:
                continue
            group_ids = [str(value) for value in assignment.get("canonical_group_ids") or []]
            signature = (day, str(block.get("start_time") or ""), str(block.get("end_time") or ""),
                         str(assignment.get("activity") or ""), display_room(metadata.get("room")) or "",
                         tuple(sorted(group_ids)))
            if signature in seen_assignments:
                continue
            seen_assignments.add(signature)
            metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
            item = {"date": day, "weekday": int(block["weekday"]), "start_time": block.get("start_time"), "end_time": block.get("end_time"),
                    "activity": assignment.get("activity"), "audience_group_ids": group_ids,
                    "audiences": [groups.get(group_id, group_id) for group_id in group_ids],
                    "grade_scope": block.get("grade_scope"), "teacher_ids": teacher_ids,
                    "teacher_names": metadata.get("teachers") or [], "room": display_room(metadata.get("room")),
                    "provenance": {"block_key": block["block_key"], "source_provenance": block.get("source_provenance") or {}}}
            item["activity_type"] = metadata.get("activity_type", "lesson")
            if metadata.get("day_plan_mode") == "online":
                item.update(delivery_mode="online", room="", connection_info=(metadata.get("constructor_details") or {}).get("connectionInfo", ""))
            if metadata.get("activity_format") == "independent_practice":
                item['activity_format']='independent_practice'
                item['responsible_teacher_ids']=responsible_ids
                item['responsible_teachers']=metadata.get('responsible_teachers') or []
                item['physical_presence']=teacher_id in teacher_ids
            joint_id = metadata.get("joint_activity_id") if metadata.get("audience_coverage") == "explicit" else None
            if joint_id:
                joint_key = (day,str(block.get("start_time")),str(block.get("end_time")),str(joint_id))
                previous = joint_items.get(joint_key)
                if previous is not None:
                    previous["audience_group_ids"] = sorted(set(previous["audience_group_ids"]) | set(group_ids))
                    previous["audiences"] = [groups.get(g,g) for g in previous["audience_group_ids"]]
                    grades = {g for value in (previous["grade_scope"],item["grade_scope"]) for g in str(value or "").split(",") if g}
                    previous["grade_scope"] = ",".join(sorted(grades,key=int))
                    continue
                joint_items[joint_key] = item
            key = (day, str(block.get("start_time") or ""))
            if teacher_id in teacher_ids:
                occupied[key].append(item)
            items.append(item)
    for key, values in occupied.items():
        if len(values) > 1:
            issues.append({"date": key[0], "start_time": key[1], "reason": "teacher has simultaneous canonical assignments", "items": values})
    published_source, publication_id = publication.source, publication.source_publication_id
    items.extend(_published_unresolved_items(published_source,publication_id,teacher_id,week_start,'teacher'))
    day_modes=_teacher_day_modes(database,published_source,teacher_id,week_start)
    return {"publication": publication.metadata, "identity_id": teacher_id, "week_start": week_start, "status": effective["canonical_status"], "source_snapshot_id": effective["source_snapshot_id"], "items": items, "issues": issues, "day_modes":day_modes}


def project_teacher_range(database: Database, identity_id: Any, start_day: str, end_day: str | None = None) -> dict[str, Any]:
    finish = end_day or start_day
    first, last = date.fromisoformat(start_day), date.fromisoformat(finish)
    if last < first:
        raise ValueError("end_day must not precede start_day")
    # A range can cross month/year boundaries and can contain weeks that have
    # no materialized overlay. Resolve every Monday in the range instead of
    # assuming the first week's effective publication applies to all dates.
    projections: dict[str, dict[str, Any]] = {}
    cursor = first
    while cursor <= last:
        week_start = _week_start(cursor.isoformat())
        if week_start not in projections:
            projections[week_start] = project_teacher(database, identity_id, week_start)
        cursor += timedelta(days=1)

    approved = {"approved_baseline", "approved_with_exceptions", "authoritative"}
    published = [value for value in projections.values() if value.get("status") in approved]
    items = [item for value in published for item in value.get("items", [])
             if start_day <= str(item.get("date")) <= finish]
    issues = [issue for value in published for issue in value.get("issues", [])
              if start_day <= str(issue.get("date", start_day)) <= finish]
    day_modes = [item for value in published for item in value.get("day_modes", [])
                 if start_day <= str(item.get("date")) <= finish]

    statuses = [value.get("status") for value in projections.values()]
    # An unmaterialized week is an expected sparse-range state. It must not
    # hide approved weeks; only an explicitly unpublished week suppresses the
    # aggregate, matching the single-week publication contract.
    status = next((value for value in statuses if value in {"draft", "superseded"}), None)
    if status in {"draft", "superseded"}:
        status = "not_published"
        items, issues, day_modes = [], [], []
    elif published:
        status = published[0].get("status")
    else:
        status = statuses[0] if statuses else "canonical_not_materialized"
    result = dict(next(iter(projections.values()), {}))
    result.update({
        "identity_id": str(identity_id),
        "start_day": start_day,
        "end_day": finish,
        "status": status,
        "items": items,
        "issues": issues,
        "day_modes": day_modes,
    })
    # Keep publication metadata added by the single-week projection.  Older
    # projections do not expose it, so absence remains backward compatible.
    if any("publications" in value for value in projections.values()):
        result["publications"] = [publication for value in projections.values()
                                   for publication in (value.get("publications") or [])]
    return result


def effective_blocks(database: Database, week_start: str) -> dict[str, Any]:
    effective = _effective_week(database, None, week_start)
    if not effective:
        return {"status": "canonical_not_materialized", "week_start": week_start, "items": []}
    return {"status": effective["canonical_status"], "week_start": week_start, "effective_week": effective,
            "items": _block_rows(database, effective["effective_week_id"])}


def published_week_view(database: Database, week_start: str) -> dict[str, Any]:
    """Read runtime-selected effective blocks only for approved canonical versions."""
    edition = resolve_published_week(database, week_start)
    response = {"status": edition.status, "published": edition.metadata["published"], "publication": edition.metadata,
                "week_start": edition.metadata["week_start"], "effective_week_id": edition.metadata["effective_week_id"]}
    items = []
    if not edition.effective:
        return {**response, "items": items}
    for block in _block_rows(database, edition.effective["effective_week_id"]):
        patch = block.get("patch") if isinstance(block.get("patch"), Mapping) else {}
        assignments = [] if block.get("change_kind") == "cancelled" else _assignments(
            database, block.get("canonical_block_id"), patch)
        items.append({**block, "assignments": assignments})
    return {**response, "items": items}
