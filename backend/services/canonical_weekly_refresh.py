"""Read and materialize a current weekly sheet against persisted canonical v2.

This module deliberately does not call the retired schedule parser.  It uses
the source only to classify structural/metadata changes and reuses persisted
canonical assignments for unchanged blocks.  A changed block is materialized
only when the V2 resolver produces a deterministic replacement.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping

from backend.config import Settings
from backend.database import Database
from backend.services.canonical_schedule import (
    canonical_version,
    materialize_effective_week,
    read_canonical_template,
    schedule_admin_observability,
)
from backend.services.google_live import GoogleLiveClient, GoogleLiveError, GoogleTokenStore
from backend.services.google_oauth import google_config
from backend.scripts.build_current_week_overlay import (
    assignment_signature,
    by_source_column,
    comparison_key,
    meta_map,
    norm,
    parse_structure,
    parse_weekly_lessons,
    raw_by_coord,
    semantic_norm,
)
from backend.services.schedule_canonical_bootstrap import build_bootstrap_canonical


def _title(titles: list[str], wanted: str) -> str | None:
    key = wanted.casefold().strip()
    return next((item for item in titles if item.casefold().strip() == key), None)


def _client(settings: Settings) -> tuple[GoogleLiveClient, str]:
    config = google_config(settings)
    store = GoogleTokenStore()
    token = store.load()
    if not token or not token.get("refresh_token"):
        raise GoogleLiveError("Stored Google refresh token is required")
    client = GoogleLiveClient(config, token, store)
    account = client.userinfo()
    return client, str(account.get("email") or "")


def _grid_tab(client: GoogleLiveClient, spreadsheet_id: str, title: str, sheet_id: str) -> dict[str, Any]:
    payload = client.sheet_grid_range(spreadsheet_id, title, "A1:V200")
    sheet = (payload.get("sheets") or [{}])[0]
    data = (sheet.get("data") or [{}])[0]
    start_row = int(data.get("startRow") or 0)
    start_col = int(data.get("startColumn") or 0)
    rows = data.get("rowData") or []
    width = max((start_col + len(row.get("values") or []) for row in rows), default=0)
    values: list[list[dict[str, Any]]] = []
    cells: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows, start=start_row):
        row_values = [{} for _ in range(width)]
        for col_index, cell in enumerate(row.get("values") or [], start=start_col):
            item = dict(cell)
            row_values[col_index] = item
            letters = ""
            number = col_index + 1
            while number:
                number, rem = divmod(number - 1, 26)
                letters = chr(65 + rem) + letters
            coordinate = f"{letters}{row_index + 1}"
            cells.append({"coordinate": coordinate, **item})
        values.append(row_values)
    return {"sheet_id": str(sheet_id), "title": title, "values": values,
            "merges": sheet.get("merges") or [], "structured_cells": cells}


def _db_corpus(database: Database) -> dict[str, list[dict[str, Any]]]:
    with database.connection() as connection:
        students = [dict(row) for row in database.execute(connection, "SELECT id,display_name,class_name,status FROM identities WHERE kind='student'").fetchall()]
        teachers = [dict(row) for row in database.execute(connection, "SELECT id,display_name,status FROM identities WHERE kind='teacher' AND status='active'").fetchall()]
        groups = [dict(row) for row in database.execute(connection, "SELECT * FROM groups WHERE canonical IS TRUE").fetchall()]
        memberships = [dict(row) for row in database.execute(connection, "SELECT group_id,identity_id,member_role,active FROM memberships WHERE active IS TRUE").fetchall()]
    return {"students": students, "teachers": teachers, "groups": groups, "memberships": memberships}


def _artifact_shape(template: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(template)
    blocks: dict[str, Any] = {}
    for key, raw in (template.get("blocks") or {}).items():
        block = dict(raw)
        block["slot"] = {"start": str(block.get("start_time") or "")[:5], "end": str(block.get("end_time") or "")[:5]}
        block["derived_from"] = block.get("source_provenance") or {}
        assignments = []
        for item in block.get("assignments") or []:
            assignment = dict(item)
            assignment["role"] = assignment.get("assignment_kind") or "primary"
            assignment["audience"] = {"type": assignment.get("audience_kind") or "canonical_groups", "canonical_group_ids": assignment.get("canonical_group_ids") or []}
            assignments.append(assignment)
        block["assignments"] = assignments
        blocks[str(key)] = block
    result["blocks"] = blocks
    return result


def _source_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def refresh_current_week(database: Database, settings: Settings, week_start: str = "2026-09-14") -> dict[str, Any]:
    version = canonical_version(database)
    if not version:
        return {"status": "blocked", "message": "Canonical v2 is not persisted"}
    spreadsheet_id = settings.google_sheets_spreadsheet_id or ""
    client, account = _client(settings)
    metadata = client.spreadsheet(spreadsheet_id)
    spreadsheet_title = str((metadata.get("properties") or {}).get("title") or "")
    titles = [str((sheet.get("properties") or {}).get("title") or "") for sheet in metadata.get("sheets") or []]
    template_title = _title(titles, "2026/27 шаблон")
    weekly_title = _title(titles, "14–18 сентября") or _title(titles, "14-18 сентября")
    if not template_title or not weekly_title:
        return {"status": "blocked", "message": "Current template or 14–18 сентября tab was not found", "tabs": titles}
    sheet_by_title = {str((sheet.get("properties") or {}).get("title") or ""): str((sheet.get("properties") or {}).get("sheetId") or "") for sheet in metadata.get("sheets") or []}
    template_tab = _grid_tab(client, spreadsheet_id, template_title, sheet_by_title[template_title])
    weekly_tab = _grid_tab(client, spreadsheet_id, weekly_title, sheet_by_title[weekly_title])
    raw_payload = {"spreadsheet_id": spreadsheet_id, "spreadsheet_title": spreadsheet_title, "sheet_id": weekly_tab["sheet_id"], "sheet_title": weekly_title, "week_start": week_start, "values": weekly_tab["values"], "merges": weekly_tab["merges"], "structured_cells": weekly_tab["structured_cells"]}
    fingerprint = _source_fingerprint(raw_payload)
    corpus_db = _db_corpus(database)
    current = _artifact_shape(read_canonical_template(database, version["version_id"]) or {})
    template_parsed = parse_structure(template_tab["values"], sheet_id=template_tab["sheet_id"], title=template_title, week_start=week_start)
    weekly_parsed = parse_structure(weekly_tab["values"], sheet_id=weekly_tab["sheet_id"], title=weekly_title, week_start=week_start)
    template_raw, weekly_raw = raw_by_coord(template_parsed), raw_by_coord(weekly_parsed)
    weekly_lessons = parse_weekly_lessons(weekly_parsed, corpus_db["teachers"])
    weekly_artifact = build_bootstrap_canonical({"snapshot": {"id": f"weekly-{weekly_tab['sheet_id']}-{week_start}", "fingerprint": fingerprint}, "lessons": weekly_lessons, **corpus_db})
    canonical_by_key = {comparison_key(block): block for block in (current.get("blocks") or {}).values()}
    weekly_by_key = {comparison_key(block): block for block in (weekly_artifact.get("blocks") or {}).values() if block.get("slot")}
    template_meta = meta_map(template_tab["structured_cells"], weekly=False)
    weekly_meta = meta_map(weekly_tab["structured_cells"], weekly=True)
    patches: list[dict[str, Any]] = []
    diff_counts: Counter[str] = Counter()
    for key, base in canonical_by_key.items():
        source_cells = [str(item.get("coordinate") or item.get("cell") or "") if isinstance(item, Mapping) else str(item) for item in (base.get("derived_from") or {}).get("source_cells") or []]
        source_cells = [item for item in source_cells if item]
        if not source_cells:
            source_cells = [str(item["source_cell"]) for item in template_parsed.get(key, [])]
        base_items = {coord: template_raw.get(coord, {}) for coord in source_cells}
        week_items = {str(item["source_cell"]): item for item in weekly_parsed.get(key, [])}
        base_cells, week_cells = by_source_column(list(base_items.values())), by_source_column(list(week_items.values()))
        base_sem = {column: semantic_norm(item.get("raw_text")) for column, item in base_cells.items()}
        week_sem = {column: semantic_norm(item.get("raw_text")) for column, item in week_cells.items()}
        base_raw = {column: norm(item.get("raw_text")) for column, item in base_cells.items()}
        week_raw_values = {column: norm(item.get("raw_text")) for column, item in week_cells.items()}
        base_format = {column: template_meta.get(str(item.get("source_cell")), {}) for column, item in base_cells.items()}
        week_format = {column: weekly_meta.get(str(item.get("source_cell")), {}) for column, item in week_cells.items()}
        if not week_cells:
            classification, kind = "CANCELLED", "cancelled"
        elif base_sem != week_sem:
            classification, kind = "REPLACED", "replaced"
        elif base_raw != week_raw_values or base_format != week_format:
            classification, kind = "METADATA_ONLY", "metadata"
        else:
            classification, kind = "UNCHANGED", "unchanged"
        diff_counts[classification] += 1
        if classification == "UNCHANGED":
            continue
        patch: dict[str, Any] = {"block_key": base.get("block_key"), "change_kind": kind, "change_classification": classification, "weekly_source_cells": sorted(week_items), "weekly_raw_text": {coord: item.get("raw_text") for coord, item in week_items.items()}}
        weekly_block = weekly_by_key.get(key)
        if classification == "REPLACED" and weekly_block and assignment_signature(base) != assignment_signature(weekly_block):
            patch["assignments"] = weekly_block.get("assignments") or []
            patch["source_provenance"] = weekly_block.get("derived_from") or {}
        elif classification == "METADATA_ONLY":
            patch["source_provenance"] = {"sheet": weekly_title, "source_cells": [{"coordinate": coord, "raw_text": item.get("raw_text")} for coord, item in week_items.items()], "metadata_only": True}
        elif classification == "REPLACED":
            patch["source_issue"] = "changed source has no deterministic V2 assignment replacement"
        patches.append(patch)
    for key, block in weekly_by_key.items():
        if key in canonical_by_key:
            continue
        diff_counts["ADDED"] += 1
        patches.append({"block_key": f"weekly-only|{key}", "change_kind": "weekly_only", "change_classification": "ADDED", "assignments": block.get("assignments") or [], "weekday": key[0], "start_time": key[1], "end_time": key[2], "grade_scope": key[3], "source_provenance": block.get("derived_from") or {}})
    effective = materialize_effective_week(database, version["version_id"], week_start, patches, overlay_source_snapshot_id=f"weekly:{weekly_tab['sheet_id']}:{fingerprint}", overlay_fingerprint=fingerprint, overlay_observed_at=datetime.now(timezone.utc).isoformat())
    result = schedule_admin_observability(database, week_start)
    result["refresh"] = {"status": "applied", "google_account": account, "source_fingerprint": fingerprint, "diff_counts": dict(diff_counts), "patch_count": len(patches), "effective": effective}
    return result
