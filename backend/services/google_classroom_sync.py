from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.database import Database
from backend.services.google_live import GoogleLiveClient


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _due_at(work: dict[str, Any]) -> str | None:
    due_date = work.get("dueDate") or {}
    if not due_date.get("year") or not due_date.get("month") or not due_date.get("day"):
        return None
    due_time = work.get("dueTime") or {}
    hour = int(due_time.get("hours", 0))
    minute = int(due_time.get("minutes", 0))
    second = int(due_time.get("seconds", 0))
    return f"{int(due_date['year']):04d}-{int(due_date['month']):02d}-{int(due_date['day']):02d}T{hour:02d}:{minute:02d}:{second:02d}"


def normalize_course(course: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id": str(course.get("id", "")),
        "title": str(course.get("name", "")).strip(),
        "section": course.get("section"),
        "description": course.get("description"),
        "room": course.get("room"),
        "update_time": course.get("updateTime"),
        "raw_source": _json(course),
    }


def normalize_coursework(work: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id": str(work.get("id", "")),
        "title": str(work.get("title", "")).strip(),
        "description": str(work.get("description") or ""),
        "due_at": _due_at(work),
        "alternate_link": work.get("alternateLink"),
        "state": work.get("state"),
        "work_type": work.get("workType"),
        "max_points": work.get("maxPoints"),
        "update_time": work.get("updateTime"),
        "raw_source": _json(work),
    }


def normalize_material(material: dict[str, Any], *, source_type: str, source_id: str, index: int) -> dict[str, Any]:
    material_type = ""
    title = ""
    url = None
    drive_file_id = None
    for candidate in ("driveFile", "link", "youtubeVideo", "form"):
        payload = material.get(candidate)
        if not isinstance(payload, dict):
            continue
        material_type = candidate
        title = str(payload.get("title") or payload.get("text") or "").strip()
        url = payload.get("url") or payload.get("alternateLink")
        if candidate == "driveFile":
            nested = payload.get("driveFile") or {}
            drive_file_id = nested.get("id")
            title = str(nested.get("title") or title).strip()
            url = nested.get("alternateLink") or url
        break
    stable_part = drive_file_id or url or _json(material)
    key = f"{source_type}:{source_id}:{hashlib.sha256(str(stable_part).encode('utf-8')).hexdigest()}:{index}"
    return {
        "external_material_key": key,
        "source_type": source_type,
        "title": title,
        "material_type": material_type,
        "url": url,
        "drive_file_id": drive_file_id,
        "raw_source": _json(material),
    }


def normalize_submission(submission: dict[str, Any], identity_id: Any | None = None) -> dict[str, Any]:
    return {
        "external_id": str(submission.get("id", "")),
        "external_student_id": submission.get("userId"),
        "identity_id": identity_id,
        "state": str(submission.get("state", "UNKNOWN")),
        "assigned_grade": submission.get("assignedGrade"),
        "draft_grade": submission.get("draftGrade"),
        "late": submission.get("late"),
        "raw_source": _json(submission),
    }


def sync_classroom_course(
    database: Database,
    client: GoogleLiveClient,
    course: dict[str, Any],
    *,
    teacher_account: str,
    identity_map: dict[str, Any] | None = None,
    group_id: Any | None = None,
) -> dict[str, int]:
    normalized_course = normalize_course(course)
    if not normalized_course["external_id"] or not normalized_course["title"]:
        raise ValueError("Classroom course is missing id or name")
    course_id = normalized_course["external_id"]
    try:
        # Complete the read phase first. An API failure therefore cannot partially replace
        # the last normalized snapshot.
        works = client.coursework(course_id)
        dedicated_materials = client.coursework_materials(course_id)
        submissions_by_work = {
            str(work.get("id", "")): client.student_submissions(course_id, str(work.get("id", "")))
            for work in works
            if work.get("id")
        }
    except Exception as error:
        database.record_audit_event("classroom_sync.failed", "classroom_sync", {"course_id": course_id, "error": str(error)})
        raise

    try:
        course_row = database.upsert_classroom_course(normalized_course, teacher_account, group_id=group_id)
        counts = {"coursework": 0, "embedded_materials": 0, "dedicated_materials": 0, "student_submissions": 0, "unmapped_submissions": 0}
        seen_work_ids: list[str] = []
        for work in works:
            normalized_work = normalize_coursework(work)
            if not normalized_work["external_id"] or not normalized_work["title"]:
                raise ValueError("Classroom coursework is missing id or title")
            seen_work_ids.append(normalized_work["external_id"])
            work_row = database.upsert_classroom_coursework(normalized_work, course_row["id"])
            counts["coursework"] += 1
            for index, material in enumerate(work.get("materials") or []):
                database.upsert_classroom_material(normalize_material(material, source_type="embedded", source_id=normalized_work["external_id"], index=index), work_row["id"])
                counts["embedded_materials"] += 1
            for submission in submissions_by_work.get(normalized_work["external_id"], []):
                normalized_submission = normalize_submission(submission, (identity_map or {}).get(str(submission.get("userId", ""))))
                if not normalized_submission["external_id"]:
                    raise ValueError("Classroom submission is missing id")
                if normalized_submission["identity_id"] is None:
                    database.record_audit_event("classroom_submission.unmapped", "classroom_sync", {"course_id": course_id, "coursework_id": normalized_work["external_id"], "submission_id": normalized_submission["external_id"], "external_student_id": normalized_submission["external_student_id"]})
                    counts["unmapped_submissions"] += 1
                    continue
                database.upsert_classroom_submission(normalized_submission, work_row["id"])
                counts["student_submissions"] += 1
        for index, material_item in enumerate(dedicated_materials):
            dedicated_id = str(material_item.get("id", ""))
            for material_index, material in enumerate(material_item.get("materials") or []):
                database.upsert_classroom_material(normalize_material(material, source_type="dedicated", source_id=dedicated_id or str(index), index=material_index), course_id=course_row["id"])
                counts["dedicated_materials"] += 1
        database.mark_missing_classroom_coursework(course_row["id"], seen_work_ids)
        database.record_audit_event("classroom_sync.success", "classroom_sync", {"course_id": course_id, "counts": counts})
        return counts
    except Exception as error:
        database.record_audit_event("classroom_sync.failed", "classroom_sync", {"course_id": course_id, "error": str(error)})
        raise
