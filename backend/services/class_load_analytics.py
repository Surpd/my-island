"""Class and instructional-group workload from pinned published assignments."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from backend.database import Database
from backend.services import canonical_schedule as canonical


def _union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _minutes(intervals):
    return sum(end - start for start, end in _union(intervals))


def _day_summary(day, items, pauses):
    intervals = [(x["start_minute"], x["end_minute"]) for x in items if x["duration_minutes"] is not None]
    result = {"date": day, "first_start": None, "last_end": None, "occupied_minutes": None,
              "window_minutes": None, "gap_minutes": None, "excluded_pause_minutes": None}
    if not intervals:
        return result
    start, end = min(x[0] for x in intervals), max(x[1] for x in intervals)
    occupied = _minutes(intervals)
    breaks = [(max(start,a), min(end,b)) for a,b in pauses if a < end and b > start]
    occupied_or_break = _minutes(intervals + breaks)
    result.update(first_start=f"{start//60:02}:{start%60:02}", last_end=f"{end//60:02}:{end%60:02}",
                  occupied_minutes=occupied, window_minutes=end-start,
                  gap_minutes=end-start-occupied_or_break, excluded_pause_minutes=occupied_or_break-occupied)
    return result


def _group_summary(group, trajectory, occurrences, pauses):
    # Optional clubs have a separate denominator and cannot stretch a compulsory day.
    lessons = sorted(occurrences, key=lambda x: (x["date"], x["start_minute"] is None, x["start_minute"] or 0, x["occurrence_id"]))
    required = [x for x in lessons if x["activity_type"] != "club"]
    clubs = [x for x in lessons if x["activity_type"] == "club"]
    by_day = defaultdict(list)
    for item in required:
        by_day[item["date"]].append(item)
    days = [_day_summary(day, items, pauses.get(day, [])) for day, items in sorted(by_day.items())]
    valid = [x for x in required if x["duration_minutes"] is not None]
    subject, formats, activities = Counter(), Counter(), Counter()
    for item in valid:
        subject[item["activity"] or "unknown"] += item["duration_minutes"]
        formats[item["activity_format"] or item["delivery_mode"]] += item["duration_minutes"]
        activities[item["activity_type"]] += item["duration_minutes"]
    minutes = sum(x["duration_minutes"] for x in valid)
    return {"group_id": str(group["id"]), "group_name": group["display_name"] or group["name"],
            "group_type": group["group_type"], "trajectory": trajectory, "days": list(sorted(by_day)), "day_summaries": days,
            "lesson_count": len(required), "scheduled_minutes": (sum(x["occupied_minutes"] or 0 for x in days)
                                                                  if valid or not required else None),
            "assignment_minutes": minutes if valid or not required else None, "known_duration_count": len(valid),
            "mean_lesson_minutes": minutes/len(valid) if valid else None, "unknown_duration_items": len(required)-len(valid),
            "optional_lesson_count": len(clubs),
            "optional_minutes": (sum(x["duration_minutes"] or 0 for x in clubs)
                                 if not clubs or any(x["duration_minutes"] is not None for x in clubs) else None),
            "optional_unknown_duration_items": sum(x["duration_minutes"] is None for x in clubs),
            "activity_minutes": dict(activities), "subject_minutes": dict(subject), "format_minutes": dict(formats),
            "items": [{k:v for k,v in item.items() if k not in {"start_minute","end_minute"}} for item in lessons]}


def _directory(database):
    with database.connection() as conn:
        groups = {str(row["id"]): dict(row) for row in database.execute(conn,
            """SELECT id,name,display_name,group_type,base_class_name,subject,subject_subgroup,exam_track
               FROM groups WHERE canonical IS TRUE ORDER BY id""").fetchall()}
    classes_by_label, classes_by_grade = defaultdict(set), defaultdict(set)
    for gid, group in groups.items():
        if group["group_type"] == "class":
            for label in (group["name"], group["display_name"], group["base_class_name"]):
                if label:
                    classes_by_label[str(label).strip().casefold()].add(gid)
            grade = canonical.canonical_group_grade(group)
            if grade:
                classes_by_grade[grade].add(gid)
    parent, children = {}, defaultdict(set)
    for gid, group in groups.items():
        if group["group_type"] != "class" and group["base_class_name"]:
            candidates = classes_by_label[str(group["base_class_name"]).strip().casefold()]
            if len(candidates) == 1:
                parent[gid] = next(iter(candidates))
                children[parent[gid]].add(gid)
    return groups, classes_by_grade, parent, children


def class_load_analytics(database: Database, start_day: str, end_day: str | None = None, *,
                         group_id: str | None = None, group_type: str | None = None,
                         trajectory: str | None = None) -> dict[str, Any]:
    first, last = date.fromisoformat(start_day), date.fromisoformat(end_day or start_day)
    if last < first or (last-first).days >= 366:
        raise ValueError("Select between 1 and 366 calendar days")
    groups, grades, parents, children = _directory(database)
    items, pauses = defaultdict(dict), defaultdict(lambda: defaultdict(list))
    weeks, unknown_audience = [], 0
    monday = first - timedelta(days=first.weekday())
    while monday <= last:
        edition = canonical.resolve_published_week(database, monday.isoformat())
        weeks.append(edition.metadata)
        blocks = canonical._block_rows(database, edition.metadata["effective_week_id"]) if edition.effective else []
        for block in blocks:
            if block["change_kind"] == "cancelled" or block["weekday"] not in range(7):
                continue
            # Compare offsets before adding, including the last representable year.
            offset = int(block["weekday"])
            if not (first-monday).days <= offset <= (last-monday).days:
                continue
            day = (monday + timedelta(days=offset)).isoformat()
            start, end = block["start_time"], block["end_time"]
            a,b = canonical._clock_minutes(start),canonical._clock_minutes(end)
            duration = b-a if a is not None and b is not None and b>a else None
            for i, assignment in enumerate(canonical._assignments(database, block["canonical_block_id"], block["patch"])):
                meta = assignment.get("metadata") or {}
                kind, mark = meta.get("activity_type") or "lesson", meta.get("special_mark")
                if mark == "no_lesson" or assignment.get("activity") == "NO_LESSON":
                    continue
                rule = canonical._assignment_audience_rule(assignment)
                direct = set(canonical._assignment_group_ids(assignment)) | set(map(str,rule.get("group_ids") or []))
                if rule.get("kind") == "whole_grade":
                    direct.update(grades.get(str(rule.get("grade")), set()))
                # A residual audience covers part of this class. Its individual
                # subgroup assignment is unknown and must not be propagated.
                partial = rule.get("kind") in {"remaining", "available_slot", "students"}
                if rule.get("class_group_id"):
                    direct.add(str(rule["class_group_id"]))
                scope = direct.intersection(groups)
                pause = kind in {"lunch", "break"} or mark in {"lunch", "break"}
                for gid in tuple(scope):
                    if gid in parents and not pause:
                        scope.add(parents[gid])
                    if not partial:
                        scope.update(children[gid])
                if not scope:
                    unknown_audience += 1
                    continue
                if pause:
                    if duration is not None:
                        for gid in scope:
                            pauses[gid][day].append((a,b))
                    continue
                joint = meta.get("joint_activity_id") if meta.get("audience_coverage") == "explicit" else None
                identity = (["joint", str(joint), day, str(start), str(end)] if joint else
                            ["assignment", block["block_key"], canonical._assignment_id(block,assignment,i)])
                occurrence = canonical._hash([edition.metadata["effective_week_id"], *identity])
                origin = {"block_key": block["block_key"], "assignment_id": canonical._assignment_id(block,assignment,i),
                          "lesson_id": meta.get("constructor_lesson_id")}
                for gid in scope:
                    if occurrence in items[gid]:
                        items[gid][occurrence]["provenance"]["sources"].append(origin)
                        continue
                    items[gid][occurrence] = {"occurrence_id": occurrence, "date": day, "start_time": start, "end_time": end,
                        "start_minute": a, "end_minute": b, "duration_minutes": duration, "activity": assignment.get("activity"),
                        "activity_type": kind, "activity_format": meta.get("activity_format"),
                        "delivery_mode": meta.get("day_plan_mode") or "normal", "joint_activity_id": joint,
                        "provenance": {"effective_week_id": edition.metadata["effective_week_id"],
                                       "publication_id": edition.metadata["publication_id"], "sources": [origin]}}
        if (last-monday).days < 7:
            break
        monday += timedelta(days=7)
    published = sum(w["published"] for w in weeks)
    partial_weeks = sum(w["coverage"] in {"partial","with_exceptions"} for w in weeks)
    broken = any(w["origin"] == "broken_head" for w in weeks)
    status = ("partial" if published and (published != len(weeks) or partial_weeks) else "published" if published
              else "broken_publication" if broken else "canonical_not_materialized")
    result = []
    # A specific existing group's empty interval is measurable in an approved
    # week. An unpublished range carries its coverage without a synthetic zero.
    candidates = set(items)
    if group_id in groups:
        candidates.add(group_id)
    for gid in candidates:
        group = groups[gid]
        path = "/".join(str(group[k]) for k in ("base_class_name","subject","subject_subgroup","exam_track") if group[k])
        if (group_id and gid != group_id) or (group_type and group["group_type"] != group_type) or (trajectory and trajectory != path):
            continue
        summary = _group_summary(group, path, items[gid].values(), pauses[gid])
        coverage_status = ("unavailable" if not published else "partial"
                           if published < len(weeks) or partial_weeks or unknown_audience else "complete")
        summary["coverage_status"] = coverage_status
        summary["duration_coverage"] = "unavailable" if not published else "partial" if summary["unknown_duration_items"] else "complete"
        for field in ("lesson_count", "scheduled_minutes", "assignment_minutes", "mean_lesson_minutes",
                      "unknown_duration_items", "known_duration_count", "optional_lesson_count", "optional_minutes",
                      "optional_unknown_duration_items", "activity_minutes", "format_minutes", "subject_minutes",
                      "day_summaries", "days"):
            incomplete_duration = (summary["duration_coverage"] == "partial" and field in
                                   {"scheduled_minutes","assignment_minutes","mean_lesson_minutes","activity_minutes",
                                    "format_minutes","subject_minutes","day_summaries"})
            if coverage_status != "complete" or incomplete_duration or (field == "optional_minutes" and summary["optional_unknown_duration_items"]):
                summary["observed_" + field] = summary[field] if published else None
                summary[field] = None
        result.append(summary)
    result.sort(key=lambda x: (str(x["group_name"]), x["group_id"]))
    return {"status": status, "source": "published_canonical_assignments",
            "start_day": first.isoformat(), "end_day": last.isoformat(), "groups": result,
            "coverage": {"weeks": weeks, "group_count": len(result), "published_weeks": published,
                         "unknown_audience_items": unknown_audience, "partial_weeks": partial_weeks}}
