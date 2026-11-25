"""Read-only workload summaries derived from the published teacher projection."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from backend.database import Database
from backend.services.canonical_schedule import (
    PUBLISHED_STATUSES, _NO_LESSON, _assignments, _block_rows,
    _week_start, display_room, resolve_published_week,
)


def _minutes(start_time: Any, end_time: Any) -> int | None:
    if not start_time or not end_time:
        return None
    try:
        start = datetime.strptime(str(start_time), "%H:%M")
        end = datetime.strptime(str(end_time), "%H:%M")
    except ValueError:
        return None
    value = int((end - start).total_seconds() // 60)
    return value if value > 0 else None


def _teacher_rows(database: Database, teacher_id: str | None) -> list[dict[str, Any]]:
    query = "SELECT id,display_name,status FROM identities WHERE kind='teacher'"
    params: tuple[Any, ...] = ()
    if teacher_id:
        query += " AND id=?"
        params = (teacher_id,)
    query += " ORDER BY id"
    with database.connection() as connection:
        return [dict(row) for row in database.execute(connection, query, params).fetchall()]


def teacher_load_analytics(database: Database, start_day: str, end_day: str | None = None,
                           teacher_id: Any | None = None, *, activity_type: str | None = None,
                           activity_format: str | None = None, group_id: str | None = None) -> dict[str, Any]:
    """Return reproducible minutes and role-separated details for published weeks.

    The source is the same canonical teacher projection used by Campus. Draft,
    superseded, cancelled, lunch, and no-lesson rows are therefore excluded by
    the publication/visibility contract before aggregation. Unknown time
    intervals remain visible in ``unknown_duration_items`` instead of becoming
    zero-minute lessons.
    """
    finish = end_day or start_day
    first, last = date.fromisoformat(start_day), date.fromisoformat(finish)
    if last < first:
        raise ValueError("end_day must not precede start_day")
    rows = _teacher_rows(database, str(teacher_id) if teacher_id is not None else None)
    teachers: list[dict[str, Any]] = []
    published_teacher_count = 0
    projections = _batch_teacher_projections(database, [str(row["id"]) for row in rows], start_day, finish)
    for row in rows:
        projection = projections[str(row["id"])]
        status = projection.get("status")
        if status in PUBLISHED_STATUSES:
            published_teacher_count += 1
        summary = {
            "teacher_id": str(row["id"]),
            "teacher_name": row.get("display_name"),
            "status": status,
            "lesson_count": 0,
            "scheduled_minutes": 0,
            "lead_minutes": 0,
            "responsible_minutes": 0,
            "joint_minutes": 0,
            "unknown_duration_items": 0,
            "days": [],
            "activity_minutes": {},
            "items": [],
            "coverage": projection.get("publications") or [],
            "coverage_status": projection.get("coverage_status", "unavailable"),
        }
        day_values: set[str] = set()
        day_items: dict[str, list[dict[str, Any]]] = {}
        activity_minutes: Counter[str] = Counter()
        for item in projection.get("items") or []:
            if activity_type and str(item.get("activity_type") or "lesson") != activity_type:
                continue
            if activity_format and str(item.get("activity_format") or "") != activity_format:
                continue
            if group_id and str(group_id) not in {str(value) for value in item.get("audience_group_ids") or []}:
                continue
            duration = _minutes(item.get("start_time"), item.get("end_time"))
            is_responsible = bool(item.get("responsible_teacher_ids")) and not bool(item.get("physical_presence"))
            role = "responsible" if is_responsible else "lead"
            if duration is None:
                summary["unknown_duration_items"] += 1
            else:
                summary["scheduled_minutes"] += duration
                summary[f"{role}_minutes"] += duration
                if (item.get("provenance") or {}).get("joint_activity_id"):
                    summary["joint_minutes"] += duration
                activity_minutes[str(item.get("activity_type") or "lesson")] += duration
            day = str(item.get("date") or "")
            if day:
                day_values.add(day)
                day_items.setdefault(day, []).append(item)
            detail = {
                "date": day,
                "start_time": item.get("start_time"),
                "end_time": item.get("end_time"),
                "activity": item.get("activity"),
                "activity_type": item.get("activity_type", "lesson"),
                "role": role,
                "duration_minutes": duration,
                "audiences": item.get("audiences") or [],
                "room": item.get("room"),
                "activity_format": item.get("activity_format"),
                "delivery_mode": item.get("delivery_mode"),
                "audience_group_ids": item.get("audience_group_ids") or [],
                "grade_scope": item.get("grade_scope"),
            }
            if item.get("provenance"):
                detail["provenance"] = item["provenance"]
            summary["items"].append(detail)
        summary["lesson_count"] = len(summary["items"])
        summary["days"] = sorted(day_values)
        summary["activity_minutes"] = dict(sorted(activity_minutes.items()))
        summary["day_summaries"] = _day_summaries(day_items)
        if summary["coverage_status"] != "complete":
            # Numeric totals over a sparse or entirely unpublished period are
            # not complete totals. Keep the observed partial values explicit
            # instead of presenting them as school-wide zeroes/totals.
            for field in ("lesson_count", "scheduled_minutes", "lead_minutes",
                          "responsible_minutes", "joint_minutes",
                          "unknown_duration_items", "days", "activity_minutes",
                          "day_summaries"):
                summary[f"observed_{field}"] = summary[field]
                summary[field] = None
        teachers.append(summary)
    return {
        "status": "published" if published_teacher_count else "canonical_not_materialized",
        "start_day": start_day,
        "end_day": finish,
        "teachers": teachers,
        "coverage": {"teacher_count": len(teachers), "published_teacher_count": published_teacher_count},
        "source": "canonical_teacher_projection",
    }


def _batch_teacher_projections(database: Database, teacher_ids: list[str], start_day: str,
                               end_day: str) -> dict[str, dict[str, Any]]:
    """Read shared published weeks once, then fan out rows in memory.

    This is intentionally a workload-specific reader: its query count depends
    on published weeks/blocks, not on the number of teachers in the roster.
    """
    first, last = date.fromisoformat(start_day), date.fromisoformat(end_day)
    with database.connection() as connection:
        group_rows = database.execute(connection, "SELECT id,name,display_name FROM groups WHERE canonical IS TRUE ORDER BY id").fetchall()
        teacher_rows = database.execute(connection, "SELECT id,display_name FROM identities WHERE kind='teacher' AND id IN ({})".format(",".join("?" for _ in teacher_ids)), tuple(teacher_ids)).fetchall() if teacher_ids else []
    groups = {str(row["id"]): str(row["display_name"] or row["name"]) for row in group_rows}
    names = {str(row["id"]): str(row["display_name"]) for row in teacher_rows}
    result = {teacher_id: {"status": "canonical_not_materialized", "items": [], "publications": [], "coverage": [],
                           "requested_week_count": 0, "published_week_count": 0,
                           "coverage_status": "unavailable"} for teacher_id in teacher_ids}
    seen_weeks: set[str] = set()
    cursor = first
    while cursor <= last:
        week_start = _week_start(cursor.isoformat())
        if week_start in seen_weeks:
            cursor += timedelta(days=1)
            continue
        seen_weeks.add(week_start)
        publication = resolve_published_week(database, week_start)
        for teacher_id in teacher_ids:
            result[teacher_id]["publications"].append(dict(publication.metadata))
            result[teacher_id]["requested_week_count"] += 1
            if publication.status in PUBLISHED_STATUSES:
                result[teacher_id]["published_week_count"] += 1
        effective = publication.effective
        if effective and publication.status in PUBLISHED_STATUSES:
            blocks = _block_rows(database, effective["effective_week_id"])
            per_teacher: dict[str, list[dict[str, Any]]] = {teacher_id: [] for teacher_id in teacher_ids}
            per_seen: dict[str, set[tuple[Any, ...]]] = {teacher_id: set() for teacher_id in teacher_ids}
            per_joint: dict[str, dict[tuple[str, str, str, str], dict[str, Any]]] = {teacher_id: {} for teacher_id in teacher_ids}
            week_start_date = date.fromisoformat(week_start)
            for block in blocks:
                if block.get("weekday") is None or block.get("change_kind") == "cancelled":
                    continue
                day = (week_start_date + timedelta(days=int(block["weekday"]))).isoformat()
                for assignment in _assignments(database, block.get("canonical_block_id"), block.get("patch") or {}):
                    metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), dict) else {}
                    if (metadata.get("day_plan_mode") == "sdep" or metadata.get("special_mark") in {"lunch", "no_lesson"}
                            or metadata.get("activity_type") == "lunch" or assignment.get("activity") == _NO_LESSON):
                        continue
                    teacher_values = [str(value) for value in assignment.get("teacher_ids") or []]
                    responsible = [str(value) for value in metadata.get("responsible_teacher_ids") or []] if metadata.get("activity_format") == "independent_practice" else []
                    group_ids = [str(value) for value in assignment.get("canonical_group_ids") or []]
                    for teacher_id in teacher_ids:
                        if teacher_id not in teacher_values and teacher_id not in responsible:
                            continue
                        signature = (day, str(block.get("start_time") or ""), str(block.get("end_time") or ""), str(assignment.get("activity") or ""), display_room(metadata.get("room")) or "", tuple(sorted(group_ids)))
                        if signature in per_seen[teacher_id]:
                            continue
                        per_seen[teacher_id].add(signature)
                        item = {"date": day, "weekday": int(block["weekday"]), "start_time": block.get("start_time"), "end_time": block.get("end_time"), "activity": assignment.get("activity"), "audience_group_ids": group_ids, "audiences": [groups.get(group_id, group_id) for group_id in group_ids], "grade_scope": block.get("grade_scope"), "teacher_ids": teacher_values, "teacher_names": metadata.get("teachers") or [names.get(value, value) for value in teacher_values], "room": display_room(metadata.get("room")), "provenance": {"block_key": block["block_key"], "source_provenance": block.get("source_provenance") or {}}}
                        item["activity_type"] = metadata.get("activity_type", "lesson")
                        if metadata.get("day_plan_mode") == "online":
                            item.update(delivery_mode="online", room="", connection_info=(metadata.get("constructor_details") or {}).get("connectionInfo", ""))
                        if metadata.get("activity_format") == "independent_practice":
                            item.update(activity_format="independent_practice", responsible_teacher_ids=responsible, responsible_teachers=metadata.get("responsible_teachers") or [], physical_presence=teacher_id in teacher_values)
                        joint_id = metadata.get("joint_activity_id") if metadata.get("audience_coverage") == "explicit" else None
                        if joint_id:
                            item["provenance"]["joint_activity_id"] = str(joint_id)
                            joint_key = (day, str(block.get("start_time")), str(block.get("end_time")), str(joint_id))
                            previous = per_joint[teacher_id].get(joint_key)
                            if previous:
                                previous["audience_group_ids"] = sorted(set(previous["audience_group_ids"]) | set(group_ids))
                                previous["audiences"] = [groups.get(value, value) for value in previous["audience_group_ids"]]
                                continue
                            per_joint[teacher_id][joint_key] = item
                        per_teacher[teacher_id].append(item)
            for teacher_id in teacher_ids:
                result[teacher_id]["status"] = publication.status
                result[teacher_id]["coverage"].append(dict(publication.metadata))
                result[teacher_id]["items"].extend(per_teacher[teacher_id])
        cursor += timedelta(days=1)
    for teacher_id in teacher_ids:
        requested = result[teacher_id]["requested_week_count"]
        published = result[teacher_id]["published_week_count"]
        result[teacher_id]["coverage_status"] = (
            "complete" if requested and published == requested else
            "partial" if published else "unavailable"
        )
    return result


def _day_summaries(day_items: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result = []
    for day, items in sorted(day_items.items()):
        intervals = [(str(item.get("start_time")), str(item.get("end_time")), _minutes(item.get("start_time"), item.get("end_time")))
                     for item in items if item.get("start_time") and item.get("end_time")]
        valid = [(start, end, minutes) for start, end, minutes in intervals if minutes is not None]
        if not valid:
            result.append({"date": day, "first_start": None, "last_end": None, "scheduled_minutes": 0, "window_minutes": None, "gap_minutes": None})
            continue
        first_start = min(start for start, _, _ in valid)
        last_end = max(end for _, end, _ in valid)
        window = _minutes(first_start, last_end)
        scheduled = sum(minutes for _, _, minutes in valid)
        result.append({"date": day, "first_start": first_start, "last_end": last_end,
                       "scheduled_minutes": scheduled, "window_minutes": window,
                       "gap_minutes": max(0, window - scheduled) if window is not None else None})
    return result
