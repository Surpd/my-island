"""Persistence and read-side projections for the canonical schedule.

Canonical assignments store audience semantics and provenance only. Student sets
are derived at read time from the canonical directory memberships, so a
canonical version remains immutable while projections stay date-aware.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from backend.database import Database


CANONICAL_STATUSES = {"draft", "approved_baseline", "approved_with_exceptions", "authoritative", "superseded"}
_NO_LESSON = "NO_LESSON"
_OPTIONAL_TEACHER_ACTIVITIES = {"Творчество", "Тренинг", "Курс по выбору", "Цифровой трек", "SDEP"}
_RESIDUAL_ROLES = {"default", "residual", "final_state", "final_unassigned", "explicit_no_lesson"}


@dataclass
class StudentProjectionContext:
    """Fresh directory snapshot shared by every student projection in one request."""

    effective: dict[str, Any]
    students: dict[str, dict[str, Any]]
    groups: dict[str, dict[str, Any]]
    memberships: list[dict[str, Any]]
    exclusions: list[dict[str, Any]]
    blocks: list[dict[str, Any]]


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


def canonical_version(database: Database, version_id: str | None = None) -> dict[str, Any] | None:
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
        result_blocks: dict[str, Any] = {}
        for row in blocks:
            block = dict(row)
            block["source_provenance"] = _decode(database, block.get("source_provenance"), {})
            block["evidence"] = _decode(database, block.get("evidence"), {})
            block["unresolved"] = _decode(database, block.get("unresolved"), [])
            assignments = database.execute(
                connection,
                "SELECT * FROM canonical_schedule_assignments WHERE block_id=? ORDER BY ordinal",
                (row["id"],),
            ).fetchall()
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
) -> StudentProjectionContext | None:
    """Load mutable directory state once; never retain it on ``Database``."""
    effective = _effective_week(database, version_id, week_start)
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
    exclusions = [
        dict(row)
        for row in _all_rows(
            database,
            """SELECT id,identity_id,group_id,member_role,action,active,valid_from,valid_until
                 FROM membership_overrides
                WHERE active IS TRUE AND action='exclude'
                ORDER BY id""",
        )
    ]
    # Assignment rows are immutable with their canonical block. Preloading them
    # once avoids one connection per block while remaining safely keyed by the
    # immutable block id (unlike mutable directory caches).
    assignment_cache = getattr(database, "_canonical_assignment_cache", None)
    if assignment_cache is None:
        assignment_cache = {}
        setattr(database, "_canonical_assignment_cache", assignment_cache)
    if not getattr(database, "_canonical_assignments_loaded", False):
        for row in _all_rows(database, "SELECT * FROM canonical_schedule_assignments ORDER BY id"):
            item = dict(row)
            for key in ("canonical_group_ids", "source_cells", "teacher_ids", "metadata"):
                decoded = _decode(database, item.get(key), [] if key != "metadata" else {})
                item[key] = _assignment_metadata(decoded) if key == "metadata" else decoded
            assignment_cache.setdefault(str(row["block_id"]), []).append(item)
        setattr(database, "_canonical_assignments_loaded", True)
    return StudentProjectionContext(
        effective=dict(effective),
        students=students,
        groups=groups,
        memberships=memberships,
        exclusions=exclusions,
        blocks=_block_rows(database, str(effective["effective_week_id"])),
    )


def _date_in_period(day: str, start: Any, end: Any) -> bool:
    start_text, end_text = str(start or ""), str(end or "")
    return (not start_text or start_text <= day) and (not end_text or end_text >= day)


def _student_memberships(context: StudentProjectionContext, student_id: str, day: str) -> set[str]:
    excluded = {
        str(row["group_id"])
        for row in context.exclusions
        if str(row.get("identity_id")) == student_id
        and str(row.get("member_role") or "student") == "student"
        and _date_in_period(day, row.get("valid_from"), row.get("valid_until"))
    }
    return {
        str(row["group_id"])
        for row in context.memberships
        if str(row.get("identity_id")) == student_id
        and bool(row.get("active"))
        and str(row.get("member_role")) == "student"
        and str(row.get("identity_status")) == "active"
        and str(row.get("identity_kind")) == "student"
        and bool(row.get("group_canonical"))
        and _date_in_period(day, row.get("valid_from"), row.get("valid_until"))
        and str(row["group_id"]) not in excluded
    }


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
    cache[effective_week_id] = result
    return result


def _assignments(database: Database, block_id: Any, patch: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    if patch and isinstance(patch.get("assignments"), list):
        result = [dict(item) for item in patch["assignments"] if isinstance(item, Mapping)]
        # A weekly patch replaces the assignment payload, but an omitted teacher
        # is not evidence that a reviewed baseline teacher was removed. Preserve
        # that proof when the source cell and canonical audience are unchanged.
        if block_id is not None and result:
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
    cache = getattr(database, "_canonical_assignment_cache", None)
    if cache is None:
        cache = {}
        setattr(database, "_canonical_assignment_cache", cache)
    cache_key = str(block_id)
    if cache_key in cache:
        return cache[cache_key]
    if database.database_url and not getattr(database, "_canonical_assignments_loaded", False):
        offset = 0
        while True:
            page = _single_fetchall(database, "SELECT * FROM canonical_schedule_assignments ORDER BY id LIMIT ? OFFSET ?", (1000, offset))
            for row in page:
                item = dict(row)
                for key in ("canonical_group_ids", "source_cells", "teacher_ids", "metadata"):
                    decoded = _decode(database, item.get(key), [] if key != "metadata" else {})
                    item[key] = _assignment_metadata(decoded) if key == "metadata" else decoded
                cache[str(row["block_id"])] = cache.get(str(row["block_id"]), []) + [item]
            if len(page) < 1000:
                break
            offset += len(page)
        setattr(database, "_canonical_assignments_loaded", True)
        return cache.get(cache_key, [])
    with database.connection() as connection:
        rows = database.execute(connection, "SELECT * FROM canonical_schedule_assignments WHERE block_id=? ORDER BY ordinal", (block_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for key in ("canonical_group_ids", "source_cells", "teacher_ids", "metadata"):
            decoded = _decode(database, item.get(key), [] if key != "metadata" else {})
            item[key] = _assignment_metadata(decoded) if key == "metadata" else decoded
        result.append(item)
    cache[cache_key] = result
    return result


def _effective_week(database: Database, version_id: str | None, week_start: str) -> dict[str, Any] | None:
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


def effective_status(database: Database, week_start: str | None = None) -> dict[str, Any]:
    version = canonical_version(database)
    result = {"canonical": version, "effective_week": None, "status": "canonical_not_imported" if not version else version["status"]}
    if week_start and version:
        result["effective_week"] = _effective_week(database, version["version_id"], week_start)
    return result


def schedule_admin_observability(database: Database, week_start: str | None = None, *, include_student_projection: bool = True) -> dict[str, Any]:
    """Return a read-only, human-oriented view of persisted canonical schedule state."""
    version = canonical_version(database)
    if not version:
        return {"status": "canonical_not_imported", "weeks": [], "blocks": []}
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
    if not effective:
        return {
            "status": "canonical_not_materialized",
            "canonical": {"version_id": version_id, "status": version.get("status"), "authoritative": (version.get("metadata") or {}).get("authoritative", False), "source_fingerprint": version.get("source_fingerprint")},
            "weeks": [{"week_start": value, "current": value == selected_week} for value in available_values],
            "selected_week": selected_week,
            "blocks": [],
        }

    canonical = read_canonical_template(database, version_id) or {}
    canonical_blocks = canonical.get("blocks") if isinstance(canonical.get("blocks"), Mapping) else {}
    effective_blocks = _block_rows(database, effective["effective_week_id"])
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
            "room": metadata.get("room") or "",
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
        "status": version.get("status"), "selected_week": selected_week,
        "weeks": [{"week_start": value, "current": value == selected_week, "historical": value != selected_week} for value in available_values],
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
    role = str(claim.get("role") or "primary")
    role_priority = {"primary": 40, "secondary": 30, "special": 20}.get(role, 10)
    group_roles = set(claim.get("group_roles") or [])
    specificity = 30 if "instructional_partition" in group_roles else 20 if "elective_or_special" in group_roles else 0
    return role_priority, specificity


def _other_assignment_occupies_slot(
    context: StudentProjectionContext, student: Mapping[str, Any], membership_groups: set[str],
    assignment: Mapping[str, Any], grade_scope: str,
) -> bool:
    if str(assignment.get("activity") or "") == _NO_LESSON:
        return False
    rule = _assignment_audience_rule(assignment)
    kind = str(rule.get("kind") or assignment.get("audience_kind") or "groups")
    student_id = str(student["id"])
    if student_id in {str(value) for value in rule.get("exclude_student_ids") or []}:
        return False
    if student_id in {str(value) for value in rule.get("include_student_ids") or []}:
        return True
    grade = str(rule.get("grade") or grade_scope or "")
    in_grade = bool(grade and str(student.get("class_name") or "").strip().startswith(grade))
    if kind == "whole_grade":
        return in_grade
    if kind == "students":
        return False
    if kind == "available_slot":
        return False
    if kind == "remaining":
        partition_ids = {str(value) for value in rule.get("partition_group_ids") or []}
        dimension = str(rule.get("partition_dimension") or "")
        if dimension:
            partition_ids.update(group_id for group_id, group in context.groups.items() if _group_dimension(group)[0] == dimension)
        return in_grade and not bool(membership_groups.intersection(partition_ids))
    return bool(membership_groups.intersection(_assignment_group_ids(assignment)))


def _student_busy_in_slot(
    database: Database, context: StudentProjectionContext, student: Mapping[str, Any],
    membership_groups: set[str], block: Mapping[str, Any], assignment_index: int,
) -> bool:
    bell_starts = ("09:00", "09:50", "10:50", "11:50", "12:45", "13:40", "14:35", "15:30", "16:20")

    def period(value: Any) -> int:
        try:
            hour, minute = (int(part) for part in str(value or "").split(":")[:2])
            minutes = hour * 60 + minute
            return min(range(len(bell_starts)), key=lambda index: abs(minutes - int(bell_starts[index][:2]) * 60 - int(bell_starts[index][3:])))
        except (ValueError, TypeError):
            return -1

    target_period = period(block.get("start_time"))
    for other_block in context.blocks:
        if other_block.get("weekday") != block.get("weekday") or target_period < 0 or period(other_block.get("start_time")) != target_period:
            continue
        if other_block.get("change_kind") == "cancelled":
            continue
        patch = other_block.get("patch") if isinstance(other_block.get("patch"), Mapping) else {}
        for other_index, other_assignment in enumerate(_assignments(database, other_block.get("canonical_block_id"), patch)):
            if other_block.get("block_key") == block.get("block_key") and other_index == assignment_index:
                continue
            if _other_assignment_occupies_slot(context, student, membership_groups, other_assignment, str(other_block.get("grade_scope") or "")):
                return True
    return False


def _project_student_block(
    database: Database,
    context: StudentProjectionContext,
    student: Mapping[str, Any],
    block: Mapping[str, Any],
    day: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    student_id = str(student["id"])
    membership_groups = _student_memberships(context, student_id, day)
    memberships_by_group: defaultdict[str, list[str]] = defaultdict(list)
    for membership in context.memberships:
        group_id = str(membership.get("group_id"))
        if group_id in membership_groups and str(membership.get("identity_id")) == student_id:
            memberships_by_group[group_id].append(str(membership.get("id")))

    patch = block.get("patch") if isinstance(block.get("patch"), Mapping) else {}
    assignments = [] if block.get("change_kind") == "cancelled" else _assignments(database, block.get("canonical_block_id"), patch)
    claims: list[dict[str, Any]] = []
    residual_claims: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    assignment_traces: list[dict[str, Any]] = []

    for index, assignment in enumerate(assignments):
        assignment_id = _assignment_id(block, assignment, index)
        activity = str(assignment.get("activity") or "").strip()
        role = _assignment_role(assignment)
        group_ids = _assignment_group_ids(assignment)
        audience_rule = _assignment_audience_rule(assignment)
        audience_kind = str(audience_rule.get("kind") or assignment.get("audience_kind") or "groups")
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
            explicit_match = explicit_match or (bool(grade) and str(student.get("class_name") or "").strip().startswith(grade))
        elif audience_kind == "students":
            explicit_match = student_id in include_student_ids
        elif audience_kind == "remaining":
            grade = str(audience_rule.get("grade") or block.get("grade_scope") or "")
            partition_ids = {str(value) for value in audience_rule.get("partition_group_ids") or []}
            dimension = str(audience_rule.get("partition_dimension") or "")
            if dimension:
                partition_ids.update(group_id for group_id, group in context.groups.items() if _group_dimension(group)[0] == dimension)
            explicit_match = bool(not grade or str(student.get("class_name") or "").strip().startswith(grade)) and not bool(membership_groups.intersection(partition_ids))
        elif audience_kind == "available_slot":
            grade = str(audience_rule.get("grade") or block.get("grade_scope") or "")
            explicit_match = bool(grade and str(student.get("class_name") or "").strip().startswith(grade)) and not _student_busy_in_slot(database, context, student, membership_groups, block, index)
        dimensions = []
        dimension_evidence = []
        group_roles = []
        for group_id in matched_group_ids:
            group = context.groups[group_id]
            dimension, evidence = _group_dimension(group)
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
        for group_id in _assignment_group_ids(assignment):
            group = context.groups.get(group_id)
            if not group or not bool(group.get("canonical")):
                continue
            role = str(group.get("role") or "")
            group_type = str(group.get("group_type") or "")
            if role not in {"base_class", "instructional_partition"} and group_type not in {"class", "instructional", "instructional_group", "subject_group"}:
                continue
            dimension, evidence = _group_dimension(group)
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
            activities = {claim["activity"] for claim in best}
            if len(activities) > 1:
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
                selected = sorted(best, key=lambda claim: (claim["activity"], claim["assignment_id"]))[0]
        if selected is not None:
            assignment = selected["assignment"]
            metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
            result = {
                "state": "ACTIVITY", "activity": selected["activity"],
                "teacher_ids": assignment.get("teacher_ids") or [], "teacher_names": metadata.get("teachers") or [],
                "room": metadata.get("room", ""),
                "provenance": {**provenance, "source_cells": selected["source_cells"]},
            }
        elif result is None:
            non_no_lesson = [claim for claim in residual_claims if claim["activity"] and claim["activity"] != _NO_LESSON]
            residual_activities = {claim["activity"] for claim in non_no_lesson}
            if len(residual_activities) > 1:
                trace = {"assignment_ids": sorted(claim["assignment_id"] for claim in non_no_lesson), "source_cells": sorted({str(cell) for claim in non_no_lesson for cell in claim["source_cells"]}), "final_state": "UNRESOLVED", "assignments": assignment_traces}
                issue = _projection_issue("MULTIPLE_ACTIVITY_CLAIMS", "multiple residual activities apply", student=student, block=block, day=day, trace=trace)
                issues.append(issue)
                result = {"state": "UNRESOLVED", "activity": None, "reason": issue["reason"], "issue_codes": [issue["code"]], "provenance": provenance}
            elif non_no_lesson:
                selected = sorted(non_no_lesson, key=lambda claim: (claim["activity"], claim["assignment_id"]))[0]
                assignment = selected["assignment"]
                metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
                result = {"state": "ACTIVITY", "activity": selected["activity"], "teacher_ids": assignment.get("teacher_ids") or [], "teacher_names": metadata.get("teachers") or [], "room": metadata.get("room", ""), "provenance": {**provenance, "source_cells": selected["source_cells"]}}
            else:
                note = next((str(trace.get("note") or "") for trace in assignment_traces if trace["matched"] and trace["activity"] == _NO_LESSON and trace.get("note")), "")
                result = {"state": "NO_LESSON", "activity": _NO_LESSON, "reason": "no applicable canonical activity", "note": note, "provenance": provenance}
    assert result is not None
    result["trace"] = {"memberships": sorted(membership_groups), "assignments": assignment_traces, "final_state": result["state"], "selected_activity": result.get("activity")}
    if result["state"] == "NO_LESSON" and any(trace["matched"] and trace["activity"] != _NO_LESSON for trace in assignment_traces):
        trace = {"source_cells": sorted({str(cell) for item in assignment_traces for cell in item["source_cells"]}), "final_state": "NO_LESSON", "assignments": assignment_traces}
        issue = _projection_issue("UNEXPECTED_NO_LESSON", "applicable activity claim ended as NO_LESSON", student=student, block=block, day=day, trace=trace)
        issues.append(issue)
        result = {**result, "state": "UNRESOLVED", "activity": None, "reason": issue["reason"], "issue_codes": [issue["code"]]}
    return result, issues


def project_student(
    database: Database,
    identity_id: Any,
    week_start: str,
    *,
    context: StudentProjectionContext | None = None,
) -> dict[str, Any]:
    context = context or build_student_projection_context(database, week_start)
    student_id = str(identity_id)
    if not context:
        return {"identity_id": student_id, "week_start": week_start, "status": "canonical_not_materialized", "items": [], "issues": []}
    effective = context.effective
    student = context.students.get(student_id)
    if not student or student.get("status") != "active":
        return {"identity_id": student_id, "week_start": week_start, "status": "student_not_found", "items": [], "issues": []}
    items, issues = [], []
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
        items.append({"block_key": block["block_key"], "date": day, "weekday": int(block["weekday"]), "start_time": block.get("start_time"), "end_time": block.get("end_time"), **state})
    return {"identity_id": student_id, "week_start": week_start, "status": effective["canonical_status"], "source_snapshot_id": effective["source_snapshot_id"], "items": items, "issues": issues}


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
    items = [item for projection in projections.values() for item in projection["items"] if start_day <= str(item.get("date")) <= finish]
    issues = [issue for projection in projections.values() for issue in projection["issues"]]
    statuses = [projection["status"] for projection in projections.values()]
    status = next((item for item in statuses if item not in {"approved_baseline", "approved_with_exceptions", "authoritative"}), statuses[0] if statuses else "canonical_not_materialized")
    return {"identity_id": str(identity_id), "start_day": start_day, "end_day": finish, "status": status, "items": items, "issues": issues}


def project_teacher(database: Database, identity_id: Any, week_start: str) -> dict[str, Any]:
    week_start = _week_start(week_start)
    effective = _effective_week(database, None, week_start)
    teacher_id = str(identity_id)
    if not effective:
        return {"identity_id": teacher_id, "week_start": week_start, "status": "canonical_not_materialized", "items": [], "issues": []}
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
    seen_assignments: set[tuple[str, str, str, str, tuple[str, ...]]] = set()
    for block in _block_rows(database, effective["effective_week_id"]):
        if block.get("weekday") is None:
            continue
        day = (start + timedelta(days=int(block["weekday"]))).isoformat()
        for assignment in _assignments(database, block.get("canonical_block_id"), block.get("patch") or {}):
            teacher_ids = [str(value) for value in assignment.get("teacher_ids") or []]
            if teacher_id not in teacher_ids:
                continue
            group_ids = [str(value) for value in assignment.get("canonical_group_ids") or []]
            signature = (day, str(block.get("start_time") or ""), teacher_id,
                         str(assignment.get("activity") or ""), tuple(sorted(group_ids)))
            if signature in seen_assignments:
                continue
            seen_assignments.add(signature)
            metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
            item = {"date": day, "weekday": int(block["weekday"]), "start_time": block.get("start_time"), "end_time": block.get("end_time"),
                    "activity": assignment.get("activity"), "audience_group_ids": group_ids,
                    "audiences": [groups.get(group_id, group_id) for group_id in group_ids],
                    "grade_scope": block.get("grade_scope"), "teacher_ids": teacher_ids,
                    "teacher_names": metadata.get("teachers") or [], "room": metadata.get("room", ""),
                    "provenance": {"block_key": block["block_key"], "source_provenance": block.get("source_provenance") or {}}}
            key = (day, str(block.get("start_time") or ""))
            occupied[key].append(item)
            items.append(item)
    for key, values in occupied.items():
        if len(values) > 1:
            issues.append({"date": key[0], "start_time": key[1], "reason": "teacher has simultaneous canonical assignments", "items": values})
    return {"identity_id": teacher_id, "week_start": week_start, "status": effective["canonical_status"], "source_snapshot_id": effective["source_snapshot_id"], "items": items, "issues": issues}


def project_teacher_range(database: Database, identity_id: Any, start_day: str, end_day: str | None = None) -> dict[str, Any]:
    finish = end_day or start_day
    first, last = date.fromisoformat(start_day), date.fromisoformat(finish)
    if last < first:
        raise ValueError("end_day must not precede start_day")
    projection = project_teacher(database, identity_id, _week_start(start_day))
    projection["start_day"] = start_day
    projection["end_day"] = finish
    projection["items"] = [item for item in projection.get("items", []) if start_day <= str(item.get("date")) <= finish]
    projection["issues"] = [issue for issue in projection.get("issues", []) if start_day <= str(issue.get("date", start_day)) <= finish]
    return projection


def effective_blocks(database: Database, week_start: str) -> dict[str, Any]:
    effective = _effective_week(database, None, week_start)
    if not effective:
        return {"status": "canonical_not_materialized", "week_start": week_start, "items": []}
    return {"status": effective["canonical_status"], "week_start": week_start, "effective_week": effective,
            "items": _block_rows(database, effective["effective_week_id"])}
