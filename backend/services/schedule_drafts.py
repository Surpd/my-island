from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence

from backend.database import Database
from backend.services.canonical_schedule import (
    canonical_version,
    import_canonical_artifact,
    materialize_effective_week,
    read_canonical_template,
)


AUDIENCE_KINDS = {"groups", "whole_grade", "base_class", "remaining", "students"}


class DraftConflict(Exception):
    def __init__(self, current: Mapping[str, Any] | None) -> None:
        super().__init__("Schedule draft was changed in another session")
        self.current = dict(current or {})


class DraftNotFound(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(database: Database, value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return deepcopy(fallback)
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return deepcopy(fallback)


def _draft_key(scope_kind: str, scope_key: str) -> str:
    if scope_kind not in {"template", "week"}:
        raise ValueError("scope_kind must be template or week")
    normalized = str(scope_key or "").strip()
    if not normalized:
        raise ValueError("scope_key is required")
    return f"{scope_kind}:{normalized}"


def _row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = _decode(item.get("payload"), {"changes": []})
    item["source_context"] = _decode(item.get("source_context"), {})
    item["has_changes"] = bool(item["payload"].get("changes"))
    return item


def get_draft(database: Database, scope_kind: str, scope_key: str) -> dict[str, Any]:
    key = _draft_key(scope_kind, scope_key)
    with database.connection() as connection:
        row = database.execute(connection, "SELECT * FROM schedule_editor_drafts WHERE draft_key=?", (key,)).fetchone()
    if row:
        return _row(row)
    version = canonical_version(database)
    return {
        "draft_key": key,
        "scope_kind": scope_kind,
        "scope_key": scope_key,
        "base_version_id": version.get("version_id") if version else None,
        "base_effective_week_id": None,
        "revision": 0,
        "payload": {"changes": []},
        "source_context": {},
        "has_changes": False,
        "updated_at": None,
    }


def _normalize_audience(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    kind = str(raw.get("kind") or "groups")
    if kind not in AUDIENCE_KINDS:
        raise ValueError(f"Unsupported audience kind: {kind}")
    result = {
        "kind": kind,
        "group_ids": sorted({str(item) for item in raw.get("group_ids") or [] if str(item)}),
        "grade": str(raw.get("grade") or ""),
        "partition_dimension": str(raw.get("partition_dimension") or ""),
        "partition_group_ids": sorted({str(item) for item in raw.get("partition_group_ids") or [] if str(item)}),
        "include_student_ids": sorted({str(item) for item in raw.get("include_student_ids") or [] if str(item)}),
        "exclude_student_ids": sorted({str(item) for item in raw.get("exclude_student_ids") or [] if str(item)}),
    }
    if kind in {"groups", "base_class"} and not result["group_ids"]:
        raise ValueError("Group audience requires at least one canonical group id")
    if kind == "remaining" and not (result["partition_dimension"] or result["partition_group_ids"]):
        raise ValueError("Remaining audience requires a partition dimension or group ids")
    if kind == "students" and not result["include_student_ids"]:
        raise ValueError("Student audience requires include_student_ids")
    return result


def _normalize_change(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Each draft change must be an object")
    change = deepcopy(dict(value))
    operation = str(change.get("operation") or "upsert")
    if operation not in {"upsert", "delete"}:
        raise ValueError(f"Unsupported draft operation: {operation}")
    block_key = str(change.get("block_key") or "").strip()
    if not block_key:
        raise ValueError("Draft change requires block_key")
    change["operation"] = operation
    change["block_key"] = block_key
    if operation == "upsert":
        lesson = dict(change.get("lesson") or {})
        lesson["audience"] = _normalize_audience(lesson.get("audience"))
        lesson["teacher_ids"] = [str(item) for item in lesson.get("teacher_ids") or []]
        lesson["activity"] = str(lesson.get("activity") or "").strip()
        if not lesson["activity"]:
            raise ValueError("Lesson activity is required")
        change["lesson"] = lesson
    change["manual"] = bool(change.get("manual", True))
    return change


def save_draft(
    database: Database,
    scope_kind: str,
    scope_key: str,
    *,
    expected_revision: int,
    payload: Mapping[str, Any],
    actor_user_id: Any = None,
    base_version_id: str | None = None,
    base_effective_week_id: str | None = None,
    source_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    key = _draft_key(scope_kind, scope_key)
    normalized_payload = {**dict(payload), "changes": [_normalize_change(item) for item in payload.get("changes") or []]}
    now = _now()
    with database.connection() as connection:
        current = database.execute(connection, "SELECT * FROM schedule_editor_drafts WHERE draft_key=?", (key,)).fetchone()
        if current:
            if int(current["revision"]) != int(expected_revision):
                raise DraftConflict(_row(current))
            next_revision = int(expected_revision) + 1
            payload_sql = "?::jsonb" if database.database_url else "?"
            row = database.execute(connection, f"""UPDATE schedule_editor_drafts
                SET payload={payload_sql}, source_context={payload_sql}, base_version_id=?,
                    base_effective_week_id=?, revision=?, updated_by=?, updated_at=?
                WHERE draft_key=? AND revision=? RETURNING *""", (
                _json(database, normalized_payload), _json(database, dict(source_context or _decode(current["source_context"], {}))),
                base_version_id or current["base_version_id"], base_effective_week_id or current["base_effective_week_id"],
                next_revision, actor_user_id, now, key, expected_revision,
            )).fetchone()
            if not row:
                latest = database.execute(connection, "SELECT * FROM schedule_editor_drafts WHERE draft_key=?", (key,)).fetchone()
                raise DraftConflict(_row(latest) if latest else None)
        else:
            if int(expected_revision) != 0:
                raise DraftConflict(None)
            version = base_version_id or ((canonical_version(database) or {}).get("version_id"))
            payload_sql = "?::jsonb" if database.database_url else "?"
            row = database.execute(connection, f"""INSERT INTO schedule_editor_drafts(
                draft_key,scope_kind,scope_key,base_version_id,base_effective_week_id,revision,
                payload,source_context,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,?,?,1,{payload_sql},{payload_sql},?,?,?,?) RETURNING *""", (
                key, scope_kind, scope_key, version, base_effective_week_id,
                _json(database, normalized_payload), _json(database, dict(source_context or {})),
                actor_user_id, actor_user_id, now, now,
            )).fetchone()
    return _row(row)


def discard_draft(database: Database, scope_kind: str, scope_key: str, *, expected_revision: int) -> None:
    key = _draft_key(scope_kind, scope_key)
    with database.connection() as connection:
        row = database.execute(connection, "DELETE FROM schedule_editor_drafts WHERE draft_key=? AND revision=? RETURNING draft_key", (key, expected_revision)).fetchone()
        if not row:
            current = database.execute(connection, "SELECT * FROM schedule_editor_drafts WHERE draft_key=?", (key,)).fetchone()
            if current:
                raise DraftConflict(_row(current))
            raise DraftNotFound(key)


def _assignment_from_lesson(lesson: Mapping[str, Any]) -> dict[str, Any]:
    audience = _normalize_audience(lesson.get("audience"))
    audience_kind = {"groups": "canonical_groups", "base_class": "canonical_groups", "whole_grade": "whole_grade", "remaining": "remaining", "students": "students"}[audience["kind"]]
    return {
        "activity": str(lesson.get("activity") or ""),
        "role": "residual" if audience["kind"] == "remaining" else "primary",
        "audience_kind": audience_kind,
        "canonical_group_ids": audience["group_ids"],
        "teacher_ids": [str(item) for item in lesson.get("teacher_ids") or []],
        "source_cells": lesson.get("source_cells") or [],
        "metadata": {
            "room": str(lesson.get("room") or ""),
            "teachers": lesson.get("teacher_names") or [],
            "audience_rule": audience,
            "manual_override": True,
            "source_identity": lesson.get("source_identity") or {},
        },
    }


def _change_patch(change: Mapping[str, Any]) -> dict[str, Any]:
    if change.get("operation") == "delete":
        return {"block_key": change["block_key"], "change_kind": "cancelled", "change_classification": "CANCELLED", "manual_override": True}
    lesson = change["lesson"]
    return {
        "block_key": change["block_key"],
        "change_kind": str(change.get("change_kind") or "replaced"),
        "change_classification": "SEMANTIC_AUDIENCE_CHANGE" if change.get("audience_changed") else "REPLACED",
        "weekday": lesson.get("weekday"),
        "slot": {"start": lesson.get("start_time"), "end": lesson.get("end_time")},
        "grade_scope": str(lesson.get("grade") or ""),
        "assignments": [_assignment_from_lesson(lesson)],
        "manual_override": True,
        "source_identity": lesson.get("source_identity") or {},
    }


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def publish_draft(database: Database, scope_kind: str, scope_key: str, *, expected_revision: int, actor_user_id: Any = None, comment: str = "") -> dict[str, Any]:
    draft = get_draft(database, scope_kind, scope_key)
    if not draft.get("has_changes"):
        raise DraftNotFound("Draft has no changes")
    if int(draft["revision"]) != int(expected_revision):
        raise DraftConflict(draft)
    changes = draft["payload"].get("changes") or []
    content_hash = _hash({"draft_key": draft["draft_key"], "revision": expected_revision, "changes": changes})
    if scope_kind == "week":
        version_id = str(draft.get("base_version_id") or (canonical_version(database) or {}).get("version_id") or "")
        if not version_id:
            raise ValueError("A canonical base version is required")
        result = materialize_effective_week(
            database, version_id, scope_key, [_change_patch(change) for change in changes],
            overlay_source_snapshot_id=str(draft.get("source_context", {}).get("source_snapshot_id") or "manual-draft"),
            overlay_fingerprint=f"manual:{content_hash}", overlay_observed_at=_now(),
        )
        published_id = result["effective_week_id"]
    else:
        base = read_canonical_template(database, draft.get("base_version_id"))
        if not base:
            raise ValueError("A canonical template is required")
        blocks = deepcopy(base["blocks"])
        for change in changes:
            if change["operation"] == "delete":
                blocks.pop(change["block_key"], None)
                continue
            lesson = change["lesson"]
            existing = blocks.get(change["block_key"], {})
            blocks[change["block_key"]] = {
                "weekday": lesson.get("weekday", existing.get("weekday")),
                "slot": {"start": lesson.get("start_time", existing.get("start_time")), "end": lesson.get("end_time", existing.get("end_time"))},
                "grade_scope": str(lesson.get("grade") or existing.get("grade_scope") or ""),
                "status": "resolved", "mode": existing.get("mode") or "ADMIN_EDIT",
                "derived_from": {"source_cells": lesson.get("source_cells") or [], "structural_fingerprint": content_hash},
                "assignments": [_assignment_from_lesson(lesson)],
            }
        version_id = f"manual-{content_hash[:24]}"
        artifact = {
            "schema_version": "schedule-studio-draft-v1", "version_id": version_id,
            "parent_version_id": base["version_id"], "source_snapshot": {"id": f"manual:{draft['draft_key']}:{expected_revision}", "fingerprint": content_hash},
            "summary": {"changes": len(changes), "comment": comment, "created_by": actor_user_id}, "blocks": blocks,
        }
        result = import_canonical_artifact(database, artifact, status="approved_with_exceptions", approved_at=_now())
        published_id = result["version_id"]
    discard_draft(database, scope_kind, scope_key, expected_revision=expected_revision)
    return {"status": "published", "scope_kind": scope_kind, "scope_key": scope_key, "published_id": published_id, "changes": len(changes), "result": result}


def merge_import_changes(database: Database, scope_key: str, *, expected_revision: int, proposed_changes: Sequence[Mapping[str, Any]], source_context: Mapping[str, Any], actor_user_id: Any = None) -> dict[str, Any]:
    draft = get_draft(database, "week", scope_key)
    existing = {str(item.get("block_key")): deepcopy(item) for item in draft["payload"].get("changes") or []}
    merged: list[dict[str, Any]] = []
    for raw in proposed_changes:
        proposed = _normalize_change(raw)
        previous = existing.pop(proposed["block_key"], None)
        if previous and previous.get("manual"):
            old_identity = (previous.get("lesson") or {}).get("source_identity") or {}
            new_identity = (proposed.get("lesson") or {}).get("source_identity") or {}
            same_source = old_identity == new_identity
            if previous.get("operation") == "upsert" and proposed.get("operation") == "upsert":
                proposed["lesson"]["audience"] = previous["lesson"]["audience"]
                proposed["lesson"]["source_identity"] = new_identity
                proposed["manual"] = True
                if not same_source:
                    proposed["review_required"] = True
                    proposed["review_reason"] = "Источник изменился — проверьте ручное назначение"
            merged.append(proposed)
        else:
            proposed["manual"] = False
            merged.append(proposed)
    merged.extend(existing.values())
    return save_draft(database, "week", scope_key, expected_revision=expected_revision, payload={"changes": merged, "import_summary": dict(source_context)}, actor_user_id=actor_user_id, base_version_id=draft.get("base_version_id"), base_effective_week_id=draft.get("base_effective_week_id"), source_context=source_context)


def preview_changes(preview: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Translate the read-only Google diff into the schedule editor write model."""
    changes: list[dict[str, Any]] = []
    for raw in preview.get("overlay_patches") or []:
        patch = dict(raw)
        block_key = str(patch.get("block_key") or "")
        if not block_key:
            continue
        source_identity = {
            "semantic_fingerprint": _hash({
                "weekly_raw_text": patch.get("weekly_raw_text") or {},
                "assignments": patch.get("assignments") or [],
                "change_kind": patch.get("change_kind"),
                "weekday": patch.get("weekday"),
                "slot": patch.get("slot") or {},
                "grade_scope": patch.get("grade_scope"),
            }),
            "block_key": block_key,
            "source_cells": sorted(str(item) for item in patch.get("weekly_source_cells") or []),
        }
        if str(patch.get("change_kind") or "") == "cancelled":
            changes.append({"operation": "delete", "block_key": block_key, "manual": False, "source_identity": source_identity})
            continue
        assignments = patch.get("assignments") or []
        if not assignments:
            continue
        assignment = dict(assignments[0])
        metadata = dict(assignment.get("metadata") or {})
        raw_rule = metadata.get("audience_rule") or {}
        if raw_rule:
            audience = dict(raw_rule)
        else:
            audience = {
                "kind": "groups",
                "group_ids": assignment.get("canonical_group_ids") or [],
                "grade": str(patch.get("grade_scope") or ""),
            }
        lesson = {
            "activity": str(assignment.get("activity") or ""),
            "weekday": patch.get("weekday"),
            "start_time": (patch.get("slot") or {}).get("start"),
            "end_time": (patch.get("slot") or {}).get("end"),
            "grade": str(patch.get("grade_scope") or ""),
            "teacher_ids": assignment.get("teacher_ids") or [],
            "teacher_names": metadata.get("teachers") or [],
            "room": str(metadata.get("room") or ""),
            "source_cells": patch.get("weekly_source_cells") or assignment.get("source_cells") or [],
            "source_identity": source_identity,
            "audience": audience,
        }
        changes.append({
            "operation": "upsert", "block_key": block_key, "manual": False,
            "change_kind": patch.get("change_kind") or "replaced", "lesson": lesson,
            "review_required": bool(patch.get("source_issue")),
            "review_reason": patch.get("source_issue"),
        })
    return changes


def audience_preview(database: Database, rule: Mapping[str, Any]) -> dict[str, Any]:
    audience = _normalize_audience(rule)
    with database.connection() as connection:
        students = [dict(row) for row in database.execute(connection, "SELECT id,display_name,class_name FROM identities WHERE kind='student' AND status='active' ORDER BY display_name,id").fetchall()]
        memberships = [dict(row) for row in database.execute(connection, "SELECT identity_id,group_id FROM memberships WHERE active IS TRUE AND member_role='student'").fetchall()]
        groups = [dict(row) for row in database.execute(connection, "SELECT id,semantic_dimension FROM groups WHERE canonical IS TRUE").fetchall()]
    by_group: dict[str, set[str]] = {}
    for membership in memberships:
        by_group.setdefault(str(membership["group_id"]), set()).add(str(membership["identity_id"]))
    candidates: set[str]
    if audience["kind"] in {"groups", "base_class"}:
        candidates = set().union(*(by_group.get(group_id, set()) for group_id in audience["group_ids"])) if audience["group_ids"] else set()
    elif audience["kind"] == "students":
        candidates = set(audience["include_student_ids"])
    else:
        grade = audience["grade"]
        candidates = {str(item["id"]) for item in students if not grade or str(item.get("class_name") or "").strip().startswith(grade)}
        if audience["kind"] == "remaining":
            partition_ids = set(audience["partition_group_ids"])
            if audience["partition_dimension"]:
                partition_ids.update(str(item["id"]) for item in groups if str(item.get("semantic_dimension") or "") == audience["partition_dimension"])
            assigned = set().union(*(by_group.get(group_id, set()) for group_id in partition_ids)) if partition_ids else set()
            candidates.difference_update(assigned)
    candidates.update(audience["include_student_ids"])
    candidates.difference_update(audience["exclude_student_ids"])
    resolved = [item for item in students if str(item["id"]) in candidates]
    return {"count": len(resolved), "students": resolved, "audience": audience, "warning": None}
