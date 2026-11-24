from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from backend.services.schedule_edit_validation import CLUB_LESSON_ONLY_NOTES, is_legacy_group_link_note, normalized_note, notes, without_legacy_group_notes


def payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verified_teacher_directory_evidence(database: Any, draft: Mapping[str, Any]) -> tuple[set[str], set[tuple[str, str, str]]]:
    """Read active teacher identities and active group assignments in one query."""
    payload = draft.get("payload") if isinstance(draft.get("payload"), Mapping) else {}
    raw_reference = str(payload.get("week_start") or payload.get("reference_date") or date.today().isoformat())[:10]
    try:
        reference = date.fromisoformat(raw_reference)
    except ValueError:
        reference = date.today()
    with database.connection() as connection:
        rows = database.execute(connection, """SELECT i.id AS teacher_id,ta.group_id,ta.subject
            FROM identities i
            LEFT JOIN teacher_assignments ta ON ta.teacher_identity_id=i.id AND ta.active IS TRUE
              AND (ta.valid_from IS NULL OR ta.valid_from <= ?)
              AND (ta.valid_until IS NULL OR ta.valid_until >= ?)
            LEFT JOIN groups g ON g.id=ta.group_id AND g.archived_at IS NULL
            WHERE i.kind='teacher' AND i.status='active' AND (ta.id IS NULL OR g.id IS NOT NULL)""", (reference, reference)).fetchall()
    active_teachers = {str(row["teacher_id"]) for row in rows}
    assignments = {(str(row["teacher_id"]), str(row["group_id"]), " ".join(str(row["subject"] or "").casefold().split()))
                   for row in rows if row["group_id"] is not None}
    return active_teachers, assignments


def verified_teacher_assignment_pairs(database: Any, draft: Mapping[str, Any]) -> set[tuple[str, str, str]]:
    return verified_teacher_directory_evidence(database, draft)[1]


LUNCH_SCOPE_CLAUSE = "Уточните охват обеда: класс или отдельная группа"
ELECTIVE_ROSTER_CLAUSES = frozenset({
    "Состав направления курса по выбору не подтверждён",
    "Не подтверждена учебная группа или аудитория курса",
    "Направление курса не имеет отдельного подтверждённого roster",
})


def verified_administrative_classes(database: Any) -> dict[str, str]:
    with database.connection() as connection:
        rows = database.execute(connection, """SELECT id,name,display_name,base_class_name FROM groups
            WHERE canonical IS TRUE AND group_type='class' AND archived_at IS NULL""").fetchall()
    from backend.services.schedule_experiments import _grade_number
    return {str(row["id"]): next((n for key in ("base_class_name", "display_name", "name")
                                  if (n := _grade_number(row[key]))), "") for row in rows}


