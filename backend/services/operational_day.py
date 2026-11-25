"""Admin read models for one published school day and the independent planning card."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from backend.database import Database
from backend.services import canonical_schedule as canonical
from backend.services.constructor_publication import preview_publication
from backend.services.schedule_rooms import display_room, room_key


def school_day(now: datetime | None = None) -> tuple[date, str]:
    zone = ZoneInfo(os.environ.get("SCHOOL_TIMEZONE", "Europe/Moscow"))
    return (now or datetime.now(timezone.utc)).astimezone(zone).date(), zone.key


def _record_id(*parts) -> str:
    return sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _occurrence(assignment, block, index):
    meta = assignment.get("metadata") or {}
    mode = meta.get("day_plan_mode") or "normal"
    activity = assignment.get("activity")
    kind = meta.get("activity_type") or "lesson"
    if activity == "NO_LESSON" or kind == "lunch" or meta.get("special_mark") in {"no_lesson", "lunch"}:
        return None
    room = display_room(meta.get("room"))
    if mode in {"sdep", "online"} or room_key(room) in {"online", "онлайн"}:
        room = ""
    start, end = str(block.get("start_time") or ""), str(block.get("end_time") or "")
    start_min, end_min = canonical._clock_minutes(start), canonical._clock_minutes(end)
    teachers = sorted({str(x) for x in assignment.get("teacher_ids") or []}) if mode != "sdep" else []
    group_ids = sorted(set(canonical._assignment_group_ids(assignment)))
    responsible = sorted({str(x) for x in meta.get("responsible_teacher_ids") or []})
    joint = meta.get("joint_activity_id") if meta.get("audience_coverage") == "explicit" else None
    origin = meta.get("constructor_lesson_id")
    key = _record_id("joint", joint, start, end, "cancelled" if block.get("change_kind") == "cancelled" else "scheduled") if joint else _record_id("assignment", block["block_key"], origin or index)
    return {"id": key, "activity": activity, "activity_type": kind, "delivery_mode": mode,
            "teacher_ids": teachers, "responsible_teacher_ids": responsible,
            "group_ids": group_ids, "grade_scope": str(block.get("grade_scope") or "") or None,
            "activity_format": meta.get("activity_format"), "joint_activity_id": joint,
            "room": room or None, "start": start, "end": end,
            "duration_minutes": end_min - start_min if start_min is not None and end_min is not None and end_min > start_min else None,
            "lesson_id": origin, "lesson_ids": [origin] if origin else [], "block_keys": [block["block_key"]], "change": "unchanged"}


def _overlaps(occurrences):
    resources = defaultdict(list)
    for item in occurrences:
        if item["change"] == "cancelled" or item["duration_minutes"] is None:
            continue
        if item["room"]:
            resources[("room", room_key(item["room"]))].append(item)
        for teacher in item["teacher_ids"]:
            resources[("teacher", teacher)].append(item)
    alerts = []
    for (kind, resource), items in resources.items():
        active = []
        for current in sorted(items, key=lambda x: (canonical._clock_minutes(x["start"]), x["id"])):
            start = canonical._clock_minutes(current["start"])
            active = [other for other in active if canonical._clock_minutes(other["end"]) > start]
            for other in active:
                ids = sorted([current["id"], other["id"]])
                alerts.append({"code": kind + "_overlap", "id": _record_id(kind, resource, ids),
                               "resource": resource, "occurrence_ids": ids})
            active.append(current)
    return alerts


def _decorate_directory(database, occurrences):
    groups = sorted({key for row in occurrences for key in row.get("group_ids", [])})
    teacher_fields = ("teacher_ids", "responsible_teacher_ids", "previous_teacher_ids")
    teachers = sorted({key for row in occurrences for field in teacher_fields for key in row.get(field, [])})
    labels = {}
    with database.connection() as connection:
        for keys, table, fields in (
            (groups, "groups", "COALESCE(display_name,name) AS name,group_type,base_class_name"),
            (teachers, "identities", "display_name AS name,status"),
        ):
            labels[table] = {}
            for offset in range(0, len(keys), 500):
                batch = keys[offset:offset + 500]
                placeholders = ",".join("?" for _ in batch)
                rows = database.execute(connection, f"""SELECT CAST(id AS TEXT) AS id,{fields}
                    FROM {table} WHERE CAST(id AS TEXT) IN ({placeholders})""", tuple(batch)).fetchall()
                labels[table].update({row["id"]: dict(row) for row in rows})
    for row in occurrences:
        row["groups"] = [dict(labels["groups"].get(key, {"id": key, "name": None})) for key in row.get("group_ids", [])]
        for field in teacher_fields:
            if field in row:
                row[field.replace("teacher_ids", "teachers")] = [
                    labels["identities"].get(key, {"id": key, "name": None}) for key in row[field]]


def _baseline_intervals(database, version_id):
    with database.connection() as connection:
        rows = database.execute(connection, """SELECT id AS canonical_block_id,block_key,weekday,start_time,end_time
            FROM canonical_schedule_blocks WHERE version_id=?""", (version_id,)).fetchall()
    return {str(row["canonical_block_id"]): dict(row) for row in rows}


def _cross_block_moves(database, blocks, monday):
    """Pair explicit lesson lineage when the overlay cancels and adds different block keys."""
    changed = {"cancelled": defaultdict(list), "added": defaultdict(list)}
    for block in blocks:
        change = block["change_kind"]
        if change not in changed or block.get("weekday") is None:
            continue
        patch = block.get("patch") if change == "added" else None
        for index, assignment in enumerate(canonical._assignments(database, block["canonical_block_id"], patch)):
            item = _occurrence(assignment, block, index)
            if item and item["lesson_id"]:
                interval = {"day": (monday + timedelta(days=block["weekday"])).isoformat(),
                            "start": item["start"], "end": item["end"]}
                changed[change][item["lesson_id"]].append(interval)
    pairs = {}
    for lesson_id, previous in changed["cancelled"].items():
        current = changed["added"].get(lesson_id, [])
        if len(previous) == len(current) == 1 and previous[0] != current[0]:
            pairs[lesson_id] = previous[0], current[0]
    return pairs


def operational_day(database: Database, selected: date, zone: str) -> dict:
    edition = canonical.resolve_published_week(database, selected.isoformat())
    monday = date.fromisoformat(edition.metadata["week_start"])
    edition_id = edition.metadata["effective_week_id"]
    items = {}
    alerts = []
    if edition.effective:
        blocks = canonical._block_rows(database, edition_id)
        canonical._prefetch_block_assignments(database, blocks)
        previous_blocks = None
        cross_block_moves = _cross_block_moves(database, blocks, monday)
        for block in blocks:
            if block.get("weekday") != selected.weekday():
                continue
            change = block["change_kind"]
            assignments = canonical._assignments(database, block["canonical_block_id"],
                                                   None if change == "cancelled" else block.get("patch"))
            baseline = canonical._assignments(database, block["canonical_block_id"]) if change == "replaced" else []
            for index, assignment in enumerate(assignments):
                item = _occurrence(assignment, block, index)
                if not item:
                    continue
                if change == "replaced":
                    matches = [a for a in baseline if item["lesson_id"] and (a.get("metadata") or {}).get("constructor_lesson_id") == item["lesson_id"]]
                    if not matches and len(baseline) == len(assignments) == 1:
                        matches = baseline
                    if previous_blocks is None:
                        previous_blocks = _baseline_intervals(database, edition.metadata["base_version_id"])
                    base_block = previous_blocks.get(str(block["canonical_block_id"]))
                    previous = _occurrence(matches[0], base_block, index) if len(matches) == 1 and base_block else None
                    if previous is None:
                        item["change"] = "unmatched"
                    else:
                        changes = []
                        if (base_block["weekday"], previous["start"], previous["end"]) != (block["weekday"], item["start"], item["end"]):
                            changes.append("moved")
                            item["previous_interval"] = {
                                "day": (monday + timedelta(days=base_block["weekday"])).isoformat(),
                                "start": previous["start"], "end": previous["end"]}
                        if previous["teacher_ids"] != item["teacher_ids"]:
                            changes.append("teacher_replaced")
                            item["previous_teacher_ids"] = previous["teacher_ids"]
                        if any(previous[k] != item[k] for k in ("activity", "activity_type", "delivery_mode", "room")):
                            changes.append("changed")
                        if changes:
                            item.update(change=changes[0], changes=changes)
                elif change in {"cancelled", "added"}:
                    item["change"] = change
                    move = cross_block_moves.get(item["lesson_id"])
                    if move and change == "added":
                        item.update(change="moved", changes=["moved"], previous_interval=move[0])
                    elif move:
                        item["replacement_interval"] = move[1]
                existing = items.get(item["id"])
                if existing:
                    for field in ("block_keys", "teacher_ids", "responsible_teacher_ids", "previous_teacher_ids",
                                  "group_ids", "lesson_ids", "changes"):
                        if field in item:
                            existing[field] = sorted(set(existing.get(field, [])) | set(item[field]))
                    if existing["change"] == "unchanged":
                        existing["change"] = item["change"]
                    if "previous_interval" in item:
                        existing.setdefault("previous_interval", item["previous_interval"])
                else:
                    items[item["id"]] = item
        _decorate_directory(database, items.values())
        for record in (edition.source or {}).get("records", []):
            if record.get("status") == "unresolved" and record.get("day") == selected.weekday():
                alerts.append({"code": "deferred_lesson", "id": _record_id("deferred", record["lesson_id"]),
                               "lesson_id": record["lesson_id"], "start": record.get("start"), "end": record.get("end"),
                               "reasons": record.get("diagnostic_codes") or []})
        alerts.extend(_overlaps(items.values()))
    else:
        alerts.append({"code": edition.metadata["origin"] if edition.metadata["origin"] == "broken_head" else "no_published_week",
                       "id": _record_id("no_publication", selected.isoformat())})
    for row in items.values():
        for group in row["groups"]:
            if group["name"]:
                group["href"] = "/api/admin/groups/" + quote(group["id"], safe="")
    for row in [*items.values(), *alerts]:
        params = {"day": selected.isoformat(), "edition": edition_id or "unpublished", "record": row["id"]}
        row["href"] = "/api/admin/operations/day?" + urlencode(params)
    scheduled = [item for item in items.values() if item["change"] != "cancelled"]
    return {"day": selected.isoformat(), "timezone": zone, "week_days": [(monday + timedelta(days=i)).isoformat() for i in range(7)],
            "publication": edition.metadata, "status": edition.status,
            "scheduled_count": len(scheduled) if edition.effective else None,
            "rooms": {"catalog_coverage": "unknown", "occupied": sorted({room_key(x["room"]) for x in scheduled if x["room"]})},
            "items": sorted(items.values(), key=lambda x: (canonical._clock_minutes(x["start"]) if x["duration_minutes"] else 1440, x["id"])),
            "alerts": alerts}


def next_week_drafts(source: Database, store: Database, selected: date, draft_id: str | None = None):
    week = selected + timedelta(days=7-selected.weekday())
    # Read only matching non-archived school variants and only the summary columns.
    predicate = ("payload->>'week_start'=? AND payload->>'draft_kind'='school' AND payload->>'draft_archive' IS NULL"
                 if store.database_url else
                 "json_extract(payload,'$.week_start')=? AND json_extract(payload,'$.draft_kind')='school' AND json_extract(payload,'$.draft_archive') IS NULL")
    with store.connection() as conn:
        rows = store.execute(conn, f"SELECT id,name,revision FROM schedule_experiment_drafts WHERE {predicate} ORDER BY updated_at DESC,id",
                             (week.isoformat(),)).fetchall()
    drafts = [{**dict(r), "readiness": "requires_preview", "href": "/admin/schedule/constructor?" + urlencode({"draft": r["id"]})} for r in rows]
    if draft_id or len(drafts) == 1:
        target = next((r for r in drafts if not draft_id or r["id"] == draft_id), None)
        if not target:
            raise KeyError("Unknown draft for selected week")
        preview = preview_publication(source, target["id"], scope_kind="week", week_start=week.isoformat(),
                                      expected_revision=target["revision"], draft_database=store)
        target["can_publish"] = preview["can_publish"]
        target["readiness"] = "ready" if preview["can_publish"] else "blocked"
        target["blockers"] = [{"code": x.get("code"), "lesson_id": x.get("lesson_id")} for x in preview["blockers"]]
        target["deferred_count"] = len(preview["deferred"])
    return {"week_start": week.isoformat(), "kind": "draft", "items": drafts}
