"""Class workload summaries derived from published canonical teacher intervals."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any

from backend.database import Database
from backend.services.canonical_schedule import PUBLISHED_STATUSES
from backend.services.teacher_load_analytics import _batch_teacher_projections


def _duration(start_time: Any, end_time: Any) -> int | None:
    try:
        start = datetime.strptime(str(start_time), "%H:%M")
        end = datetime.strptime(str(end_time), "%H:%M")
    except (TypeError, ValueError):
        return None
    value = int((end - start).total_seconds() // 60)
    return value if value > 0 else None


def class_load_analytics(database: Database, start_day: str, end_day: str | None = None, *,
                         group_id: str | None = None, group_type: str | None = None,
                         trajectory: str | None = None) -> dict[str, Any]:
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
            connection, "SELECT id,name,display_name,group_type,subject,base_class_name,subject_subgroup,exam_track FROM groups WHERE canonical IS TRUE ORDER BY id"
        ).fetchall()}
        teachers = [dict(row) for row in database.execute(
            connection, "SELECT id FROM identities WHERE kind='teacher' AND status='active' ORDER BY id"
        ).fetchall()]

    grouped: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str, str, str, str, str]] = set()
    published_weeks = 0
    publication_coverage: dict[str, dict[str, Any]] = {}
    projections = _batch_teacher_projections(database, [str(teacher["id"]) for teacher in teachers], start_day, finish)
    for teacher in teachers:
        projection = projections[str(teacher["id"])]
        for metadata in projection.get("publications") or []:
            publication_coverage[str(metadata.get("week_start"))] = metadata
        if projection.get("status") not in PUBLISHED_STATUSES:
            continue
        published_weeks += 1
        for item in projection.get("items") or []:
            duration = _duration(item.get("start_time"), item.get("end_time"))
            day = str(item.get("date") or "")
            provenance = item.get("provenance") or {}
            joint_id = str(provenance.get("joint_activity_id") or "")
            group_ids = [str(value) for value in item.get("audience_group_ids") or []]
            for audience_group_id in group_ids:
                if audience_group_id not in groups:
                    continue
                group = groups[audience_group_id]
                if group_id and group_id != str(group["id"]):
                    continue
                if group_type and str(group.get("group_type") or "") != group_type:
                    continue
                trajectory_value = "/".join(str(group.get(key) or "") for key in ("base_class_name", "subject", "subject_subgroup", "exam_track") if group.get(key))
                if trajectory and trajectory_value != trajectory:
                    continue
                signature = (audience_group_id, str(item.get("date") or ""), str(item.get("start_time") or ""),
                             str(item.get("end_time") or ""), str(item.get("activity") or ""), joint_id)
                if signature in seen:
                    continue
                seen.add(signature)
                summary = grouped.setdefault(audience_group_id, {
                    "group_id": audience_group_id,
                    "group_name": group.get("display_name") or group.get("name"),
                    "group_type": group.get("group_type"),
                    "trajectory": trajectory_value,
                    "lesson_count": 0,
                    "scheduled_minutes": 0,
                    "unknown_duration_items": 0,
                    "days": set(),
                    "activity_minutes": Counter(),
                    "items": [],
                    "day_intervals": {},
                })
                if duration is None:
                    summary["unknown_duration_items"] += 1
                else:
                    summary["scheduled_minutes"] += duration
                    summary["activity_minutes"][str(item.get("activity_type") or "lesson")] += duration
                    summary["day_intervals"].setdefault(day, []).append((item.get("start_time"), item.get("end_time")))
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
        summary["day_summaries"] = _day_summaries(summary.pop("day_intervals"))
        summary["scheduled_minutes"] = sum(item["occupied_minutes"] for item in summary["day_summaries"])
        output.append(summary)
    output.sort(key=lambda item: (str(item["group_name"]), item["group_id"]))
    return {
        "status": "published" if published_weeks else "canonical_not_materialized",
        "start_day": start_day,
        "end_day": finish,
        "groups": output,
        "coverage": {"group_count": len(output), "published_teacher_projections": published_weeks,
                     "weeks": [publication_coverage[key] for key in sorted(publication_coverage)]},
        "source": "canonical_teacher_projection",
    }


def _clock(value: str) -> int:
    parsed = datetime.strptime(value, "%H:%M")
    return parsed.hour * 60 + parsed.minute


def _day_summaries(day_intervals: dict[str, list[tuple[Any, Any]]]) -> list[dict[str, Any]]:
    result = []
    for day, raw_intervals in sorted(day_intervals.items()):
        intervals = sorted((_clock(str(start)), _clock(str(end))) for start, end in raw_intervals if start and end)
        merged: list[list[int]] = []
        for start, end in intervals:
            if end <= start:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        if not merged:
            result.append({"date": day, "first_start": None, "last_end": None, "occupied_minutes": 0, "window_minutes": None, "gap_minutes": None})
            continue
        occupied = sum(end - start for start, end in merged)
        window = merged[-1][1] - merged[0][0]
        fmt = lambda value: f"{value // 60:02d}:{value % 60:02d}"
        result.append({"date": day, "first_start": fmt(merged[0][0]), "last_end": fmt(merged[-1][1]),
                       "occupied_minutes": occupied, "window_minutes": window,
                       "gap_minutes": max(0, window - occupied)})
    return result