def scope_warning_evidence(payload: Mapping[str, Any], classes: Mapping[str, str], *,
                           unresolved_electives: dict[str, str] | None = None) -> dict[str, tuple[str, frozenset[str]]]:
    """Exact legacy clauses that active directory/constructor evidence supersedes.

    This is a warning projection, not a roster inference or a block assignment.
    """
    from backend.services.schedule_experiments import _is_lunch_lesson, _lesson_activity_type, _lesson_interval, _lesson_weekday

    lessons = [r for r in (payload.get("lessons") or []) if isinstance(r, Mapping)]
    blocks: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in lessons:
        block_id = row.get("blockId")
        if isinstance(block_id, str) and block_id.strip():
            blocks[block_id].append(row)
    slots = payload.get("slots") or []

    def ids(row: Mapping[str, Any], list_key: str, singular: str) -> set[str]:
        raw = row.get(list_key)
        return ({str(value) for value in raw if value} if isinstance(raw, list) else set()) | (
            {str(row[singular])} if row.get(singular) else set())

    def scope(row: Mapping[str, Any]) -> set[str]:
        listed = ids(row, "classIds", "")
        singular = str(row.get("classId") or "")
        if singular and listed and singular not in listed:
            return set()
        return listed or ({singular} if singular else set())

    def block_problem(row: Mapping[str, Any]) -> str:
        block_id = row.get("blockId")
        if not isinstance(block_id, str) or not block_id.strip() or block_id != block_id.strip():
            return "Не указан подтверждённый общий блок"
        day = _lesson_weekday(row)
        interval = _lesson_interval(row, slots)
        if day is None or not interval:
            return "Не подтверждён день или интервал общего блока"
        for peer in blocks[block_id]:
            if (_lesson_activity_type(peer) != "elective" or _is_lunch_lesson(peer) or peer.get("importError")):
                return "В общем блоке есть запись другого типа или ошибка импорта"
            if (_lesson_weekday(peer) != day or _lesson_interval(peer, slots) != interval):
                return "Направления общего блока стоят в разных временных интервалах"
            class_ids = scope(peer)
            if not class_ids or not class_ids <= classes.keys():
                return "Есть направление без подтверждённой привязки к действующему классу"
            eligibility = peer.get("eligibleGrades")
            if eligibility and (not isinstance(eligibility, list) or not all(
                    classes[c] and classes[c] in {str(g) for g in eligibility} for c in class_ids)):
                return "Класс не соответствует допустимым параллелям направления"
        return ""

    result = {}
    for row in lessons:
        if not row.get("id") or row.get("needsClarification") is not True or row.get("importError"):
            continue
        clauses = frozenset(str(row.get("clarificationReason") or "").strip().split("; "))
        class_ids = scope(row)
        if _is_lunch_lesson(row) and LUNCH_SCOPE_CLAUSE in clauses:
            day = _lesson_weekday(row)
            interval = _lesson_interval(row, slots)
            if day is None or not interval or not class_ids or not class_ids <= classes.keys():
                continue
            raw = row.get("audience") or {}
            if not isinstance(raw, Mapping) or raw.get("kind") not in {None, "", "classes"}:
                continue
            raw_classes = raw.get("class_ids") or raw.get("classIds") or []
            if raw_classes and (not isinstance(raw_classes, list) or {str(c) for c in raw_classes} != class_ids):
                continue
            if (ids(row, "groupIds", "groupId") - class_ids or raw.get("grade") or row.get("grade_scope")
                    or row.get("audienceMode") == "remainder"
                    or any(row.get(key) for key in ("excludedGroupIds", "excludedStudentIds", "includedStudentIds",
                                                    "confirmedParticipantIds", "include_student_ids", "exclude_student_ids"))
                    or any(raw.get(key) for key in ("group_ids", "groupIds", "student_ids", "studentIds",
                                                    "include_student_ids", "exclude_student_ids", "partition_group_ids", "partition_dimension"))):
                continue
            result[str(row["id"])] = ("lunch_scope_warning", frozenset({LUNCH_SCOPE_CLAUSE}))
        elif (_lesson_activity_type(row) == "elective" and not _is_lunch_lesson(row)
              and (present := clauses & ELECTIVE_ROSTER_CLAUSES)):
            problem = block_problem(row)
            if not problem:
                result[str(row["id"])] = ("elective_scope_warning", present)
            elif unresolved_electives is not None:
                unresolved_electives[str(row["id"])] = problem
    return result


def without_scope_clauses(reason: Any, clauses: frozenset[str]) -> str:
    return "; ".join(part for part in str(reason or "").strip().split("; ") if part not in clauses)


def apply_scope_warning_retirement(lesson: dict[str, Any], field: str, clauses: frozenset[str]) -> None:
    if field != "elective_scope_warning":
        return
    remaining = without_scope_clauses(lesson.get("clarificationReason"), clauses)
    if remaining:
        lesson["clarificationReason"] = remaining
    else:
        lesson.pop("needsClarification", None)
        lesson.pop("clarificationReason", None)
    lesson["scope_warning_normalization"] = {
        "code": "elective_class_block_verified",
        "removed_clauses": sorted(clauses),
    }


def verified_saved_group_warning_ids(database: Any, draft: Mapping[str, Any]) -> set[str]:
    from backend.services.schedule_experiments import experiment_week_audience_preview

    payload = draft["payload"]
    ids = {str(row["id"]) for row in (payload.get("lessons") or [])
           if row.get("id") and row.get("needsClarification") is True and is_legacy_group_link_note(row)}
    if not ids:
        return set()
    reference = date.fromisoformat(str(payload.get("week_start") or payload.get("reference_date") or date.today().isoformat())[:10])
    resolved = experiment_week_audience_preview(database, payload, reference)["lessons"]
    return {lesson_id for lesson_id in ids if resolved.get(lesson_id, {}).get("verified_saved_group")}


