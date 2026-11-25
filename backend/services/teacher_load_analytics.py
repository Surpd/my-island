"""Read-only workload summaries derived from the published teacher projection."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any

from backend.database import Database
from backend.services.canonical_schedule import PUBLISHED_STATUSES, project_teacher_range


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
    for row in rows:
        projection = project_teacher_range(database, row["id"], start_day, finish)
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
        teachers.append(summary)
    return {
        "status": "published" if published_teacher_count else "canonical_not_materialized",
        "start_day": start_day,
        "end_day": finish,
        "teachers": teachers,
        "coverage": {"teacher_count": len(teachers), "published_teacher_count": published_teacher_count},
        "source": "canonical_teacher_projection",
    }


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
