"""Conservative logical occurrence matching over immutable export records."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import re
import unicodedata
from typing import Any, Mapping

from backend.services.schedule_sheet_export import _hash


def _normal(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _teachers(lesson: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(set(lesson.get("teacherIds") or ([lesson["teacherId"]] if lesson.get("teacherId") else []))))


def _room(lesson: Mapping[str, Any]) -> str:
    value = re.sub(r"^каб(?:инет)?\.?\s*", "", _normal(lesson.get("room")))
    return "" if value in {"", "-", "—", "–"} else value


def logical_occurrences(by_day: Mapping[int, list[dict[str, Any]]], groups: Mapping[str, Any],
                        week_start: str) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
    """Merge only a completely compatible confirmed-audience bucket.

    Missing fields may be enriched by a confirmed sibling. A missing record
    cannot bridge conflicting teachers or rooms. Unknown/residual audiences
    are never inferred from visual classIds or source spreadsheet coordinates.
    """
    buckets: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    unknown = []
    english_before = defaultdict(int)
    for day, lessons in by_day.items():
        for lesson in lessons:
            audience = lesson.get("audience") or {}
            kind = audience.get("kind")
            audience_ids = audience.get("group_ids" if kind == "groups" else "class_ids") or []
            confirmed = kind in {"groups", "classes"} and bool(audience_ids) and all(g in groups for g in audience_ids)
            for gid in audience_ids:
                name = str(groups.get(gid, {}).get("name") or "")
                if re.fullmatch(r"english:(?:[1-9]|10)", name):
                    english_before[name] += 1
            if not confirmed:
                unknown.append(str(lesson["id"]))
                key = ("unresolved", str(lesson["id"]))
            else:
                normalized_audience = {k: sorted(v) if isinstance(v, list) else v for k, v in audience.items()}
                key = (week_start, day, lesson["start"], lesson["end"], _normal(lesson["subject"]),
                       _hash(normalized_audience), str(lesson.get("specialMark") or lesson.get("kind") or ""),
                       str(lesson.get("audienceMode") or "canonical"), tuple(sorted(lesson.get("excludedGroupIds") or [])),
                       str(lesson.get("activityType") or ""), str(lesson.get("activityCatalogId") or ""),
                       str(lesson.get("electiveDirection") or ""), tuple(sorted(lesson.get("eligibleGrades") or [])),
                       str(lesson.get("confirmed_participants_fingerprint") or ""))
            buckets[key].append(lesson)
    merged_clusters = []
    ambiguous = []
    rendered = defaultdict(list)
    aliases = {}
    for key, records in buckets.items():
        records = sorted(records, key=lambda l: str(l["id"]))
        teacher_values = {_teachers(l) for l in records if _teachers(l)}
        room_values = {_room(l) for l in records if _room(l)}
        conflicts = []
        if len(teacher_values) > 1:
            conflicts.append("conflicting_teachers")
        if len(room_values) > 1:
            conflicts.append("conflicting_rooms")
        if len(records) > 1 and not teacher_values and not room_values:
            conflicts.append("insufficient_assignment_evidence")
        can_merge = len(records) > 1 and not conflicts
        if len(records) > 1:
            report = {"source_lesson_ids": [str(l["id"]) for l in records], "day": records[0]["day"],
                      "start": records[0]["start"], "end": records[0]["end"], "subject": records[0]["subject"],
                      "group_ids": list((records[0].get("audience") or {}).get("group_ids") or []),
                      "reasons": conflicts or ["identical_confirmed_audience_time_subject_and_compatible_assignments"]}
            (merged_clusters if can_merge else ambiguous).append(report)
        for cluster in ([records] if can_merge else [[record] for record in records]):
            # Prefer the most complete confirmed record; tie-break by stable ID.
            base = max(cluster, key=lambda l: (bool(_teachers(l))+bool(_room(l)), str(l["id"])))
            lesson = deepcopy(base)
            source_ids = sorted(str(l["id"]) for l in cluster)
            lesson["id"] = source_ids[0]
            if can_merge:
                lesson["classIds"] = sorted({c for l in cluster for c in l["classIds"]})
                lesson["classId"] = lesson["classIds"][0] if len(lesson["classIds"]) == 1 else ""
                tids = next(iter(teacher_values), ())
                lesson["teacherIds"] = list(tids)
                lesson["teacherId"] = tids[0] if len(tids) == 1 else ""
                lesson["room"] = next((str(l["room"]) for l in cluster if _room(l)), "")
            lesson["_source_ids"] = source_ids
            lesson["_source_hashes"] = {str(l["id"]): _hash(l) for l in cluster}
            lesson["_logical_id"] = "mi-occurrence-" + _hash({"week": week_start, "source_ids": source_ids})[:24]
            lesson["_dedup_conflicts"] = conflicts if len(records) > 1 else []
            lesson["_source_clarifications"] = {str(l["id"]): str(l.get("clarificationReason") or "Аудитория занятия")
                                                  for l in cluster if l.get("needsClarification")}
            lesson["needsClarification"] = bool(lesson["_source_clarifications"]) or bool(lesson["_dedup_conflicts"])
            for sid in source_ids:
                aliases[sid] = lesson["_logical_id"]
            rendered[int(lesson["day"])].append(lesson)
    english_after = defaultdict(int)
    for lessons in rendered.values():
        for lesson in lessons:
            for gid in (lesson.get("audience") or {}).get("group_ids") or []:
                name = str(groups.get(gid, {}).get("name") or "")
                if re.fullmatch(r"english:(?:[1-9]|10)", name):
                    english_after[name] += 1
    return dict(rendered), {"source_records": len(aliases), "logical_occurrences": sum(map(len, rendered.values())),
                           "merged_clusters": merged_clusters, "ambiguous_clusters": ambiguous,
                           "removed_duplicate_presentations": sum(len(c["source_lesson_ids"])-1 for c in merged_clusters),
                           "unresolved_audience_ids": sorted(unknown), "source_to_logical_id": aliases,
                           "english_groups": [{"group": n, "source_records": english_before[f"english:{n}"],
                                               "logical_occurrences": english_after[f"english:{n}"]} for n in range(1, 11)]}