def verified_lunch_remainder_warning_ids(database: Any, draft: Mapping[str, Any], *, draft_database: Any = None) -> set[str]:
    """Prove a legacy lunch note obsolete without changing its audience rule.

    The publication preview is the delivery path used by Campus.  It must
    equal a separately reconstructed class remainder from active memberships
    minus every resolved overlapping lesson.  Historical copied student IDs
    are deliberately not evidence for a dynamic rule.
    """
    from backend.services.constructor_publication import preview_publication
    from backend.services.schedule_experiments import (_grade_number, _is_lunch_lesson, _lesson_activity_type, _lesson_audience,
        _lesson_interval, _lesson_weekday, _load_experiment_audience_snapshot, experiment_week_audience_preview)

    payload = draft.get("payload") if isinstance(draft.get("payload"), Mapping) else {}
    slots = payload.get("slots") or []
    rows = [row for row in (payload.get("lessons") or []) if isinstance(row, Mapping)]
    counts = Counter(str(row.get("id") or "") for row in rows)
    candidates: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        lesson_id = str(row.get("id") or "")
        rule = _lesson_audience(row)
        if (counts[lesson_id] != 1 or row.get("needsClarification") is not True
                or row.get("importError") or row.get("unresolved") or row.get("review_required")
                or LUNCH_SCOPE_CLAUSE not in str(row.get("clarificationReason") or "").split("; ")
                or not _is_lunch_lesson(row) or rule.get("kind") not in {"available_slot", "legacy_remaining"}
                or _lesson_weekday(row) is None or not _lesson_interval(row, slots)
                or not (rule.get("class_ids") or _grade_number(rule.get("grade")))):
            continue
        candidates[lesson_id] = row
    if not candidates:
        return set()
    reference = date.fromisoformat(str(payload.get("week_start") or payload.get("reference_date")
                                        or date.today().isoformat())[:10])
    resolved = experiment_week_audience_preview(database, payload, reference)["lessons"]
    try:
        delivery_preview = preview_publication(database, str(draft.get("id") or ""), scope_kind="template",
            expected_revision=int(draft.get("revision") or 0), draft_database=draft_database or database)
    except Exception:
        return set()
    delivery = {str(row.get("lesson_id") or ""): row
                for row in ((delivery_preview.get("published_source") or {}).get("records") or [])}
    verified = set()
    monday = reference - timedelta(days=reference.weekday())
    directory_by_date: dict[str, Mapping[str, Any]] = {}
    for lesson_id, lesson in candidates.items():
        preview = resolved.get(lesson_id) or {}
        record = delivery.get(lesson_id) or {}
        delivered = record.get("known_student_ids")
        if (preview.get("status") != "ok" or preview.get("blockers")
                or record.get("status") != "resolved" or record.get("conflict_student_ids")
                or "student_overlap" in (record.get("diagnostic_codes") or [])
                or not isinstance(delivered, list) or not delivered):
            continue
        day = _lesson_weekday(lesson)
        interval = _lesson_interval(lesson, slots)
        day_text = (monday + timedelta(days=day)).isoformat()
        if day_text not in directory_by_date:
            directory_by_date[day_text] = _load_experiment_audience_snapshot(database, day_text, day_text)
        directory = directory_by_date[day_text]
        students = {str(row["id"]) for row in directory["students"]}
        groups = {str(row["id"]): dict(row) for row in directory["groups"]}
        lesson_rule = _lesson_audience(lesson)
        class_ids = [str(value) for value in lesson_rule.get("class_ids") or []]
        grade = _grade_number(lesson_rule.get("grade"))
        if not class_ids and grade:
            allowed = {str(value) for value in payload.get("classes") or []}
            class_ids = [group_id for group_id, group in groups.items()
                         if group_id in allowed and group.get("group_type") == "class"
                         and _grade_number(group.get("base_class_name") or group.get("display_name")
                                           or group.get("raw_name") or group.get("name")) == grade]
        if not class_ids or any(value not in groups or groups[value].get("group_type") != "class" for value in class_ids):
            continue
        class_grades = {_grade_number(groups[value].get("base_class_name") or groups[value].get("display_name")
                                      or groups[value].get("raw_name") or groups[value].get("name"))
                        for value in class_ids}
        class_grades.discard(None)
        grade = grade or (next(iter(class_grades)) if len(class_grades) == 1 else None)
        excluded = {(str(row["identity_id"]), str(row["group_id"])) for row in directory["membership_overrides"]
                    if row["action"] == "exclude"}
        included = {(str(row["identity_id"]), str(row["group_id"])) for row in directory["membership_overrides"]
                    if row["action"] == "include"}
        by_group: dict[str, set[str]] = {}
        for row in directory["memberships"]:
            pair = (str(row["identity_id"]), str(row["group_id"]))
            if pair[0] in students and pair not in excluded:
                by_group.setdefault(pair[1], set()).add(pair[0])
        for student_id, group_id in included:
            if student_id in students and group_id in groups and (student_id, group_id) not in excluded:
                by_group.setdefault(group_id, set()).add(student_id)
        base = set().union(*(by_group.get(class_id, set()) for class_id in class_ids))
        if lesson_rule.get("kind") == "legacy_remaining":
            partition_ids = set(map(str, lesson_rule.get("partition_group_ids") or []))
            dimension = str(lesson_rule.get("partition_dimension") or "")
            if dimension:
                partition_ids.update(group_id for group_id, group in groups.items()
                                     if str(group.get("semantic_dimension") or "") == dimension)
            if not partition_ids or any(group_id not in groups or not by_group.get(group_id)
                                        for group_id in partition_ids):
                continue
            base.difference_update(set().union(*(by_group[group_id] for group_id in partition_ids)))
        occupied, unsafe = set(), False
        for other in rows:
            other_id = str(other.get("id") or "")
            other_interval = _lesson_interval(other, slots)
            if (other_id == lesson_id or _lesson_weekday(other) != day or not other_interval
                    or not (interval[0] < other_interval[1] and other_interval[0] < interval[1])
                    or _is_lunch_lesson(other) or str(other.get("specialMark") or "") == "no_lesson"
                    or other.get("kind") == "event" and other.get("informational") is True):
                continue
            other_preview = resolved.get(other_id) or {}
            other_rule = _lesson_audience(other)
            other_students = other_preview.get("proposed_student_ids")
            known_overlap = isinstance(other_students, list) and bool(base & set(map(str, other_students)))
            other_classes = set(map(str, other_rule.get("class_ids") or other.get("classIds") or []))
            other_groups = set(map(str, other_rule.get("group_ids") or []))
            scoped_overlap = bool(set(class_ids) & other_classes
                                  or base & set().union(*(by_group.get(group_id, set()) for group_id in other_groups)))
            other_grade = _grade_number(other_rule.get("grade"))
            if not known_overlap and not scoped_overlap and (not other_grade or other_grade != grade):
                continue  # An unrelated card cannot affect this candidate cohort.
            if other_preview.get("status") != "ok" or other_preview.get("blockers"):
                unsafe = True
                break
            # Elective directions share one card and cannot be reconstructed
            # as independent personal occupancies here; fail closed.
            if _lesson_activity_type(other) == "elective":
                unsafe = True
                break
            if not isinstance(other_students, list):
                unsafe = True
                break
            occupied.update(map(str, other_students))
        expected = base - occupied
        if not unsafe and expected and set(map(str, delivered)) == expected:
            verified.add(lesson_id)
    return verified


