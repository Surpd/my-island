from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from backend.services.schedule_experiments import (
    ExperimentConflict,
    _lesson_interval,
    _write_experiment_revision,
    get_experiment,
)


def term_comparison_lesson_fingerprint(lesson: Mapping[str, Any]) -> str:
    fields = (
        "day", "weekday", "period", "start", "end", "subject", "teacherId", "teacherIds",
        "room", "place", "classId", "classIds", "displayClassIds", "groupId", "groupIds",
        "audience", "audienceMode", "excludedGroupIds", "kind", "activityType",
        "specialMark", "blockId", "blockName", "format", "informational", "allDay",
    )
    author_fields = {key: lesson[key] for key in fields if key in lesson}
    encoded = json.dumps(author_fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _term_preparation(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    source = payload.get("school_source")
    if isinstance(source, Mapping):
        term = source.get("term2_preparation")
        if isinstance(term, Mapping):
            return term
    return {}


def _lesson_digests(payload: Mapping[str, Any]) -> dict[str, str]:
    return {str(row["id"]): term_comparison_lesson_fingerprint(row)
            for row in payload.get("lessons") or [] if row.get("id")}


def retain_term_comparison(payload: dict[str, Any], previous: Mapping[str, Any] | None,
                           *, record_edits: bool = False) -> None:
    """Discard client-written metadata and record edits from saved timetable changes."""
    source = payload.get("school_source")
    source = dict(source) if isinstance(source, Mapping) else {}
    term = dict(_term_preparation(payload))
    if "comparison" in term:
        del term["comparison"]
    if not payload.get("week_start") and previous:
        term.update(deepcopy(_term_preparation(previous)))
        comparison = term.get("comparison")
        if record_edits and isinstance(comparison, dict):
            before = _lesson_digests(previous)
            after = _lesson_digests(payload)
            changes = {key: after.get(key) for key in before.keys() | after.keys()
                       if before.get(key) != after.get(key)}
            if changes:
                comparison.setdefault("manual_edits", {}).update(changes)
    if term:
        source["term2_preparation"] = term
    elif "term2_preparation" in source:
        del source["term2_preparation"]
    if source:
        payload["school_source"] = source
    else:
        payload.pop("school_source", None)


def _slot_matches(lesson: Mapping[str, Any], target: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    start, end = target.get("start"), target.get("end")
    if not (isinstance(start, str) and isinstance(end, str) and target.get("day") is not None):
        return False
    interval = _lesson_interval({"start": start, "end": end}, [])
    return bool(interval and lesson.get("day", lesson.get("weekday")) == target["day"]
                and _lesson_interval(lesson, payload.get("slots") or []) == interval)


def record_term_comparison_decision(database: Any, draft_id: str, *, expected_revision: int, key: str,
                                    action: str, reason: str, lesson_id: str | None,
                                    actor_user_id: Any) -> dict[str, Any]:
    draft = get_experiment(database, draft_id)
    if int(draft["revision"]) != expected_revision:
        raise ExperimentConflict(draft)
    payload = deepcopy(draft["payload"])
    preparation = _term_preparation(payload)
    comparison = preparation.get("comparison")
    if payload.get("week_start") or not isinstance(comparison, dict):
        raise ValueError("Сверка доступна только в проверочной копии шаблона.")
    item = next((row for row in comparison.get("items", []) if row.get("key") == key), None)
    if item is None:
        raise ValueError("Позиция сверки отсутствует.")
    if item.get("initial_status") == "accepted":
        raise ValueError("Это изменение уже внесено при подготовке копии.")
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise ValueError("Укажите основание решения до 1000 символов.")
    statuses = {"accept": "accepted", "reject": "rejected", "defer": "deferred"}
    if action not in statuses:
        raise ValueError("Недопустимое решение по сверке.")
    receipts = comparison.setdefault("decisions", [])
    prior = next((row for row in reversed(receipts) if row.get("key") == key), None)
    if prior and prior.get("status") in {"accepted", "rejected"}:
        raise ValueError("Решение по этой позиции уже сохранено.")

    linked_id = None
    if action == "accept":
        baseline = comparison.get("baseline_lessons")
        if not isinstance(baseline, Mapping) or not baseline:
            raise ValueError("В копии нет сохранённой версии для проверки правки.")
        saved = {str(row.get("id") or ""): row for row in payload.get("lessons") or []}
        manual_edits = comparison.get("manual_edits") or {}
        source_id = str(item.get("lesson_id") or "")
        linked_id = str(lesson_id or source_id)
        if source_id and linked_id != source_id:
            raise ValueError("Сохраните правку в занятии, связанном с позицией сверки.")
        if item.get("kind") == "removed":
            if (not source_id or source_id not in baseline or source_id in saved
                    or source_id not in manual_edits or manual_edits[source_id] is not None):
                raise ValueError("Удалите связанное занятие в редакторе и сохраните черновик.")
        else:
            lesson = saved.get(linked_id)
            digest = term_comparison_lesson_fingerprint(lesson) if lesson else None
            if not digest or baseline.get(linked_id) == digest or manual_edits.get(linked_id) != digest:
                raise ValueError("Сначала внесите правку в редакторе и сохраните черновик.")
            target = item.get("after")
            if not isinstance(target, Mapping) or not _slot_matches(lesson, target, payload):
                raise ValueError("Выберите сохранённое занятие в слоте, указанном в таблице.")

    receipts.append({
        "key": key,
        "status": statuses[action],
        "reason": reason,
        "lesson_id": linked_id,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "actor_id": str(actor_user_id),
    })
    return _write_experiment_revision(database, draft_id, expected_revision, payload,
                                      actor_user_id=actor_user_id)
