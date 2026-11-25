"""One owned worksheet per week. Render only the immutable, released lessons."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from uuid import uuid4

from backend.services.schedule_sheet_export import build_sheet_plan, column_name, freeze_week, sheets_requests
from backend.services.schedule_sheet_publishing import TEST_EXPORT_TARGET, _spreadsheet_url, _target


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS published_week_sheet_bindings(
        target_id TEXT NOT NULL, week_start TEXT NOT NULL, sheet_id INTEGER NOT NULL,
        delivered_revision INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(target_id,week_start), UNIQUE(target_id,sheet_id))""",
    """CREATE TABLE IF NOT EXISTS published_week_sheet_jobs(
        target_id TEXT NOT NULL, publication_id TEXT NOT NULL, week_start TEXT NOT NULL,
        head_revision INTEGER NOT NULL, directory_json TEXT NOT NULL, state TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL DEFAULT 0,
        lease_token TEXT, lease_until INTEGER NOT NULL DEFAULT 0, error_code TEXT,
        PRIMARY KEY(target_id,publication_id), UNIQUE(target_id,week_start,head_revision))""",
)


class DeliveryConflict(ValueError):
    pass


def enabled():
    return os.getenv("MY_ISLAND_PUBLISHED_WEEK_SHEETS") == "1"


def enqueue(database, connection, week, publication_id):
    # OAuth, rendering and remote I/O happen only after the publication commits.
    target = os.getenv("GOOGLE_SCHEDULE_EXPORT_SPREADSHEET_ID", TEST_EXPORT_TARGET).strip()
    revision = database.execute(connection, "SELECT revision FROM schedule_publication_heads WHERE scope_key=?", ("week:" + week,)).fetchone()["revision"]
    digest = hashlib.sha256(f"my-island:published:{target}:{week}".encode()).hexdigest()
    sid = int(digest[:8], 16) % 2_000_000_000 + 1
    database.execute(connection, """INSERT INTO published_week_sheet_bindings(target_id,week_start,sheet_id) VALUES(?,?,?)
        ON CONFLICT(target_id,week_start) DO NOTHING""", (target, week, sid))
    groups = database.execute(connection, "SELECT id,name,display_name,group_type,base_class_name,subject,subject_subgroup,semantic_dimension FROM groups ORDER BY id").fetchall()
    teachers = database.execute(connection, "SELECT id,display_name FROM identities WHERE kind='teacher' ORDER BY id").fetchall()
    directory = {"groups": [dict(g) for g in groups], "teachers": [dict(t) for t in teachers]}
    database.execute(connection, """INSERT INTO published_week_sheet_jobs(target_id,publication_id,week_start,head_revision,directory_json,state)
        VALUES(?,?,?,?,?,'pending') ON CONFLICT(target_id,publication_id) DO NOTHING""",
        (target, publication_id, week, revision, json.dumps(directory, ensure_ascii=False, default=str)))
    database.execute(connection, """UPDATE published_week_sheet_jobs SET state='superseded',lease_token=NULL
        WHERE target_id=? AND week_start=? AND head_revision<? AND state IN ('pending','retry','working')""", (target, week, revision))


def _plan(database, job):
    from backend.services.constructor_publication import get_published_source
    archived = get_published_source(database, job["publication_id"])["published_source"]
    if not archived:
        raise DeliveryConflict("missing_published_source")
    approved = {r["lesson_id"] for r in archived["records"] if r["status"] != "unresolved"}
    payload = {**archived["payload"], "lessons": [l for l in archived["payload"]["lessons"] if str(l["id"]) in approved]}
    if job["target_id"] != _target(payload.get("school_source")):
        raise DeliveryConflict("target_changed")
    source = {"draft_id": job["publication_id"], "name": "Опубликованная неделя", "revision": job["head_revision"]}
    snapshot = freeze_week({"source": source, "payload": payload, **json.loads(job["directory_json"])}, job["week_start"])
    return build_sheet_plan(snapshot, title="Неделя " + job["week_start"], sheet_id=job["sheet_id"],
                           mapping_sheet_id=0, layout="school-grid",
                           existing_notes={l["id"]: [l["note"]] for l in payload["lessons"] if l.get("note")})


