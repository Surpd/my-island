"""Persistence and read-side projections for the canonical schedule.

Canonical assignments store audience semantics and provenance only. Student sets
are derived at read time from the canonical directory memberships, so a
canonical version remains immutable while projections stay date-aware.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from backend.database import Database


CANONICAL_STATUSES = {"draft", "approved_baseline", "approved_with_exceptions", "authoritative", "superseded"}
_NO_LESSON = "NO_LESSON"
_OPTIONAL_TEACHER_ACTIVITIES = {"Творчество", "Тренинг", "Курс по выбору", "Цифровой трек", "SDEP"}


def _json(database: Database, value: Any) -> Any:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if database.database_url:
        from psycopg.types.json import Jsonb
        return Jsonb(value, dumps=lambda payload: json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    return serialized


def _decode(database: Database, value: Any, fallback: Any) -> Any:
    decoded = database._decode_json_value(value)
    return fallback if decoded is None or decoded == "" else decoded


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
    metadata = {key: value for key, value in assignment.items() if key not in {"student_ids", "student_count", "activity", "role", "kind", "audience"}}
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
            WHERE status <> 'superseded' ORDER BY CASE status WHEN 'authoritative' THEN 0 WHEN 'approved_with_exceptions' THEN 1 WHEN 'approved_baseline' THEN 2 ELSE 3 END, created_at DESC LIMIT 1""").fetchone()
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
                assignment["metadata"] = _decode(database, assignment.get("metadata"), {})
                normalized.append(assignment)
            block["assignments"] = normalized
            result_blocks[block["block_key"]] = block
    return {
        "schema_version": version.get("artifact_schema_version") or "",
        "version_id": version["version_id"],
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
                               overlay_source_snapshot_id: str | None = None) -> dict[str, Any]:
    """Create an immutable effective week from canonical blocks and patches."""
    try:
        start = date.fromisoformat(week_start)
    except ValueError as error:
        raise ValueError("week_start must be an ISO date") from error
    effective_id = _hash({"version_id": version_id, "week_start": week_start})
    patch_by_key = {str(item.get("block_key")): dict(item) for item in patches if item.get("block_key")}
    if database.autocommit:
        return _materialize_effective_week_reconnect(
            database, version_id, start.isoformat(), effective_id, patch_by_key,
            overlay_source_snapshot_id=overlay_source_snapshot_id,
        )
    with database.connection() as connection:
        version = database.execute(connection, "SELECT version_id FROM canonical_schedule_versions WHERE version_id = ?", (version_id,)).fetchone()
        if not version:
            raise ValueError("Canonical version was not found")
        existing = database.execute(connection, "SELECT * FROM canonical_effective_weeks WHERE effective_week_id = ?", (effective_id,)).fetchone()
        if existing:
            return {**dict(existing), "idempotent": True}
        database.execute(connection, """INSERT INTO canonical_effective_weeks(
                effective_week_id,version_id,week_start,status,overlay_source_snapshot_id,provenance)
                VALUES (?,?,?,?,?,?)""", (effective_id, version_id, start.isoformat(), "materialized",
                overlay_source_snapshot_id, _json(database, {"patch_count": len(patch_by_key)})))
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
                                          overlay_source_snapshot_id: str | None) -> dict[str, Any]:
    """Pooler-safe effective-week materialization using short-lived connections."""
    if not _single_fetchone(database, "SELECT version_id FROM canonical_schedule_versions WHERE version_id=?", (version_id,)):
        raise ValueError("Canonical version was not found")
    existing = _single_fetchone(database, "SELECT * FROM canonical_effective_weeks WHERE effective_week_id=?", (effective_id,))
    if existing:
        return {**dict(existing), "idempotent": True}
    _single_execute(database, """INSERT INTO canonical_effective_weeks(
            effective_week_id,version_id,week_start,status,overlay_source_snapshot_id,provenance)
            VALUES (?,?,?,?,?,?)""", (
        effective_id, version_id, week_start, "materialized", overlay_source_snapshot_id,
        _json(database, {"patch_count": len(patch_by_key), "connection_mode": "pooler_autocommit"}),
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
    cache = getattr(database, "_canonical_membership_cache", None)
    if cache is None:
        cache = {}
        setattr(database, "_canonical_membership_cache", cache)
    if day in cache:
        return cache[day]
    membership_query = """SELECT m.group_id,m.identity_id,m.valid_from,m.valid_until
        FROM memberships m JOIN identities i ON i.id=m.identity_id
        WHERE m.active IS TRUE AND m.member_role='student' AND i.kind='student' AND i.status='active'
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
    cache[day] = result
    return result


def _active_students(database: Database) -> dict[str, dict[str, Any]]:
    cached = getattr(database, "_canonical_active_students_cache", None)
    if cached is not None:
        return cached
    if database.autocommit:
        rows: list[Any] = []
        offset = 0
        while True:
            page = _single_fetchall(database, "SELECT id,display_name,class_name FROM identities WHERE kind='student' AND status='active' ORDER BY id LIMIT ? OFFSET ?", (50, offset))
            rows.extend(page)
            if len(page) < 50:
                break
            offset += len(page)
        result = {str(row["id"]): dict(row) for row in rows}
        setattr(database, "_canonical_active_students_cache", result)
        return result
    with database.connection() as connection:
        result = {str(row["id"]): dict(row) for row in database.execute(connection, "SELECT id,display_name,class_name FROM identities WHERE kind='student' AND status='active'").fetchall()}
        setattr(database, "_canonical_active_students_cache", result)
        return result


def _canonical_group_ids(database: Database) -> set[str]:
    cached = getattr(database, "_canonical_group_ids_cache", None)
    if cached is not None:
        return cached
    if database.autocommit:
        rows: list[Any] = []
        offset = 0
        while True:
            page = _single_fetchall(database, "SELECT id FROM groups WHERE canonical IS TRUE ORDER BY id LIMIT ? OFFSET ?", (50, offset))
            rows.extend(page)
            if len(page) < 50:
                break
            offset += len(page)
        result = {str(row["id"]) for row in rows}
        setattr(database, "_canonical_group_ids_cache", result)
        return result
    with database.connection() as connection:
        result = {str(row["id"]) for row in database.execute(connection, "SELECT id FROM groups WHERE canonical IS TRUE").fetchall()}
        setattr(database, "_canonical_group_ids_cache", result)
        return result


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
            page = _single_fetchall(database, base_query + " LIMIT ? OFFSET ?", (effective_week_id, 5, offset))
            rows.extend(page)
            if len(page) < 5:
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
        return [dict(item) for item in patch["assignments"] if isinstance(item, Mapping)]
    cache = getattr(database, "_canonical_assignment_cache", None)
    if cache is None:
        cache = {}
        setattr(database, "_canonical_assignment_cache", cache)
    cache_key = str(block_id)
    if cache_key in cache:
        return cache[cache_key]
    if database.autocommit and not getattr(database, "_canonical_assignments_loaded", False):
        offset = 0
        while True:
            page = _single_fetchall(database, "SELECT * FROM canonical_schedule_assignments ORDER BY id LIMIT ? OFFSET ?", (10, offset))
            for row in page:
                item = dict(row)
                for key in ("canonical_group_ids", "source_cells", "teacher_ids", "metadata"):
                    item[key] = _decode(database, item.get(key), [] if key != "metadata" else {})
                cache[str(row["block_id"])] = cache.get(str(row["block_id"]), []) + [item]
            if len(page) < 10:
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
            item[key] = _decode(database, item.get(key), [] if key != "metadata" else {})
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
        query += " ORDER BY ew.created_at DESC LIMIT 1"
        row = database.execute(connection, query, params).fetchone()
        return dict(row) if row else None


def effective_status(database: Database, week_start: str | None = None) -> dict[str, Any]:
    version = canonical_version(database)
    result = {"canonical": version, "effective_week": None, "status": "canonical_not_imported" if not version else version["status"]}
    if week_start and version:
        result["effective_week"] = _effective_week(database, version["version_id"], week_start)
    return result


def schedule_admin_observability(database: Database, week_start: str | None = None) -> dict[str, Any]:
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
    effective = _effective_week(database, version_id, selected_week)
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
    active_students = _active_students(database)
    memberships = _membership_sets(database, selected_week)

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
            "groups": [group_names.get(group_id, group_id) for group_id in group_ids],
            "teacher_ids": teacher_ids,
            "teachers": [teacher_names.get(teacher_id, teacher_id) for teacher_id in teacher_ids],
            "room": metadata.get("room") or "",
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
        projection = project_student(database, student_id, selected_week)
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
            projection_issues.append({"student_id": student_id, "student_name": student.get("display_name"), "class_name": student.get("class_name"), "date": (date.fromisoformat(selected_week) + timedelta(days=int(block.get("weekday") or 0))).isoformat() if block.get("weekday") is not None else None, "start_time": block.get("start_time"), "block_key": issue.get("block_key"), "reason": issue.get("reason")})

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
            "change_kind": change_kind, "change_classification": classification, "source_cells": source_coordinates(block.get("source_provenance")),
            "source_provenance": block.get("source_provenance") or {}, "baseline_assignments": [assignment_view(item) for item in baseline_assignments],
            "weekly_change": patch if change_kind != "unchanged" else None, "effective_assignments": [assignment_view(item) for item in effective_assignments],
            "affected_students": {"count": rollup.get("students", 0), "states": dict(rollup.get("states", {})), "sample": rollup.get("sample_students", [])},
            "issues": list(block.get("unresolved") or []) + [{"reason": issue["reason"], "activity": issue["activity"], "source_cells": issue["source_cells"]} for issue in teacher_unresolved if issue.get("block_key") == block.get("block_key")],
        })

    effective_assignment_count = sum(len(_assignments(database, block.get("canonical_block_id"), block.get("patch") if isinstance(block.get("patch"), Mapping) else {})) for block in effective_blocks)
    return {
        "status": version.get("status"), "selected_week": selected_week,
        "weeks": [{"week_start": value, "current": value == selected_week, "historical": value != selected_week} for value in available_values],
        "canonical": {"version_id": version_id, "status": version.get("status"), "authoritative": bool((version.get("metadata") or {}).get("authoritative", False)), "source_snapshot_id": version.get("source_snapshot_id"), "source_fingerprint": version.get("source_fingerprint"), "blocks": len(canonical_blocks), "assignments": sum(len(item.get("assignments") or []) for item in canonical_blocks.values())},
        "effective_week": {"effective_week_id": effective.get("effective_week_id"), "week_start": selected_week, "source_snapshot": snapshot, "overlay_patch_count": sum(value for key, value in diff_counts.items() if key != "UNCHANGED"), "effective_blocks": len(rendered_blocks), "diff_counts": dict(diff_counts)},
        "student_projection": {"active_students": len(active_students), "states": dict(projection_states), "conflicts": sum(1 for item in projection_issues if item.get("reason") == "multiple effective activities"), "unresolved": len(projection_issues), "unresolved_by_reason": dict(Counter(str(item.get("reason") or "") for item in projection_issues)), "issues": projection_issues, "samples": dict(projection_samples)},
        "teacher_projection": {"resolved": teacher_resolved, "unresolved": len(teacher_unresolved), "orphan": len(teacher_orphans), "conflicts": len(teacher_conflicts), "unresolved_items": teacher_unresolved, "orphan_items": teacher_orphans, "conflict_items": teacher_conflicts},
        "blocks": rendered_blocks,
    }


def project_student(database: Database, identity_id: Any, week_start: str) -> dict[str, Any]:
    effective = _effective_week(database, None, week_start)
    student_id = str(identity_id)
    if not effective:
        return {"identity_id": student_id, "week_start": week_start, "status": "canonical_not_materialized", "items": [], "issues": []}
    students = _active_students(database)
    student = students.get(student_id)
    if not student:
        return {"identity_id": student_id, "week_start": week_start, "status": "student_not_found", "items": [], "issues": []}
    memberships_by_group = _membership_sets(database, week_start)
    canonical_group_ids = _canonical_group_ids(database)
    items, issues = [], []
    start = date.fromisoformat(week_start)
    for block in _block_rows(database, effective["effective_week_id"]):
        grade = str(block.get("grade_scope") or "")
        if _grade(student.get("class_name")) not in {value for value in grade.split(",") if value}:
            continue
        if block.get("weekday") is None:
            continue
        day = (start + timedelta(days=int(block["weekday"]))).isoformat()
        patch = block.get("patch") or {}
        assignments = _assignments(database, block.get("canonical_block_id"), patch)
        if block.get("change_kind") == "cancelled":
            assignments = []
        claims: list[dict[str, Any]] = []
        remaining = {student_id}
        unresolved_reason = ""
        for assignment in assignments:
            activity = str(assignment.get("activity") or "")
            role = str(assignment.get("assignment_kind") or assignment.get("role") or assignment.get("kind") or "primary")
            group_ids = [str(value) for value in assignment.get("canonical_group_ids") or ((assignment.get("audience") or {}).get("canonical_group_ids") if isinstance(assignment.get("audience"), Mapping) else []) or []]
            unknown_group_ids = [group_id for group_id in group_ids if group_id not in canonical_group_ids]
            if unknown_group_ids:
                unresolved_reason = f"canonical group not found: {', '.join(unknown_group_ids)}"
                continue
            audience = set().union(*(memberships_by_group.get(group_id, set()) for group_id in group_ids)) if group_ids else set(remaining)
            if role in {"default", "residual", "final_state", "final_unassigned"} or str(assignment.get("audience_kind") or "") in {"complement", "remaining"}:
                audience = set(remaining)
            else:
                # Parallel assignments are evaluated in row order. A
                # secondary/parallel canonical group must not re-claim a
                # student already routed by an earlier applicable assignment.
                audience &= set(remaining)
            if student_id in audience:
                provenance = {"block_key": block["block_key"], "source_provenance": block.get("source_provenance") or {}, "source_cells": assignment.get("source_cells") or []}
                if activity == _NO_LESSON or role in {"final_state", "final_unassigned", "explicit_no_lesson"}:
                    claims.append({"state": "NO_LESSON", "activity": _NO_LESSON, "reason": role, "provenance": provenance})
                else:
                    metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
                    claims.append({"state": "ACTIVITY", "activity": activity, "teacher_ids": assignment.get("teacher_ids") or [], "teacher_names": metadata.get("teachers") or [], "room": metadata.get("room", ""), "provenance": provenance})
                remaining.discard(student_id)
        for issue in block.get("unresolved") or []:
            if student_id in {str(value) for value in (issue.get("student_ids") or [])}:
                unresolved_reason = str(issue.get("reason") or "canonical block unresolved")
        effective_activities = {
            str(claim.get("activity") or "").strip()
            for claim in claims
            if claim.get("state") == "ACTIVITY" and str(claim.get("activity") or "").strip()
        }
        if unresolved_reason or len(effective_activities) > 1:
            state = {"state": "UNRESOLVED", "activity": None, "reason": unresolved_reason or "multiple effective activities", "provenance": {"block_key": block["block_key"], "source_provenance": block.get("source_provenance") or {}}}
            issues.append({"block_key": block["block_key"], "reason": state["reason"]})
        elif claims:
            state = claims[0]
        else:
            state = {"state": "NO_LESSON", "activity": _NO_LESSON, "reason": "no remaining canonical activity", "provenance": {"block_key": block["block_key"], "source_provenance": block.get("source_provenance") or {}}}
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
