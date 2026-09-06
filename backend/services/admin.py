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