def reviewed_scope_proposals(payload: Mapping[str, Any], classes: Mapping[str, str],
                             evidence: Mapping[str, tuple[str, frozenset[str]]]) -> list[dict[str, Any]]:
    """Display evidence and authorized eligibility; do not edit the draft or its copy."""
    from backend.services.schedule_experiments import _lesson_activity_type, _lesson_interval, _lesson_weekday

    rows = [row for row in (payload.get("lessons") or []) if isinstance(row, Mapping)]
    row_ids = Counter(str(row.get("id") or "") for row in rows)
    slots = payload.get("slots") or []
    proposals = []
    for row in rows:
        lesson_id = str(row.get("id") or "")
        if evidence.get(lesson_id, (None,))[0] == "lunch_scope_warning":
            clauses = evidence[lesson_id][1]
            proposals.append({"lesson_id": lesson_id, "kind": "lunch_verified",
                              "field": "lunch_scope_warning",
                              "before": str(row.get("clarificationReason") or "").strip(),
                              "clauses": sorted(clauses),
                              "current_note_eligible": row_ids[lesson_id] == 1
                              and _can_apply_current_note(row, "lunch_scope_warning")})

    def is_named(row: Mapping[str, Any]) -> bool:
        subject = " ".join(str(row.get("subject") or "").casefold().split())
        return subject == "математика + вайб"

    def class_ids(row: Mapping[str, Any]) -> set[str]:
        multi = set(map(str, row.get("classIds") or [])) if isinstance(row.get("classIds"), list) else set()
        single = str(row.get("classId") or "")
        if multi and single and single not in multi:
            return set()
        return multi or ({single} if single else set())

    def period(row: Mapping[str, Any]) -> tuple:
        return _lesson_weekday(row), _lesson_interval(row, slots)

    # The name is authorized only for this exact direction, never as a new
    # default for other programs or as a replacement for existing participants.
    named = [row for row in rows if is_named(row)]
    occurrences: Counter[tuple] = Counter((row.get("blockId"), *period(row)) for row in named
                                         if isinstance(row.get("blockId"), str))
    for row in named:
        lesson_id = str(row.get("id") or "")
        if not lesson_id:
            continue
        block = row.get("blockId")
        slot = period(row)
        ids = class_ids(row)
        target = {"5", "6"}
        old = row.get("eligibleGrades")
        old_grades = {str(grade) for grade in old} if isinstance(old, list) else set()
        peers = [peer for peer in rows if peer.get("blockId") == block] if isinstance(block, str) and block.strip() else []
        problem = ""
        if not all(any(grade == value for value in classes.values()) for grade in target):
            problem = "Не подтверждены действующие классы обеих параллелей."
        elif (_lesson_activity_type(row) != "elective" or row.get("importError") or not peers
              or slot[0] is None or not slot[1] or occurrences[(block, *slot)] != 1
              or any(_lesson_activity_type(peer) != "elective" or peer.get("importError") or period(peer) != slot
                     for peer in peers)):
            problem = "Проверьте тип занятия, его время и подтверждённый общий блок."
        elif not ids or not ids <= classes.keys() or any(classes[value] not in target for value in ids):
            problem = "Проверьте привязку к действующим классам 5–6."
        elif old is not None and (not isinstance(old, list) or not old_grades <= target):
            problem = "Текущая доступность выходит за рамки 5–6; сверьте запись перед изменением."
        if problem or old_grades != target:
            proposals.append({"lesson_id": lesson_id, "kind": "elective_availability",
                              "eligible_grades": [5, 6], "problem": problem})
    return proposals


