"""Evidence-backed cleanup of known legacy notes during an ordinary editor save."""
from datetime import datetime, timezone

from backend.services.schedule_subjects import subject_label

STALE_MATH = "прежняя математическая группа удалена"
UNCONFIRMED_STUDY_GROUP = "для этого предмета не подтверждена учебная группа"
CLUB_LESSON_ONLY_NOTES = {
    UNCONFIRMED_STUDY_GROUP,
    "не определена учебная группа для урока английского",
    "выберите группу и проверьте её состав",
}
TEACHER_IDENTITY_NOTE = "не подтверждён преподаватель"
TEACHER_ASSIGNMENT_NOTE = "нет подтверждённого назначения преподавателя группе"


def normalized_note(value):
    return " ".join(str(value or "").strip(" .").casefold().split())


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


def scope_verified(kind, ids, subject, directory):
    if not ids or not kind:
        return False
    rows = [directory.get(str(value)) for value in ids]
    if kind == "classes":
        return all(row and row["group_type"] == "class" for row in rows)
    return bool(subject) and all(row and row["group_type"] != "class"
                                and subject_key(row["subject"]) == subject for row in rows)


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
        rows = database.execute(connection, f"""SELECT id,group_type,subject FROM groups
            WHERE canonical IS TRUE AND archived_at IS NULL AND CAST(id AS TEXT) IN ({marks})""", tuple(sorted(ids))).fetchall()
    return {str(row["id"]): dict(row) for row in rows}
