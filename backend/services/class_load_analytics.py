"""Class workload summaries derived from published canonical teacher intervals."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any

from backend.database import Database
from backend.services.canonical_schedule import PUBLISHED_STATUSES, project_teacher_range


def _duration(start_time: Any, end_time: Any) -> int | None:
    try:
        start = datetime.strptime(str(start_time), "%H:%M")
        end = datetime.strptime(str(end_time), "%H:%M")
    except (TypeError, ValueError):
        return None
    value = int((end - start).total_seconds() // 60)
    return value if value > 0 else None


def class_load_analytics(database: Database, start_day: str, end_day: str | None = None) -> dict[str, Any]:
    """Group published teacher intervals by canonical audience group.

    A class receives one interval per canonical group even when multiple
    teachers co-teach it. Explicit joint activities are deduplicated by their
    provenance ID, so parallel teacher streams do not inflate class duration.
    """
    finish = end_day or start_day
    first, last = date.fromisoformat(start_day), date.fromisoformat(finish)
    if last < first:
        raise ValueError("end_day must not precede start_day")
    with database.connection() as connection:
        groups = {str(row["id"]): dict(row) for row in database.execute(
            connection, "SELECT id,name,display_name,group_type FROM groups WHERE canonical IS TRUE ORDER BY id"
        ).fetchall()}
        teachers = [dict(row) for row in database.execute(
            connection, "SELECT id FROM identities WHERE kind='teacher' AND status='active' ORDER BY id"
        ).fetchall()]

    grouped: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str, str, str, str, str]] = set()
    published_weeks = 0
    for teacher in teachers:
        projection = project_teacher_range(database, teacher["id"], start_day, finish)
        if projection.get("status") not in PUBLISHED_STATUSES:
            continue
        published_weeks += 1
        for item in projection.get("items") or []:
            duration = _duration(item.get("start_time"), item.get("end_time"))
            provenance = item.get("provenance") or {}
            joint_id = str(provenance.get("joint_activity_id") or "")
            group_ids = [str(value) for value in item.get("audience_group_ids") or []]
            for group_id in group_ids:
                if group_id not in groups:
                    continue
                signature = (group_id, str(item.get("date") or ""), str(item.get("start_time") or ""),
                             str(item.get("end_time") or ""), str(item.get("activity") or ""), joint_id)
                if signature in seen:
                    continue
                seen.add(signature)
                summary = grouped.setdefault(group_id, {
                    "group_id": group_id,
                    "group_name": groups[group_id].get("display_name") or groups[group_id].get("name"),
                    "group_type": groups[group_id].get("group_type"),
                    "lesson_count": 0,
                    "scheduled_minutes": 0,
                    "unknown_duration_items": 0,
                    "days": set(),
                    "activity_minutes": Counter(),
                    "items": [],
                })
                if duration is None:
                    summary["unknown_duration_items"] += 1
                else:
                    summary["scheduled_minutes"] += duration
                    summary["activity_minutes"][str(item.get("activity_type") or "lesson")] += duration
                day = str(item.get("date") or "")
                if day:
                    summary["days"].add(day)
                summary["items"].append({
                    "date": day, "start_time": item.get("start_time"), "end_time": item.get("end_time"),
                    "activity": item.get("activity"), "activity_type": item.get("activity_type", "lesson"),
                    "duration_minutes": duration, "joint_activity_id": joint_id or None,
                })
    output = []
    for summary in grouped.values():
        summary["lesson_count"] = len(summary["items"])
        summary["days"] = sorted(summary["days"])
        summary["activity_minutes"] = dict(sorted(summary["activity_minutes"].items()))
        output.append(summary)
    output.sort(key=lambda item: (str(item["group_name"]), item["group_id"]))
    return {
        "status": "published" if published_weeks else "canonical_not_materialized",
        "start_day": start_day,
        "end_day": finish,
        "groups": output,
        "coverage": {"group_count": len(output), "published_teacher_projections": published_weeks},
        "source": "canonical_teacher_projection",
    }
