"""Evidence-backed cleanup of known legacy notes during an ordinary editor save."""
from collections.abc import Mapping
from datetime import datetime, timezone

from backend.services.schedule_subjects import subject_label

STALE_MATH = "прежняя математическая группа удалена"
UNCONFIRMED_STUDY_GROUP = "для этого предмета не подтверждена учебная группа"
GROUP_LINK_NOTES = {STALE_MATH, UNCONFIRMED_STUDY_GROUP}
CLUB_LESSON_ONLY_NOTES = {
    UNCONFIRMED_STUDY_GROUP,
    "не определена учебная группа для урока английского",
    "выберите группу и проверьте её состав",
}
TEACHER_IDENTITY_NOTE = "не подтверждён преподаватель"
TEACHER_ASSIGNMENT_NOTE = "нет подтверждённого назначения преподавателя группе"


def normalized_note(value):
    return " ".join(str(value or "").strip(" .").casefold().split())


# Exact legacy field/overlap reasons only. Never clear arbitrary words or an
# import error just because a day has a mode.
_ROOM_ONLY_NOTES = {
    "кабинет не указан", "не указан кабинет", "для очного занятия не указан кабинет",
}
_SDEP_ONLY_NOTES = _ROOM_ONLY_NOTES | {
    TEACHER_IDENTITY_NOTE, TEACHER_ASSIGNMENT_NOTE, "преподаватель пока не назначен",
    "преподаватель занятия не подтверждён", "преподаватель не указан", "не указан преподаватель",
}


def day_mode_review_reason(value, *, sdep_display=False, online=False):
    waived = _SDEP_ONLY_NOTES if sdep_display else _ROOM_ONLY_NOTES if online else ()
    return "; ".join(clause for clause in notes(value) if normalized_note(clause) not in waived)


def subject_key(value):
    return subject_label(value).casefold()


def notes(value):
    return [clause.strip() for clause in str(value or "").split(";") if clause.strip()]


def selected_scope(rule):
    kind = rule.get("kind")
    if kind == "groups":
        return kind, sorted(set(rule.get("group_ids") or []))
    if kind == "classes" and not rule.get("grade"):
        return kind, sorted(set(rule.get("class_ids") or []))
    return None, []


def scope_verified(kind, ids, directory):
    """Stable active directory identity, not a subject/category recommendation."""
    if not ids or not kind:
        return False
    rows = [directory.get(str(value)) for value in ids]
    if kind == "classes":
        return all(row and row["group_type"] == "class" for row in rows)
    return kind == "groups" and all(row and row["group_type"] != "class" for row in rows)


def saved_group_is_current(lesson, rule, result):
    """Evidence of a saved exact rule and roster, never a matching label or a preview-only guess."""
    raw = lesson.get("audience")
    if not isinstance(raw, Mapping) or raw.get("kind") != "groups" or rule.get("kind") != "groups":
        return False
    group_ids = raw.get("group_ids") or raw.get("groupIds")
    if not isinstance(group_ids, list) or not group_ids:
        return False
    if sorted(set(map(str, group_ids))) != rule.get("group_ids"):
        return False
    snapshot = result.get("audience_snapshot")
    return (result.get("status") == "ok"
        and isinstance(snapshot, Mapping)
        and bool(snapshot.get("resolved_at"))
        and bool(snapshot.get("fingerprint"))
        and result.get("fingerprint_changed") is False
        and bool(snapshot.get("student_ids"))
        and snapshot["student_ids"] == result.get("proposed_student_ids"))


def is_legacy_group_link_note(lesson):
    return (not any(lesson.get(flag) for flag in ("importError", "unresolved", "review_required"))
            and STALE_MATH in {normalized_note(clause) for clause in notes(lesson.get("clarificationReason"))})


def without_legacy_group_notes(reason):
    return "; ".join(clause for clause in notes(reason) if normalized_note(clause) not in GROUP_LINK_NOTES)


def audit_resolved_notes(lesson, previous, remove, evidence, *, actor_user_id):
    """Remove proven-obsolete clauses only. Keep every unknown clause verbatim."""
    previous_note = str(previous.get("clarificationReason") or "")
    current_note = str(lesson.get("clarificationReason") or "")
    if not current_note:
        return False
    clauses = notes(current_note)
    retired = [clause for clause in clauses if normalized_note(clause) in remove]
    if not retired:
        return False
    remaining = [clause for clause in clauses if normalized_note(clause) not in remove]
    new_note = "; ".join(remaining)
    if new_note:
        lesson["clarificationReason"] = new_note
    else:
        lesson.pop("clarificationReason", None)
        lesson.pop("needsClarification", None)
    history = [*list(previous.get("edit_validation_history") or [])]
    history.append({
        "actor": str(actor_user_id),
        "at": datetime.now(timezone.utc).isoformat(),
        "before": previous_note,
        "after": new_note,
        "retired": retired,
        "evidence": evidence,
    })
    lesson["edit_validation_history"] = history
    return True


def read_active_scope_directory(database, ids):
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    with database.connection() as connection:
        rows = database.execute(connection, f"""SELECT id,group_type FROM groups
            WHERE canonical IS TRUE AND archived_at IS NULL AND CAST(id AS TEXT) IN ({marks})""", tuple(sorted(ids))).fetchall()
    return {str(row["id"]): dict(row) for row in rows}