def _contents(rows):
    values = []
    for y, row in enumerate(rows):
        for x, cell in enumerate(row.get("values", [])):
            if cell.get("userEnteredValue") or cell.get("note"):
                values.append([y, x, cell.get("userEnteredValue"), cell.get("note") or ""])
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _batch(job, plan, book, client):
    target, sid = job["target_id"], job["sheet_id"]
    title = plan["title"]
    props = {s["properties"]["sheetId"]: s["properties"] for s in book.get("sheets", [])}
    old = props.get(sid)
    titles = {p["title"] for p in props.values()}
    if not old and title in titles:
        title = f"{title} · My Island {sid}"
    raw = sheets_requests({**plan, "title": title}, spreadsheet_url=_spreadsheet_url(target))
    # Only the visible planner belongs to weekly delivery. Historical draft
    # exports keep their original two-tab layout, snapshots and identifiers.
    writes = [r for r in raw[2:] if r.get("updateCells", {}).get("start", {}).get("sheetId") != 0]
    main_cells = next(r["updateCells"]["rows"] for r in writes if "updateCells" in r)
    prefix = "MI_WEEK_" + job["week_start"].replace("-", "") + "_"
    digest = _contents(main_cells)
    name = f"{prefix}R{job['head_revision']}_{digest}"
    owned = [r for r in book.get("namedRanges", []) if r.get("name", "").startswith(prefix)]
    rect = {"sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0, "endRowIndex": plan["rows"], "endColumnIndex": plan["columns"]}
    add_marker = {"addNamedRange": {"namedRange": {"name": name, "range": rect}}}
    if not old:
        if owned:
            raise DeliveryConflict("missing_owned_sheet")
        return [raw[0], *writes, add_marker]
    if len(owned) != 1 or owned[0]["range"].get("sheetId") != sid:
        raise DeliveryConflict("ownership_mismatch")
    marker = owned[0]
    match = re.fullmatch(re.escape(prefix) + r"R(\d+)_([a-f0-9]{64})", marker["name"])
    if not match or not marker.get("namedRangeId") or marker["range"].get("startRowIndex", 0) or marker["range"].get("startColumnIndex", 0):
        raise DeliveryConflict("ownership_mismatch")
    previous_revision = int(match[1])
    if previous_revision > job["head_revision"]:
        raise DeliveryConflict("newer_remote_revision")
    grid = old["gridProperties"]
    old_rect = marker["range"]
    visible_rows = min(grid["rowCount"], max(old_rect["endRowIndex"], plan["rows"]))
    visible_cols = min(grid["columnCount"], max(old_rect["endColumnIndex"], plan["columns"]))
    inspected = client.sheet_grid_range(target, old["title"], f"A1:{column_name(visible_cols-1)}{visible_rows}")
    data = inspected["sheets"][0]
    if data["properties"]["sheetId"] != sid:
        raise DeliveryConflict("ownership_mismatch")
    rows = (data.get("data") or [{}])[0].get("rowData") or []
    previous = [{"values": row.get("values", [])[:old_rect["endColumnIndex"]]} for row in rows[:old_rect["endRowIndex"]]]
    if _contents(previous) != match[2]:
        raise DeliveryConflict("managed_cells_edited")
    for y, row in enumerate(rows[:plan["rows"]]):
        for x, cell in enumerate(row.get("values", [])[:plan["columns"]]):
            if (x >= old_rect["endColumnIndex"] or y >= old_rect["endRowIndex"]) and (cell.get("userEnteredValue") or cell.get("note")):
                raise DeliveryConflict("occupied_expansion")
    if previous_revision == job["head_revision"]:
        if marker["name"] != name:
            raise DeliveryConflict("revision_content_mismatch")
        return []
    # Consuming Google's previous namedRangeId fences a delayed batch. A stale
    # delete fails validation, so no cell in that atomic batch is changed.
    changes = [{"deleteNamedRange": {"namedRangeId": marker["namedRangeId"]}}]
    for key in ("rowCount", "columnCount"):
        needed = plan["rows" if key == "rowCount" else "columns"]
        if grid[key] < needed:
            changes.append({"appendDimension": {"sheetId": sid, "dimension": "ROWS" if key == "rowCount" else "COLUMNS", "length": needed-grid[key]}})
    changes.extend([{"unmergeCells": {"range": old_rect}},
                    {"updateCells": {"range": old_rect, "fields": "userEnteredValue,userEnteredFormat,note"}}])
    return [*changes, *writes, add_marker]


def deliver_one(database, client_factory, now=None):
    now = int(time.time()) if now is None else now
    token = uuid4().hex
    with database.connection() as conn:
        due = """((state IN ('pending','retry') AND next_attempt<=?) OR (state='working' AND lease_until<=?))"""
        job = database.execute(conn, f"""SELECT j.*,b.sheet_id FROM published_week_sheet_jobs j
            JOIN published_week_sheet_bindings b USING(target_id,week_start) WHERE {due}
            ORDER BY next_attempt,head_revision DESC LIMIT 1""", (now, now)).fetchone()
        if not job:
            return False
        job = dict(job)
        key = (job["target_id"], job["publication_id"])
        locked = database.execute(conn, f"""UPDATE published_week_sheet_jobs SET state='working', attempts=attempts+1,
            lease_until=?,lease_token=? WHERE target_id=? AND publication_id=? AND {due}""", (now+120, token, *key, now, now))
        if not locked.rowcount:
            return False
    state, error = "delivered", None
    try:
        def current():
            with database.connection() as conn:
                return database.execute(conn, """SELECT 1 FROM schedule_publication_heads h JOIN published_week_sheet_jobs j
                    ON h.scope_key=? AND h.revision=j.head_revision WHERE j.target_id=? AND j.publication_id=? AND j.lease_token=?""",
                    ("week:" + job["week_start"], *key, token)).fetchone() is not None
        if current():
            plan = _plan(database, job)
            client = client_factory()
            batch = _batch(job, plan, client.weekly_spreadsheet(job["target_id"]), client)
            if batch and current():
                client.batch_update_spreadsheet(job["target_id"], batch)
            if current() and _batch(job, plan, client.weekly_spreadsheet(job["target_id"]), client):
                raise RuntimeError("delivery_not_confirmed")
        else:
            state = "superseded"
    except DeliveryConflict as exc:
        state, error = "blocked", str(exc)
    except Exception:
        # Remote exceptions may contain credentials or workbook data.
        state, error = "retry", "delivery_unconfirmed"
    with database.connection() as conn:
        updated = database.execute(conn, """UPDATE published_week_sheet_jobs SET state=?,error_code=?,next_attempt=?,
            lease_token=NULL,lease_until=0 WHERE target_id=? AND publication_id=? AND lease_token=?""",
            (state, error, now + min(3600, 5 * 2 ** min(job["attempts"], 10)), *key, token))
        if updated.rowcount and state == "delivered":
            database.execute(conn, """UPDATE published_week_sheet_bindings SET delivered_revision=?
                WHERE target_id=? AND week_start=? AND delivered_revision<?""",
                (job["head_revision"], job["target_id"], job["week_start"], job["head_revision"]))
    return True
