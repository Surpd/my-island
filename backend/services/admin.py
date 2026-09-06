from __future__ import annotations

from typing import Any

from backend.database import Database


def create_identity(database: Database, display_name: str, class_name: str | None = None, kind: str = "student") -> Any:
    if kind not in {"student", "teacher"}:
        raise ValueError("Identity kind must be student or teacher")
    if not display_name.strip():
        raise ValueError("Identity display name is required")
    return database.create_identity(kind, display_name.strip(), class_name.strip() if class_name else None)


def create_group(database: Database, name: str, group_type: str) -> Any:
    if not name.strip() or not group_type.strip():
        raise ValueError("Group name and type are required")
    return database.create_group(name.strip(), group_type.strip())


def add_membership(
    database: Database,
    group_id: Any,
    identity_id: Any,
    source: str = "admin_override",
    member_role: str = "student",
) -> Any:
    if member_role not in {"student", "teacher"}:
        raise ValueError("Membership role must be student or teacher")
    return database.create_membership(group_id, identity_id, member_role, source)


def map_schedule_scope(
    database: Database,
    group_id: Any,
    audience: str,
    *,
    subject: str = "",
    subject_subgroup: str = "",
    exam_track: str = "",
) -> Any:
    if not audience.strip():
        raise ValueError("Schedule audience is required")
    return database.map_group_schedule_audience(
        group_id,
        audience.strip(),
        subject=subject.strip(),
        subject_subgroup=subject_subgroup.strip(),
        exam_track=exam_track.strip(),
    )


def override_membership(database: Database, identity_id: Any, group_id: Any, member_role: str, action: str, actor_user_id: Any, reason: str = "") -> Any:
    if member_role not in {"student", "teacher"}:
        raise ValueError("Membership role must be student or teacher")
    if action not in {"include", "exclude"}:
        raise ValueError("Membership override action must be include or exclude")
    identity = database.get_identity(identity_id)
    if not identity or identity["status"] != "active":
        raise ValueError("Membership identity must be active")
    if identity["kind"] != member_role:
        raise ValueError("Membership role must match identity kind")
    if not database.get_group(group_id):
        raise ValueError("Membership group was not found")
    if not reason.strip():
        raise ValueError("Membership override reason is required")
    return database.set_membership_override(identity_id, group_id, member_role, action, actor_user_id, reason.strip())


def assign_teacher(
    database: Database,
    teacher_identity_id: Any,
    group_id: Any,
    subject: str,
    active: bool,
    actor_user_id: Any,
    *,
    base_class_name: str | None = None,
    subject_subgroup: str | None = None,
    classroom_course_id: Any | None = None,
    exam_track: str | None = None,
) -> Any:
    identity = database.get_identity(teacher_identity_id)
    if not identity or identity["kind"] != "teacher" or identity["status"] != "active":
        raise ValueError("Teacher assignment requires an active teacher identity")
    if not database.get_group(group_id):
        raise ValueError("Teacher assignment group was not found")
    if classroom_course_id and not database.get_classroom_course(classroom_course_id):
        raise ValueError("Teacher assignment Classroom course was not found")
    return database.set_teacher_assignment(
        teacher_identity_id,
        group_id,
        subject.strip(),
        active,
        actor_user_id,
        base_class_name=base_class_name.strip() if base_class_name else None,
        subject_subgroup=subject_subgroup.strip() if subject_subgroup else None,
        classroom_course_id=classroom_course_id,
        exam_track=exam_track.strip() if exam_track else None,
    )


def assign_homeroom(database: Database, teacher_identity_id: Any, group_id: Any, active: bool, actor_user_id: Any) -> Any:
    identity = database.get_identity(teacher_identity_id)
    if not identity or identity["kind"] != "teacher" or identity["status"] != "active":
        raise ValueError("Homeroom assignment requires an active teacher identity")
    group = database.get_group(group_id)
    if not group or group["group_type"] != "class":
        raise ValueError("Homeroom assignment requires a class group")
    return database.set_homeroom_assignment(teacher_identity_id, group_id, active, actor_user_id)


def publish_information(database: Database, values: dict[str, Any], actor_user_id: Any) -> Any:
    if not str(values.get("title", "")).strip() or not str(values.get("content", "")).strip():
        raise ValueError("Information title and content are required")
    if values.get("audience_kind") not in {"all", "student", "teacher", "group"}:
        raise ValueError("Information audience must be all, student, teacher, or group")
    if values.get("audience_kind") == "group" and not values.get("audience_ref"):
        raise ValueError("Group audience requires an audience reference")
    if values.get("audience_kind") == "group" and not database.get_group(values.get("audience_ref")):
        raise ValueError("Information audience group was not found")
    if values.get("status") not in {"draft", "published", "archived"}:
        raise ValueError("Information status is invalid")
    clean = {**values, "title": str(values["title"]).strip(), "content": str(values["content"]).strip()}
    return database.create_announcement(clean, actor_user_id)