CURRENT_NOTE_FIELDS = frozenset({"teacher_identity_warning", "teacher_assignment_warning", "elective_scope_warning",
                                 "lunch_scope_warning"})



def _can_apply_current_note(row: Mapping[str, Any] | None, field: str) -> bool:
    return bool(row and field in CURRENT_NOTE_FIELDS
                and not any(row.get(key) for key in ("importError", "unresolved", "review_required"))
                and (field != "elective_scope_warning" or row.get("activityType") == "elective" and row.get("blockId")))


def normalization_preview(draft: Mapping[str, Any], *, verified_active_teachers: set[str] | None = None,
                          verified_teacher_assignments: set[tuple[str, str, str]] | None = None,
                          verified_classes: Mapping[str, str] | None = None,
                          verified_group_links: set[str] | None = None,
                          verified_lunch_remainders: set[str] | None = None) -> dict[str, Any]:
    """Plan only evidence-backed legacy type updates; all other rows are preserved."""
    from backend.services.schedule_edit_validation import day_mode_review_reason
    from backend.services.schedule_experiments import _lesson_activity_type, _lesson_day_modes, lesson_is_online, lesson_is_sdep_display

    payload = draft.get("payload") if isinstance(draft.get("payload"), Mapping) else {}
    lessons = payload.get("lessons") if isinstance(payload.get("lessons"), list) else []
    blocks: Counter[str] = Counter(str(row.get("blockId") or "") for row in lessons if isinstance(row, Mapping))
    block_types: dict[str, set[str]] = {}
    for row in lessons:
        if isinstance(row, Mapping) and row.get("blockId"):
            block_types.setdefault(str(row["blockId"]), set()).add(str(row.get("activityType") or ""))
    unresolved_electives: dict[str, str] = {}
    scope_evidence = scope_warning_evidence(payload, verified_classes or {}, unresolved_electives=unresolved_electives)
    for lesson_id in verified_lunch_remainders or ():
        scope_evidence[str(lesson_id)] = ("lunch_scope_warning", frozenset({LUNCH_SCOPE_CLAUSE}))
    scope_proposals = reviewed_scope_proposals(payload, verified_classes or {}, scope_evidence)
    changes: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    unchanged = 0
    already_current = 0
    unclassified = 0
    retained_warning_counts: Counter[str] = Counter()
    retained_warning_examples: dict[str, list[dict[str, str]]] = defaultdict(list)
    verified_teacher_assignments = verified_teacher_assignments or set()
    verified_active_teachers = verified_active_teachers or set()
    for row in lessons:
        if not isinstance(row, Mapping):
            continue
        block_id = str(row.get("blockId") or "")
        kind = str(row.get("activityType") or "")
        # Stable imported elective ids encode the source slot role. Require
        # repeated membership in that same explicit block before migrating.
        strong_legacy_block = (block_id.startswith("source:") and block_id.endswith(":electives")
                               and blocks[block_id] > 1 and block_types.get(block_id, set()) <= {"", "elective"})
        if kind == "elective":
            already_current += 1
            unchanged += 1
            if not block_id and (row.get("electiveDirection") or set(str(row.get("clarificationReason") or "").split("; ")) & ELECTIVE_ROSTER_CLAUSES):
                manual.append({"lesson_id": str(row.get("id") or ""), "reason": "Элективное направление не связано с общим блоком; требуется групповое сопоставление"})
        elif not kind and strong_legacy_block:
            changes.append({"lesson_id": str(row.get("id") or ""), "field": "activity_type",
                            "before": "(не задан)", "after": "elective",
                            "companion_fields": ["activityNameSnapshot"] if not row.get("activityNameSnapshot") else [],
                            "block_id": block_id, "reason": "Стабильный исходный ID общего блока electives; в блоке несколько направлений"})
        elif not kind and row.get("electiveDirection"):
            manual.append({"lesson_id": str(row.get("id") or ""), "reason": "Направление электива не связано с подтверждённым общим блоком"})
            unclassified += 1
            unchanged += 1
        elif not kind:
            unclassified += 1
            unchanged += 1
        else:
            already_current += 1
            unchanged += 1
        warning_reason = str(row.get("clarificationReason") or "").strip()
        row_id = str(row.get("id") or "")
        if row_id in unresolved_electives and not any(item["lesson_id"] == row_id for item in manual):
            manual.append({"lesson_id": row_id, "reason": unresolved_electives[row_id]})
        if row_id in scope_evidence and scope_evidence[row_id][0] == "elective_scope_warning":
            field, clauses = scope_evidence[row_id]
            changes.append({"lesson_id": row_id, "field": field, "before": warning_reason,
                            "after": without_scope_clauses(warning_reason, clauses) or "(снято)",
                            "clauses": sorted(clauses),
                            "reason": "Общий блок проверен по типу, времени, классам и доступности; персональный состав не требуется"})
        if row.get("needsClarification") is True and warning_reason == "Не подтверждён преподаватель":
            teacher_ids = {str(value) for value in (row.get("teacherIds") or []) if str(value)}
            if row.get("teacherId"):
                teacher_ids.add(str(row["teacherId"]))
            if teacher_ids and teacher_ids <= verified_active_teachers:
                changes.append({"lesson_id": str(row.get("id") or ""), "field": "teacher_identity_warning",
                    "before": warning_reason, "after": "(снято)", "reason": "Явно указанный преподаватель найден в активном справочнике"})
        elif (row.get("needsClarification") is True
              and "Нет подтверждённого назначения преподавателя группе" in warning_reason.split("; ")):
            teacher_ids = {str(value) for value in (row.get("teacherIds") or []) if str(value)}
            if row.get("teacherId"):
                teacher_ids.add(str(row["teacherId"]))
            if teacher_ids and teacher_ids <= verified_active_teachers:
                clauses = frozenset({"Нет подтверждённого назначения преподавателя группе"})
                changes.append({"lesson_id": str(row.get("id") or ""), "field": "teacher_assignment_warning",
                    "before": warning_reason, "after": without_scope_clauses(warning_reason, clauses) or "(снято)",
                    "clauses": sorted(clauses),
                    "reason": "Явно указанный преподаватель найден в активном справочнике; отдельное назначение на группу не требуется"})
    cleared_warning_ids = {str(item["lesson_id"]) for item in changes
                           if item.get("field") in {"teacher_assignment_warning", "teacher_identity_warning"}}
    for row in lessons:
        if not isinstance(row, Mapping) or row.get("needsClarification") is not True:
            continue
        lesson_id = str(row.get("id") or "")
        if lesson_id in cleared_warning_ids:
            continue
        reason = str(row.get("clarificationReason") or "Причина не сохранена")
        if _lesson_activity_type(row) == "club" and not row.get("importError"):
            remaining = [clause for clause in notes(reason) if normalized_note(clause) not in CLUB_LESSON_ONLY_NOTES]
            if not remaining:
                continue
            reason = "; ".join(remaining)
        if lesson_id in scope_evidence:
            reason = without_scope_clauses(reason, scope_evidence[lesson_id][1])
            if not reason:
                continue
        if lesson_id in (verified_group_links or ()) and is_legacy_group_link_note(row):
            reason = without_legacy_group_notes(reason)
            if not reason:
                continue
        if not row.get("importError"):
            modes = _lesson_day_modes(payload, row, verified_classes or {})
            if lesson_is_sdep_display(row, modes) or lesson_is_online(row, modes):
                reason = day_mode_review_reason(reason, sdep_display=lesson_is_sdep_display(row, modes),
                                                online=lesson_is_online(row, modes))
                if not reason:
                    continue
        retained_warning_counts[reason] += 1
        retained_warning_examples[reason].append({"lesson_id": lesson_id, "subject": str(row.get("subject") or "Занятие")})
    rows_by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in lessons:
        if isinstance(row, Mapping):
            rows_by_id[str(row.get("id") or "")].append(row)
    for change in changes:
        matching = rows_by_id[change["lesson_id"]]
        change["current_note_eligible"] = (
            len(matching) == 1 and _can_apply_current_note(matching[0], change["field"])
        )
    source_hash = payload_hash(payload)
    plan = {"changes": changes, "scope_proposals": scope_proposals, "source_hash": source_hash,
            "source_revision": int(draft.get("revision") or 0),
            "source_id": str(draft.get("id") or "")}
    type_change_count = sum(item.get("field") == "activity_type" for item in changes)
    teacher_warning_clear_count = sum(item.get("field") in {"teacher_assignment_warning", "teacher_identity_warning"} for item in changes)
    return {
        "source_id": plan["source_id"], "source_name": str(draft.get("name") or ""),
        "source_revision": plan["source_revision"], "source_hash": source_hash,
        "plan_hash": payload_hash(plan), "lesson_count": len(lessons),
        "automatic_count": len(changes), "activity_type_change_count": type_change_count,
        "scope_proposals": scope_proposals,
        "teacher_warning_clear_count": teacher_warning_clear_count,
        "lunch_scope_warning_suppressed_count": sum(field == "lunch_scope_warning" for field, _ in scope_evidence.values()),
        "elective_scope_warning_clear_count": sum(r["field"] == "elective_scope_warning" for r in changes),
        "already_current_count": already_current,
        "unchanged_count": unchanged, "unclassified_count": unclassified,
        "manual_count": len(manual), "real_conflict_count": None,
        "changes": changes, "manual": manual,
        "retained_warning_count": sum(retained_warning_counts.values()),
        "retained_warnings": [{"reason": reason, "count": count, "examples": retained_warning_examples[reason]}
                              for reason, count in sorted(retained_warning_counts.items())],
        "notes": ["Неоднозначные записи и сохранённые предупреждения оставлены без изменений.",
                  "Цифровые направления без общего blockId нужно сопоставить отдельно в групповом конструкторе."],
    }


