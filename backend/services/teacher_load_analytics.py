"""Compare measurable teacher workload with each week's pinned base."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from backend.database import Database
from backend.services import canonical_schedule as canonical
from backend.services.canonical_schedule import resolve_published_week
from backend.services.workload_intervals import format_minute, interval_summary, overlapping_intervals


def _teacher_rows(database, teacher_id):
    query = "SELECT id,display_name,status FROM identities WHERE kind='teacher'"
    params = ()
    if teacher_id:
        query += " AND CAST(id AS TEXT)=?"
        params = (str(teacher_id),)
    with database.connection() as connection:
        return [dict(row) for row in database.execute(connection, query + " ORDER BY id", params).fetchall()]


def _time_range(block):
    start = canonical._clock_minutes(block.get("start_time"))
    end = canonical._clock_minutes(block.get("end_time"))
    return (start, end) if start is not None and end is not None and start < end else None


def _occurrences(monday, blocks, assignments, groups):
    occurrences = {}
    for block in blocks:
        weekday = block.get("weekday")
        if weekday not in range(7) or (date.max - monday).days < weekday:
            continue
        day = (monday + timedelta(days=weekday)).isoformat()
        interval = _time_range(block)
        for index, assignment in enumerate(assignments(block)):
            meta = assignment.get("metadata") or {}
            kind = meta.get("activity_type") or "lesson"
            if kind in {"lunch", "break"} or meta.get("special_mark") in {"lunch", "break", "no_lesson"}:
                continue
            if meta.get("day_plan_mode") == "sdep" or assignment.get("activity") == canonical._NO_LESSON:
                continue
            lead = {str(value) for value in assignment.get("teacher_ids") or []}
            activity_format = meta.get("activity_format")
            responsible = ({str(value) for value in meta.get("responsible_teacher_ids") or []} - lead
                           if activity_format == "independent_practice" else set())
            group_ids = set(canonical._assignment_group_ids(assignment))
            joint = meta.get("joint_activity_id") if meta.get("audience_coverage") == "explicit" else None
            assignment_id = canonical._assignment_id(block, assignment, index)
            key = ("joint", joint, day, interval) if joint and interval else ("assignment", block["block_key"], assignment_id)
            source = {
                "block_key": block["block_key"], "assignment_id": assignment_id,
                "lesson_id": meta.get("constructor_lesson_id"),
                "source_provenance": block.get("source_provenance") or {},
            }
            item = occurrences.setdefault(key, {
                "id": canonical._hash([monday.isoformat(), *key]),
                "date": day, "interval": interval, "start_time": block.get("start_time"), "end_time": block.get("end_time"),
                "duration_minutes": interval[1] - interval[0] if interval else None,
                "activity": assignment.get("activity"), "activity_type": kind, "activity_format": activity_format,
                "role_ids": {}, "audience_group_ids": set(), "grade_scope": block.get("grade_scope"),
                "room": None if meta.get("day_plan_mode") == "online" else canonical.display_room(meta.get("room")),
                "delivery_mode": "online" if meta.get("day_plan_mode") == "online" else "normal",
                "provenance": {**source, "joint_activity_id": str(joint) if joint else None, "sources": []},
                "lineage": (set(), set(), set()),
            })
            item["audience_group_ids"].update(group_ids)
            item["role_ids"] = {**dict.fromkeys(responsible, "responsible"), **item["role_ids"], **dict.fromkeys(lead, "lead")}
            item["provenance"]["sources"].append(source)
            cell_keys = frozenset(str(cell.get("coordinate") or cell.get("cell") or "") if isinstance(cell, dict)
                                  else str(cell) for cell in assignment.get("source_cells") or []) - {""}
            if source["lesson_id"]:
                item["lineage"][0].add(str(source["lesson_id"]))
            if assignment.get("id"):
                item["lineage"][1].add(str(assignment["id"]))
            if cell_keys and group_ids:
                item["lineage"][2].add((cell_keys, frozenset(group_ids)))
    for item in occurrences.values():
        item["audience_group_ids"] = sorted(item["audience_group_ids"])
        item["audiences"] = [groups.get(group_id, group_id) for group_id in item["audience_group_ids"]]
    return list(occurrences.values())


def _pair_occurrences(before, after):
    pending_before = {item["id"]: item for item in before}
    pending_after = {item["id"]: item for item in after}
    pairs = []
    for priority in range(3):
        left, right = defaultdict(set), defaultdict(set)
        for item in pending_before.values():
            for key in item["lineage"][priority]:
                left[key].add(item["id"])
        for item in pending_after.values():
            for key in item["lineage"][priority]:
                right[key].add(item["id"])
        candidates = defaultdict(set)
        reverse = defaultdict(set)
        for key in left.keys() & right.keys():
            if len(left[key]) == len(right[key]) == 1:
                first, second = next(iter(left[key])), next(iter(right[key]))
                candidates[first].add(second)
                reverse[second].add(first)
        for first, matches in candidates.items():
            if len(matches) == 1:
                second = next(iter(matches))
                if len(reverse[second]) == 1:
                    pairs.append((pending_before.pop(first), pending_after.pop(second)))
    return pairs, list(pending_before.values()), list(pending_after.values())


def _change_rows(before, after, cancelled, added):
    pairs, unpaired_before, unpaired_after = _pair_occurrences(before, after)
    changes = []
    for first, second in pairs:
        kinds = []
        if (first["date"], (first["interval"] or (None,))[0]) != (second["date"], (second["interval"] or (None,))[0]):
            kinds.append("moved")
        if first["duration_minutes"] != second["duration_minutes"]:
            kinds.append("duration_changed")
        if first["role_ids"] != second["role_ids"]:
            prior_leads = {key for key, role in first["role_ids"].items() if role == "lead"}
            next_leads = {key for key, role in second["role_ids"].items() if role == "lead"}
            kinds.append("teacher_replaced" if prior_leads - next_leads and next_leads - prior_leads else "teachers_changed")
        if kinds:
            changes.append((kinds, first, second))
    for item in unpaired_before:
        sources = {source["block_key"] for source in item["provenance"]["sources"]}
        kinds = ["cancelled"] if sources and sources <= cancelled else ["unmatched_baseline"]
        changes.append((kinds, item, None))
    unresolved_lineage = [set().union(*(item["lineage"][priority] for item in unpaired_before))
                          for priority in range(3)]
    for item in unpaired_after:
        sources = {source["block_key"] for source in item["provenance"]["sources"]}
        # The effective layer's explicit addition is evidence only when
        # no baseline candidate carried the same stable lineage.
        possible_matches = any(a & b for a, b in zip(item["lineage"], unresolved_lineage))
        kinds = ["added"] if sources and sources <= added and not possible_matches else ["unmatched_published"]
        changes.append((kinds, None, item))
    return sorted(changes, key=lambda change: ((change[1] or change[2])["date"], (change[1] or change[2])["id"]))


def _read_weeks(database, first, last, groups):
    weeks = []
    templates = {}
    monday = first - timedelta(days=first.weekday())
    while monday <= last:
        edition = resolve_published_week(database, monday.isoformat())
        base_id = edition.metadata["base_version_id"] if edition.effective else None
        published, baseline, changes = [], [], []
        base_known = False
        if base_id:
            blocks = canonical._block_rows(database, edition.metadata["effective_week_id"])
            published = _occurrences(monday, (b for b in blocks if b["change_kind"] != "cancelled"),
                                     lambda b: canonical._assignments(database, b["canonical_block_id"], b["patch"]), groups)
            if base_id not in templates:
                templates[base_id] = canonical.read_canonical_template(database, base_id)
            template = templates[base_id]
            if template:
                baseline = _occurrences(monday, template["blocks"].values(), lambda b: b["assignments"], groups)
                baseline_resolved = all(b.get("status") == "resolved" and not b.get("unresolved")
                                        for b in template["blocks"].values())
                base_known = baseline_resolved
                changes = _change_rows(before=baseline, after=published,
                                       cancelled={b["block_key"] for b in blocks if b["change_kind"] == "cancelled"},
                                       added={b["block_key"] for b in blocks if b["change_kind"] in {"added", "weekly_only"}})
            # Pin every drill-down to the immutable revision being compared.
            for item in published:
                item["provenance"].update(effective_week_id=edition.metadata["effective_week_id"],
                                          publication_id=edition.metadata["publication_id"])
            for item in baseline:
                item["provenance"]["base_version_id"] = base_id
        weeks.append((edition, published, baseline, changes, base_known))
        if (last - monday).days < 7:
            break
        monday += timedelta(days=7)
    return weeks


def _visible(item):
    return {key: value for key, value in item.items() if key not in {"interval", "lineage", "role_ids"}}


def _day_summaries(day_items):
    result = []
    for day, items in sorted(day_items.items()):
        occupied = [item["interval"] for item in items if item["interval"] and item["role"] == "lead"]
        conflicts = overlapping_intervals(occupied)
        summary = interval_summary(occupied)
        if all(x["role"] == "responsible" for x in items):
            summary.update(occupied_minutes=0, gap_minutes=0)
        result.append({"date": day, **summary,
                       "scheduled_minutes": sum(x["duration_minutes"] for x in items if x["interval"]) if any(x["interval"] for x in items) else None,
                       "conflict_minutes": sum(end - start for start, end in conflicts),
                       "conflicts": [{"start_time": format_minute(start), "end_time": format_minute(end)}
                                     for start, end in conflicts]})
    return result


def _summarize(items, coverage):
    minutes = Counter()
    activity = Counter()
    by_day = defaultdict(list)
    unknown = 0
    for item in items:
        by_day[item["date"]].append(item)
        duration = item["duration_minutes"]
        if duration is None:
            unknown += 1
            continue
        minutes["scheduled_minutes"] += duration
        minutes[item["role"] + "_minutes"] += duration
        if item["provenance"]["joint_activity_id"]:
            minutes["joint_minutes"] += duration
        if item["activity_type"] in {"club", "elective"}:
            minutes["optional_minutes"] += duration
        activity[item["activity_type"]] += duration
    day_summaries = _day_summaries(by_day)
    for field in ("occupied_minutes", "gap_minutes", "conflict_minutes"):
        minutes[field] = sum(day[field] or 0 for day in day_summaries)
    duration = "unavailable" if items and unknown == len(items) else "partial" if unknown else "complete"
    summary = {"coverage_status": coverage, "duration_coverage": duration if coverage != "unavailable" else "unavailable",
               "unknown_duration_items": unknown, "items": [_visible(i) for i in items]}
    fields = {
        "lesson_count": len(items), "days": sorted(by_day), "activity_minutes": dict(activity),
        "day_summaries": day_summaries, **{key: minutes[key] for key in (
            "scheduled_minutes", "lead_minutes", "responsible_minutes", "joint_minutes", "optional_minutes",
            "occupied_minutes", "gap_minutes", "conflict_minutes")},
    }
    for name, value in fields.items():
        duration_field = name not in {"lesson_count", "days"}
        if coverage != "complete" or (duration_field and duration != "complete"):
            summary["observed_" + name] = value if coverage != "unavailable" and not (duration_field and duration == "unavailable") else None
            summary[name] = None
        else:
            summary[name] = value
    return summary


def teacher_load_analytics(database: Database, start_day: str, end_day: str | None = None,
                           teacher_id: Any | None = None, *, activity_type: str | None = None,
                           activity_format: str | None = None, group_id: str | None = None) -> dict[str, Any]:
    first, last = date.fromisoformat(start_day), date.fromisoformat(end_day or start_day)
    if last < first or (last - first).days >= 366:
        raise ValueError("Select between 1 and 366 calendar days")
    rows = _teacher_rows(database, teacher_id)
    with database.connection() as connection:
        groups = {str(row["id"]): row["display_name"] or row["name"] for row in database.execute(connection,
                 "SELECT id,name,display_name FROM groups WHERE canonical IS TRUE").fetchall()}
    weeks = _read_weeks(database, first, last, groups)
    start, finish = first.isoformat(), last.isoformat()
    teacher_ids = {str(row["id"]) for row in rows}

    def included(item):
        return bool(item and start <= item["date"] <= finish
                    and (not activity_type or item["activity_type"] == activity_type)
                    and (not activity_format or activity_format in {item["activity_format"], item["delivery_mode"]})
                    and (not group_id or str(group_id) in item["audience_group_ids"]))

    published_by_teacher = defaultdict(list)
    base_by_teacher = defaultdict(list)
    changes_by_teacher = defaultdict(list)
    measured_occurrences = 0
    unassigned = 0
    for edition, published, baseline, changes, _ in weeks:
        for items, destination in ((published, published_by_teacher), (baseline, base_by_teacher)):
            for item in items:
                if not included(item):
                    continue
                assigned = teacher_ids & item["role_ids"].keys()
                for tid in assigned:
                    destination[tid].append({**item, "role": item["role_ids"][tid]})
                if destination is published_by_teacher:
                    measured_occurrences += int(not teacher_id or bool(assigned))
                    unassigned += int(not item["role_ids"] and item["activity_type"] in {"lesson", "consultation", "laboratory"})
        for kinds, before, after in changes:
            participants = set().union(*(item["role_ids"] for item in (before, after) if included(item)))
            for tid in teacher_ids & participants:
                changes_by_teacher[tid].append({"kinds": kinds, "before": _change_view(before, tid),
                                                "after": _change_view(after, tid)})

    metadata = [edition.metadata for edition, *_ in weeks]
    available = sum(week["published"] for week in metadata)
    coverage = ("unavailable" if not available else "complete"
                if all(w["published"] and w["coverage"] == "complete" for w in metadata) and not unassigned else "partial")
    base_coverage = ("unavailable" if not any(base_known or baseline for _, _, baseline, _, base_known in weeks) else
                     "complete" if all(base_known for *_, base_known in weeks) else "partial")
    comparison_weeks = [{"week_start": edition.metadata["week_start"], "base_version_id": edition.metadata["base_version_id"],
                         "effective_week_id": edition.metadata["effective_week_id"], "baseline_known": known}
                        for edition, _, _, _, known in weeks]
    teachers = []
    for row in rows:
        tid = str(row["id"])
        summary = _summarize(sorted(published_by_teacher[tid], key=lambda item: (item["date"], item["interval"] or (1440, 1440), item["id"])), coverage)
        baseline = _summarize(base_by_teacher[tid], base_coverage)
        published_minutes = summary["scheduled_minutes"]
        base_minutes = baseline["scheduled_minutes"]
        summary.update(teacher_id=tid, teacher_name=row["display_name"], coverage=metadata,
                       status="published" if available else "canonical_not_materialized",
                       comparison={"weeks": comparison_weeks, "baseline_coverage": base_coverage,
                                   "baseline_scheduled_minutes": base_minutes,
                                   "observed_baseline_scheduled_minutes": baseline.get("observed_scheduled_minutes", base_minutes),
                                   "scheduled_minutes_delta": published_minutes - base_minutes
                                   if published_minutes is not None and base_minutes is not None else None,
                                   "changes": changes_by_teacher[tid]})
        teachers.append(summary)
    school_complete = coverage == "complete" and all(t["scheduled_minutes"] is not None for t in teachers)
    observed_minutes = [t.get("observed_scheduled_minutes", t["scheduled_minutes"]) for t in teachers]
    return {"status": "published" if available else "canonical_not_materialized", "start_day": start, "end_day": finish,
            "coverage_status": coverage, "teachers": teachers, "source": "published_canonical_assignments",
            "school_occurrence_count": measured_occurrences if coverage == "complete" else None,
            "observed_school_occurrence_count": measured_occurrences if available else None,
            "scheduled_person_minutes": sum(observed_minutes) if school_complete else None,
            "observed_scheduled_person_minutes": sum(m for m in observed_minutes if m is not None)
            if available and (not teachers or any(m is not None for m in observed_minutes)) else None,
            "coverage": {"teacher_count": len(teachers), "published_teacher_count": len(teachers) if available else 0,
                         "weeks": metadata, "unassigned_teacher_items": unassigned if available else None}}


def _change_view(item, teacher_id):
    if not item:
        return None
    return {**_visible(item), "role": item["role_ids"].get(teacher_id), "roles": item["role_ids"]}
