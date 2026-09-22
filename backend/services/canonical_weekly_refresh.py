"""Read and materialize a current weekly sheet against persisted canonical v2.

This module deliberately does not call the retired schedule parser.  It uses
the source only to classify structural/metadata changes and reuses persisted
canonical assignments for unchanged blocks.  A changed block is materialized
only when the V2 resolver produces a deterministic replacement.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
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
from backend.services.schedule_canonical_bootstrap import build_bootstrap_canonical
from backend.services.weekly_schedule_ingestion import (
    WeeklyIngestionError, build_weekly_diff, discover_weekly_tab, layout_profile,
    meta_map, parse_structure, parse_weekly_lessons,
)


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
    queries = {
        "students": "SELECT id,display_name,class_name,status FROM identities WHERE kind='student' ORDER BY id",
        "teachers": "SELECT id,display_name,status FROM identities WHERE kind='teacher' AND status='active' ORDER BY id",
        "groups": "SELECT * FROM groups WHERE canonical IS TRUE ORDER BY id",
        "memberships": "SELECT group_id,identity_id,member_role,active FROM memberships WHERE active IS TRUE ORDER BY group_id,identity_id",
    }
    if database.autocommit:
        result: dict[str, list[dict[str, Any]]] = {}
        for key, query in queries.items():
            rows: list[dict[str, Any]] = []
            offset = 0
            while True:
                with database.connection() as connection:
                    page = database.execute(connection, query + " LIMIT ? OFFSET ?", (50, offset)).fetchall()
                rows.extend(dict(row) for row in page)
                if len(page) < 50:
                    break
                offset += len(page)
            result[key] = rows
        return result
    with database.connection() as connection:
        students = [dict(row) for row in database.execute(connection, queries["students"]).fetchall()]
        teachers = [dict(row) for row in database.execute(connection, queries["teachers"]).fetchall()]
        groups = [dict(row) for row in database.execute(connection, queries["groups"]).fetchall()]
        memberships = [dict(row) for row in database.execute(connection, queries["memberships"]).fetchall()]
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


def _persist_source_snapshot(
    database: Database, *, spreadsheet_id: str, spreadsheet_title: str,
    weekly_tab: Mapping[str, Any], week_start: str, week_end: str,
    fingerprint: str, raw_payload: Mapping[str, Any], structural_payload: Mapping[str, Any],
) -> dict[str, Any]:
    external_key = f"canonical-weekly:{spreadsheet_id}:{weekly_tab['sheet_id']}"
    raw_json = json.dumps(raw_payload, ensure_ascii=False, default=str)
    structural_json = json.dumps(structural_payload, ensure_ascii=False, default=str)
    diagnostics_json = json.dumps({"fingerprint": fingerprint, "week_start": week_start, "week_end": week_end}, ensure_ascii=False)
    json_cast = "?::jsonb" if database.database_url else "?"
    with database.connection() as connection:
        source = database.execute(connection, "SELECT id FROM school_sources WHERE source_type='schedule' AND external_key=?", (external_key,)).fetchone()
        if not source:
            source = database.execute(connection, f"""INSERT INTO school_sources(
                source_type,external_key,display_name,location_ref,authority_status,configuration)
                VALUES ('schedule',?,?,?,'authoritative',{json_cast}) RETURNING id""",
                (external_key, f"{spreadsheet_title} · {weekly_tab['title']}", spreadsheet_id, json.dumps({"sheet_id": weekly_tab["sheet_id"], "kind": "canonical_weekly"}, ensure_ascii=False))).fetchone()
        existing = database.execute(connection, "SELECT id,sync_run_id FROM school_source_snapshots WHERE source_id=? AND fingerprint=?", (source["id"], fingerprint)).fetchone()
        if existing:
            return {"id": str(existing["id"]), "sync_run_id": str(existing["sync_run_id"]), "fingerprint": fingerprint, "idempotent": True}
        previous = database.execute(connection, "SELECT id FROM school_source_snapshots WHERE source_id=? AND is_last_known_valid IS TRUE ORDER BY created_at DESC LIMIT 1", (source["id"],)).fetchone()
        run = database.execute(connection, f"""INSERT INTO school_sync_runs(source_id,mode,status,idempotency_key,diagnostics)
            VALUES (?,'incremental','started',?,{json_cast}) RETURNING id""", (source["id"], f"canonical-weekly:{fingerprint}", diagnostics_json)).fetchone()
        if previous:
            database.execute(connection, "UPDATE school_source_snapshots SET is_last_known_valid=FALSE,status='superseded' WHERE id=?", (previous["id"],))
        snapshot = database.execute(connection, f"""INSERT INTO school_source_snapshots(
            source_id,sync_run_id,previous_snapshot_id,fingerprint,observed_at,effective_from,effective_until,
            raw_payload,structural_payload,status,is_last_known_valid)
            VALUES (?,?,?,?,CURRENT_TIMESTAMP,?,?,{json_cast},{json_cast},'valid',TRUE) RETURNING id""",
            (source["id"], run["id"], previous["id"] if previous else None, fingerprint, week_start, week_end, raw_json, structural_json)).fetchone()
        for key, cells in structural_payload.get("rows", {}).items():
            payload = {"key": key, "cells": cells}
            database.execute(connection, f"""INSERT INTO school_source_records(
                snapshot_id,record_key,fingerprint,source_ref,change_kind,parse_status,raw_payload,structural_payload)
                VALUES (?,?,?,?,'new','structural',{json_cast},{json_cast})""",
                (snapshot["id"], key, _source_fingerprint(payload), f"{weekly_tab['title']}!{','.join(str(item.get('source_cell') or '') for item in cells)}", json.dumps(payload, ensure_ascii=False, default=str), json.dumps(payload, ensure_ascii=False, default=str)))
        database.execute(connection, f"UPDATE school_sync_runs SET status='applied',finished_at=CURRENT_TIMESTAMP,diagnostics={json_cast} WHERE id=?", (diagnostics_json, run["id"]))
        return {"id": str(snapshot["id"]), "sync_run_id": str(run["id"]), "fingerprint": fingerprint, "idempotent": False}


def _effective_block_count(current: Mapping[str, Any], patches: list[Mapping[str, Any]]) -> int:
    known = {
        str(block.get("block_key") or key)
        for key, block in (current.get("blocks") or {}).items()
    }
    added = {str(item.get("block_key")) for item in patches if item.get("block_key") and str(item.get("block_key")) not in known}
    return len(known) + len(added)


def _prepare_current_week(
    database: Database,
    settings: Settings,
    week_start: str | None = None,
    *,
    target_date: date | None = None,
) -> dict[str, Any]:
    """Read and validate one source week without persisting any derived state."""
    version = canonical_version(database)
    if not version:
        return {"status": "blocked", "message": "Canonical v2 is not persisted"}
    spreadsheet_id = settings.google_sheets_spreadsheet_id or ""
    client, account = _client(settings)
    metadata = client.spreadsheet(spreadsheet_id)
    spreadsheet_title = str((metadata.get("properties") or {}).get("title") or "")
    sheets = list(metadata.get("sheets") or [])
    titles = [str((sheet.get("properties") or {}).get("title") or "") for sheet in sheets]
    template_title = _title(titles, "2026/27 шаблон")
    if not template_title:
        return {"status": "blocked", "message": "Current template tab was not found", "tabs": titles}
    selected, candidates = discover_weekly_tab(sheets, target_date=target_date, explicit_week_start=week_start)
    weekly_title, week_start, week_end = selected.title, selected.week_start, selected.week_end
    sheet_by_title = {str((sheet.get("properties") or {}).get("title") or ""): str((sheet.get("properties") or {}).get("sheetId") or "") for sheet in metadata.get("sheets") or []}
    template_tab = _grid_tab(client, spreadsheet_id, template_title, sheet_by_title[template_title])
    weekly_tab = _grid_tab(client, spreadsheet_id, weekly_title, sheet_by_title[weekly_title])
    template_layout = layout_profile(template_tab["values"], template_tab["merges"])
    weekly_layout = layout_profile(weekly_tab["values"], weekly_tab["merges"])
    if (
        template_layout["class_header_row"] != weekly_layout["class_header_row"]
        or template_layout["used_columns"] != weekly_layout["used_columns"]
        or template_layout["audiences"] != weekly_layout["audiences"]
    ):
        raise WeeklyIngestionError(f"Schedule layout drift: weekly class columns differ from template; template={template_layout['signature']}; weekly={weekly_layout['signature']}")
    raw_payload = {"spreadsheet_id": spreadsheet_id, "spreadsheet_title": spreadsheet_title, "sheet_id": weekly_tab["sheet_id"], "sheet_title": weekly_title, "range": "A1:V200", "week_start": week_start, "week_end": week_end, "values": weekly_tab["values"], "merges": weekly_tab["merges"], "structured_cells": weekly_tab["structured_cells"]}
    fingerprint = _source_fingerprint(raw_payload)
    corpus_db = _db_corpus(database)
    current = _artifact_shape(read_canonical_template(database, version["version_id"]) or {})
    template_parsed = parse_structure(template_tab["values"], sheet_id=template_tab["sheet_id"], title=template_title, week_start=week_start, merges=template_tab["merges"])
    weekly_parsed = parse_structure(weekly_tab["values"], sheet_id=weekly_tab["sheet_id"], title=weekly_title, week_start=week_start, merges=weekly_tab["merges"])
    weekly_lessons = parse_weekly_lessons(weekly_parsed, corpus_db["teachers"])
    weekly_artifact = build_bootstrap_canonical({"snapshot": {"id": f"weekly-{weekly_tab['sheet_id']}-{week_start}", "fingerprint": fingerprint}, "lessons": weekly_lessons, **corpus_db})
    template_meta = meta_map(template_tab["structured_cells"], weekly=False)
    weekly_meta = meta_map(weekly_tab["structured_cells"], weekly=True)
    diff = build_weekly_diff(current, template_parsed, weekly_parsed, weekly_artifact, template_meta, weekly_meta)
    structural_payload = {"layout": weekly_layout, "candidate_tabs": candidates, "rows": {"|".join(map(str, key)): cells for key, cells in weekly_parsed.items()}}
    return {
        "status": "ready",
        "version": version,
        "google_account": account,
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_title": spreadsheet_title,
        "selected": selected,
        "candidate_tabs": candidates,
        "weekly_tab": weekly_tab,
        "week_start": week_start,
        "week_end": week_end,
        "template_layout": template_layout,
        "weekly_layout": weekly_layout,
        "raw_payload": raw_payload,
        "structural_payload": structural_payload,
        "fingerprint": fingerprint,
        "current": current,
        "weekly_artifact": weekly_artifact,
        "diff": diff,
        "effective_block_count": _effective_block_count(current, diff["patches"]),
    }


def _preview_payload(prepared: Mapping[str, Any]) -> dict[str, Any]:
    if prepared.get("status") != "ready":
        return dict(prepared)
    selected = prepared["selected"]
    weekly_tab = prepared["weekly_tab"]
    structural = prepared["structural_payload"]
    rows = structural.get("rows") or {}
    diff = prepared["diff"]
    warnings = [
        {"block_key": patch.get("block_key"), "warning": patch.get("source_issue")}
        for patch in diff["patches"]
        if patch.get("source_issue")
    ]
    return {
        "status": "preview",
        "mode": "read_only",
        "writes_performed": 0,
        "google_account": prepared["google_account"],
        "selected_tab": selected.as_dict(),
        "candidate_tabs": prepared["candidate_tabs"],
        "week_start": prepared["week_start"],
        "week_end": prepared["week_end"],
        "source_fingerprint": prepared["fingerprint"],
        "layout": prepared["weekly_layout"],
        "structural_row_count": len(rows),
        "structural_cell_count": sum(len(cells) for cells in rows.values()),
        "merge_count": len(weekly_tab.get("merges") or []),
        "merges": weekly_tab.get("merges") or [],
        "canonical_block_count": len(prepared["current"].get("blocks") or {}),
        "weekly_block_count": len(prepared["weekly_artifact"].get("blocks") or {}),
        "diff_counts": diff["counts"],
        "overlay_patch_count": len(diff["patches"]),
        "overlay_patches": diff["patches"],
        "effective_block_count": prepared["effective_block_count"],
        "warnings": warnings,
        "errors": [],
    }


def preview_current_week(database: Database, settings: Settings, week_start: str | None = None, *, target_date: date | None = None) -> dict[str, Any]:
    """Return the exact Stage 1 plan without creating snapshots, runs, records, or effective weeks."""
    return _preview_payload(_prepare_current_week(database, settings, week_start, target_date=target_date))


def refresh_current_week(database: Database, settings: Settings, week_start: str | None = None, *, target_date: date | None = None) -> dict[str, Any]:
    prepared = _prepare_current_week(database, settings, week_start, target_date=target_date)
    if prepared.get("status") != "ready":
        return dict(prepared)
    selected = prepared["selected"]
    weekly_tab = prepared["weekly_tab"]
    week_start = str(prepared["week_start"])
    week_end = str(prepared["week_end"])
    fingerprint = str(prepared["fingerprint"])
    diff = prepared["diff"]
    structural_payload = prepared["structural_payload"]
    snapshot = _persist_source_snapshot(database, spreadsheet_id=str(prepared["spreadsheet_id"]), spreadsheet_title=str(prepared["spreadsheet_title"]), weekly_tab=weekly_tab, week_start=week_start, week_end=week_end, fingerprint=fingerprint, raw_payload=prepared["raw_payload"], structural_payload=structural_payload)
    effective = materialize_effective_week(database, prepared["version"]["version_id"], week_start, diff["patches"], overlay_source_snapshot_id=snapshot["id"], overlay_fingerprint=fingerprint, overlay_observed_at=datetime.now(timezone.utc).isoformat())
    result = schedule_admin_observability(database, week_start)
    result["refresh"] = {"status": "applied", "google_account": prepared["google_account"], "selected_tab": selected.as_dict(), "candidate_tabs": prepared["candidate_tabs"], "source_snapshot": snapshot, "source_fingerprint": fingerprint, "diff_counts": diff["counts"], "patch_count": len(diff["patches"]), "effective_block_count": prepared["effective_block_count"], "effective": effective}
    return result