def normalized_copy_payload(draft: Mapping[str, Any], preview: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(draft.get("payload") or {}))
    changes = list(preview.get("changes") or [])
    for lesson in payload.get("lessons") or []:
        if isinstance(lesson, dict):
            lesson_changes = [row for row in changes if str(row.get("lesson_id") or "") == str(lesson.get("id") or "")]
            for change in lesson_changes:
                if change.get("field") in {"teacher_assignment_warning", "teacher_identity_warning"}:
                    remaining = (without_scope_clauses(lesson.get("clarificationReason"),
                                                       frozenset(change.get("clauses") or ()))
                                 if change.get("field") == "teacher_assignment_warning" else "")
                    if remaining:
                        lesson["clarificationReason"] = remaining
                    else:
                        lesson.pop("needsClarification", None)
                        lesson.pop("clarificationReason", None)
                    lesson["normalization_resolution"] = {
                        "code": "teacher_assignment_verified" if change.get("field") == "teacher_assignment_warning" else "teacher_identity_verified",
                        "source": "identity_directory"}
                elif change.get("field") == "elective_scope_warning":
                    apply_scope_warning_retirement(lesson, change["field"], frozenset(change["clauses"]))
                elif change.get("field") == "activity_type":
                    lesson["activityType"] = "elective"
                    if "activityNameSnapshot" in change.get("companion_fields", []) and str(lesson.get("subject") or "").strip():
                        lesson["activityNameSnapshot"] = str(lesson["subject"]).strip()
    source = dict(payload.get("school_source") or {})
    source["normalization_copy"] = {"source_id": preview["source_id"],
        "source_revision": preview["source_revision"], "source_hash": preview["source_hash"],
        "plan_hash": preview["plan_hash"], "changed_count": len(changes),
        "activity_type_change_count": int(preview.get("activity_type_change_count") or 0),
        "teacher_warning_clear_count": int(preview.get("teacher_warning_clear_count") or 0),
        "change_plan": [{"lesson_id": str(row.get("lesson_id") or ""),
                         "fields": ([row["field"], *row.get("companion_fields", [])]
                                    if row.get("field") == "activity_type" else
                                    ["needsClarification", "clarificationReason",
                                     "scope_warning_normalization" if row["field"] == "elective_scope_warning"
                                     else "normalization_resolution"])}
                        for row in changes]}
    payload["school_source"] = source
    return payload


def apply_selected_schedule_notes(database: Any, source_database: Any, draft_id: str, *, expected_revision: int,
                                  expected_source_hash: str, expected_plan_hash: str, selected: list[dict[str, str]],
                                  actor_user_id: Any = None) -> dict[str, Any]:
    """Recompute proof and save only selected warning fields in the original draft."""
    from backend.services.schedule_experiments import ExperimentConflict, get_experiment, _write_experiment_revision

    draft = get_experiment(database, draft_id)
    if draft["revision"] != expected_revision:
        raise ExperimentConflict(draft)
    source = draft["payload"]
    if source.get("week_start") or source.get("draft_archive") or source.get("draft_kind") not in {"school", "user"}:
        raise ValueError("Действие доступно только для открытого школьного шаблона.")
    teachers, assignments = verified_teacher_directory_evidence(database, draft)
    plan = normalization_preview(draft, verified_active_teachers=teachers, verified_teacher_assignments=assignments,
                                 verified_classes=verified_administrative_classes(database),
                                 verified_group_links=verified_saved_group_warning_ids(source_database, draft),
                                 verified_lunch_remainders=verified_lunch_remainder_warning_ids(
                                     source_database, draft, draft_database=database))
    if plan["source_hash"] != expected_source_hash or plan["plan_hash"] != expected_plan_hash:
        raise ExperimentConflict(draft)
    keys = [(entry["lesson_id"], entry["field"]) for entry in selected]
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("Выберите конкретные замечания без повторов.")
    candidates = {(str(item["lesson_id"]), str(item["field"])): item
                  for item in [*plan["changes"], *plan.get("scope_proposals", [])]
                  if item.get("field") and item.get("current_note_eligible")}
    payload = copy.deepcopy(source)
    rows = payload.get("lessons") or []
    for lesson_id, field in keys:
        proposed = candidates.get((lesson_id, field))
        matching = [row for row in rows if str(row.get("id") or "") == lesson_id]
        if not proposed or len(matching) != 1:
            raise ValueError("Одно из выбранных замечаний больше не подтверждено. Проверьте расписание повторно.")
        row = matching[0]
        if row.get("importError") or row.get("unresolved") or row.get("review_required"):
            raise ValueError("У выбранного занятия осталась самостоятельная ошибка. Проверьте её отдельно.")
        original = str(row.get("clarificationReason") or "").strip()
        if row.get("needsClarification") is not True or original != proposed["before"]:
            raise ExperimentConflict(draft)
        if field == "elective_scope_warning":
            if row.get("activityType") != "elective" or not row.get("blockId"):
                raise ValueError("Не подтверждён ранее сохранённый общий блок курса.")
            remaining = without_scope_clauses(original, frozenset(proposed["clauses"]))
        elif field == "lunch_scope_warning":
            # The proposal is regenerated from the current draft, active class
            # directory and exact day/slot evidence. Remove only that proven
            # historical clause; every independent warning remains intact.
            clauses = frozenset(proposed.get("clauses") or ())
            if clauses != frozenset({LUNCH_SCOPE_CLAUSE}) or LUNCH_SCOPE_CLAUSE not in original.split("; "):
                raise ValueError("Старая пометка об охвате обеда больше не подтверждена.")
            remaining = without_scope_clauses(original, clauses)
        else:
            # Existing plan requires explicitly saved teacher IDs and matching
            # active directory evidence; never set or replace any teacher here.
            expected = {"teacher_identity_warning": "Не подтверждён преподаватель",
                        "teacher_assignment_warning": "Нет подтверждённого назначения преподавателя группе"}[field]
            if field == "teacher_identity_warning" and original != expected:
                raise ExperimentConflict(draft)
            if field == "teacher_assignment_warning":
                clauses = frozenset(proposed.get("clauses") or ())
                if clauses != frozenset({expected}) or expected not in original.split("; "):
                    raise ExperimentConflict(draft)
                remaining = without_scope_clauses(original, clauses)
            else:
                remaining = ""
        if remaining:
            row["clarificationReason"] = remaining
        else:
            row.pop("clarificationReason", None)
            row.pop("needsClarification", None)
    saved = _write_experiment_revision(database, draft_id, expected_revision, payload, actor_user_id=actor_user_id)
    return {"draft": saved, "cleared_count": len(keys)}
