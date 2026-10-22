"""Seed an entirely synthetic school and authoritative canonical baseline."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.database import Database
from backend.services.canonical_schedule import (
    canonical_version, import_canonical_artifact, materialize_effective_week,
    project_student, project_teacher, read_canonical_template,
)
from backend.services.schedule_activities import seed_activities
from backend.services.schedule_experiments import create_experiment
from backend.services.constructor_publication import initialize_publication_schema, preview_publication
from backend.services.teacher_ui_colors import initialize_missing, initialize_sqlite_schema


ORIGIN = "consolidation-fixture"
WEEK = "2026-10-19"
VERSION = "local-consolidation-baseline-v1"


def _insert_group(db: Database, name: str, group_type: str, **fields: Any) -> str:
    semantic_dimension = fields.pop("semantic_dimension", None)
    row = db.create_group(name, group_type, **fields)
    if semantic_dimension:
        with db.connection() as c:
            c.execute("UPDATE groups SET semantic_dimension=? WHERE id=?", (semantic_dimension, row["id"]))
    return str(row["id"])


def _dense_constructor_payload(class_groups: dict[str, str], math_groups: dict[str, str],
                               english_group: str, teacher_ids: list[str], slots: list[tuple[str, str]]) -> dict[str, Any]:
    """A visually dense but collision-free synthetic school week for palette/UI review."""
    labels = list(class_groups)
    lessons: list[dict[str, Any]] = []
    subjects = ["Математика", "Русский язык", "История", "Биология", "Физика", "Литература", "География", "Информатика"]
    for day in range(5):
        for period, (start, end) in enumerate(slots):
            # One shared English occurrence replaces each class's ordinary
            # lesson in this slot, retaining the canonical cross-class audience.
            if day == 2 and period == 3:
                lessons.append({"id": "fixture-dense-english-shared", "subject": "Английский язык",
                    "day": day, "period": period, "start": start, "end": end,
                    "classId": class_groups[labels[0]], "classIds": list(class_groups.values()),
                    "groupIds": [english_group], "teacherId": teacher_ids[12], "teacherIds": [teacher_ids[12]],
                    "room": "Онлайн", "audience": {"kind": "groups", "group_ids": [english_group]},
                    "canonicalRole": "primary", "resolutionStatus": "resolved"})
                continue
            for class_index, label in enumerate(labels):
                subject = subjects[period]
                group_id = (math_groups[f"{label}:{'A' if (day + class_index) % 2 == 0 else 'B'}"]
                            if period == 0 else class_groups[label])
                teacher_index = (class_index * len(slots) + period) % len(teacher_ids)
                lesson = {"id": f"fixture-dense-{day}-{period}-{class_index}", "subject": subject,
                    "day": day, "period": period, "start": start, "end": end,
                    "classId": class_groups[label], "classIds": [class_groups[label]],
                    "groupIds": [group_id], "teacherId": teacher_ids[teacher_index],
                    "teacherIds": [teacher_ids[teacher_index]], "room": str(101 + class_index),
                    "audience": {"kind": "groups", "group_ids": [group_id]},
                    "canonicalRole": "primary", "resolutionStatus": "resolved"}
                if day == 0 and period == 4:
                    lesson.update(subject="Обед", teacherId="", teacherIds=[], room="Столовая",
                                  specialMark="lunch", protected=True)
                if day == 4 and period == 6:
                    lesson.update(subject="Событие школы", activityType="event",
                                  teacherId=teacher_ids[20 + class_index], teacherIds=[teacher_ids[20 + class_index]],
                                  room="Актовый зал")
                if day == 4 and period == 7 and class_index == 0:
                    lesson.update(subject="Без занятия", teacherId="", teacherIds=[], noLesson=True, specialMark="no_lesson",
                                  protected=True, audience={"kind": "classes", "class_ids": [class_groups[label]]})
                lessons.append(lesson)

    # Keep two real math subgroup lessons in parallel at the first period.
    for class_index, label in enumerate(labels[:2]):
        lane = "B" if class_index % 2 == 0 else "A"
        group_id = math_groups[f"{label}:{lane}"]
        lessons.append({"id": f"fixture-dense-math-parallel-{class_index}", "subject": "Математика",
            "day": 0, "period": 0, "start": slots[0][0], "end": slots[0][1],
            "classId": class_groups[label], "classIds": [class_groups[label]], "groupIds": [group_id],
            "teacherId": teacher_ids[4 + class_index], "teacherIds": [teacher_ids[4 + class_index]],
            "room": str(111 + class_index), "audience": {"kind": "groups", "group_ids": [group_id]},
            "canonicalRole": "primary", "resolutionStatus": "resolved", "parallel": True})
    if len(lessons) != 120:
        raise AssertionError(f"Expected 120 synthetic dense constructor lessons, got {len(lessons)}")
    return {"draft_kind": "school", "days": [0, 1, 2, 3, 4],
        "slots": [{"start": start, "end": end} for start, end in slots],
        "classes": list(class_groups.values()), "lessons": lessons}


def ensure_dense_template(db: Database, fixture: dict[str, Any]) -> dict[str, Any]:
    """Add the visual-review template and school pointer without rebuilding data."""
    with db.connection() as c:
        selection = c.execute("SELECT draft_id FROM school_template_selection WHERE scope='school'").fetchone()
    if selection:
        fixture["school_template_selection_id"] = str(selection["draft_id"])
        fixture["dense_template_draft_id"] = str(selection["draft_id"])
        return fixture
    slots = [("08:30", "09:15"), ("09:25", "10:10"), ("10:20", "11:05"), ("11:15", "12:00"),
             ("12:10", "12:55"), ("13:05", "13:50"), ("14:00", "14:45"), ("14:55", "15:40")]
    dense = create_experiment(db, name="Synthetic school template · 120 lessons", source="import",
        payload=_dense_constructor_payload(fixture["classes"], fixture["math_groups"], fixture["english_group"],
                                           fixture["teachers"], slots),
        actor_user_id=fixture["admin_user_id"])
    dense_encoded = json.dumps(dense["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(dense_encoded.encode("utf-8")).hexdigest()
    with db.connection() as c:
        c.execute("""INSERT INTO school_template_selection
            (scope,draft_id,source_draft_id,source_revision,source_payload_sha256)
            VALUES ('school',?,?,?,?)""", (dense["id"], dense["id"], dense["revision"], digest))
    fixture["dense_template_draft_id"] = dense["id"]
    fixture["school_template_selection_id"] = dense["id"]
    fixture.setdefault("counts", {})["dense_constructor_lessons"] = len(dense["payload"]["lessons"])
    return fixture


def seed(db: Database) -> dict[str, Any]:
    """Seed only the disposable DB passed by the caller; never reads source data."""
    with db.connection() as c:
        # The database is rebuilt by local_harness.py before this function runs.
        c.execute("DELETE FROM schedule_activity_catalog")
        c.execute("DELETE FROM schedule_experiment_drafts")
        c.execute("DELETE FROM canonical_effective_blocks")
        c.execute("DELETE FROM canonical_effective_weeks")
        c.execute("DELETE FROM canonical_schedule_assignments")
        c.execute("DELETE FROM canonical_schedule_blocks")
        c.execute("DELETE FROM canonical_schedule_versions")
        c.execute("DELETE FROM memberships WHERE source=?", (ORIGIN,))
        c.execute("DELETE FROM teacher_assignments WHERE source=?", (ORIGIN,))
        c.execute("DELETE FROM identities WHERE origin=?", (ORIGIN,))
        c.execute("DELETE FROM groups WHERE name LIKE 'Fixture %'")

    admin = db.get_or_create_user(990041, "admin")
    admin_id = int(admin["id"])
    with db.connection() as c:
        c.execute("INSERT OR IGNORE INTO user_roles(user_id,role,source) VALUES (?,'admin',?)", (admin_id, ORIGIN))

    labels = ["7-А synthetic", "7-Б synthetic", "8-А synthetic"]
    class_groups: dict[str, str] = {}
    math_groups: dict[str, str] = {}
    student_ids: dict[str, list[str]] = {label: [] for label in labels}
    for label in labels:
        class_groups[label] = _insert_group(db, label, "class", display_name=label, base_class_name=label)
        for lane in ("A", "B"):
            group_label = f"Fixture Math {label} {lane}"
            math_groups[f"{label}:{lane}"] = _insert_group(
                db, group_label, "instructional", display_name=group_label,
                subject="Математика", base_class_name=label, semantic_dimension="math",
            )

    english_group = _insert_group(
        db, "Fixture English 7-8 Shared", "instructional",
        display_name="Fixture English · shared 7–8", subject="Английский",
        semantic_dimension="english",
    )
    for label in labels:
        for index in range(1, 13):
            ref = f"student:{label}:{index:02d}"
            identity = db.create_identity("student", f"Synthetic Student {label} {index:02d}", label)
            identity_id = str(identity["id"])
            student_ids[label].append(identity_id)
            with db.connection() as c:
                c.execute("UPDATE identities SET origin=?,source_ref=? WHERE id=?", (ORIGIN, ref, identity_id))
            db.create_membership(class_groups[label], identity_id, "student", ORIGIN, f"class:{ref}")
            lane = "A" if index <= 6 else "B"
            db.create_membership(math_groups[f"{label}:{lane}"], identity_id, "student", ORIGIN, f"math:{ref}")
            if index <= 6:
                db.create_membership(english_group, identity_id, "student", ORIGIN, f"english:{ref}")

    teacher_ids: list[str] = []
    for index in range(1, 25):
        teacher = db.create_identity("teacher", f"Synthetic Teacher {index:02d}")
        teacher_id = str(teacher["id"])
        teacher_ids.append(teacher_id)
        with db.connection() as c:
            c.execute("UPDATE identities SET origin=?,source_ref=? WHERE id=?", (ORIGIN, f"teacher:{index:02d}", teacher_id))
    for index, group_id in enumerate([*math_groups.values(), english_group]):
        with db.connection() as c:
            c.execute("""INSERT INTO teacher_assignments
                (teacher_identity_id,group_id,subject,active,source,source_ref)
                VALUES (?,?,?,1,?,?)""", (teacher_ids[index], group_id,
                "Английский" if group_id == english_group else "Математика", ORIGIN, f"assignment:{index}"))

    # Canonical artifact contains the stable template only. Source provenance and
    # teacher colors are deliberately absent from lesson payloads and mappings.
    blocks: dict[str, dict[str, Any]] = {}
    slots = [("08:30", "09:15"), ("09:25", "10:10"), ("10:20", "11:05"), ("11:15", "12:00")]
    for class_index, label in enumerate(labels):
        key = f"2026-10-19|0|08:30|{label}"
        blocks[key] = {
            "weekday": 0, "slot": {"start": slots[0][0], "end": slots[0][1]},
            "grade_scope": label[0], "status": "resolved", "mode": "parallel",
            "derived_from": {"source_cells": [f"fixture:{label}:math"], "structural_fingerprint": f"fixture-math-{class_index}"},
            "assignments": [
                {"activity": "Математика", "role": "primary",
                 "audience": {"type": "canonical_groups", "canonical_group_ids": [math_groups[f"{label}:{lane}"]]},
                 "teacher_ids": [teacher_ids[class_index * 2 + lane_index]], "room": str(101 + class_index * 2 + lane_index),
                 "source_cells": [f"fixture:{label}:math:{lane}"]}
                for lane_index, lane in enumerate(("A", "B"))
            ],
        }
        ordinary_key = f"2026-10-19|1|09:25|{label}"
        blocks[ordinary_key] = {
            "weekday": 1, "slot": {"start": slots[1][0], "end": slots[1][1]},
            "grade_scope": label[0], "status": "resolved", "mode": "single",
            "derived_from": {"source_cells": [f"fixture:{label}:ordinary"], "structural_fingerprint": f"fixture-ordinary-{class_index}"},
            "assignments": [{"activity": "История", "role": "primary",
                "audience": {"type": "canonical_groups", "canonical_group_ids": [class_groups[label]]},
                "teacher_ids": [teacher_ids[8 + class_index]], "room": str(201 + class_index),
                "source_cells": [f"fixture:{label}:ordinary"]}],
        }

    shared_key = "2026-10-19|2|10:20|shared-english"
    blocks[shared_key] = {
        "weekday": 2, "slot": {"start": slots[2][0], "end": slots[2][1]},
        "grade_scope": "7,8", "status": "resolved", "mode": "shared",
        "derived_from": {"source_cells": ["fixture:shared:english"], "structural_fingerprint": "fixture-shared-english"},
        "assignments": [{"activity": "Английский язык", "role": "primary",
            "audience": {"type": "canonical_groups", "canonical_group_ids": [english_group]},
            "teacher_ids": [teacher_ids[12]], "room": "Онлайн", "source_cells": ["fixture:shared:english"]}],
    }
    lunch_key = "2026-10-19|3|11:15|lunch"
    blocks[lunch_key] = {
        "weekday": 3, "slot": {"start": slots[3][0], "end": slots[3][1]},
        "grade_scope": "7,8", "status": "resolved", "mode": "activity",
        "derived_from": {"source_cells": ["fixture:lunch"], "structural_fingerprint": "fixture-lunch"},
        "assignments": [{"activity": "Обед", "role": "explicit_no_lesson",
            "audience": {"type": "canonical_groups", "canonical_group_ids": [class_groups[labels[0]], class_groups[labels[1]], class_groups[labels[2]]]},
            "teacher_ids": [], "source_cells": ["fixture:lunch"]}],
    }
    event_key = "2026-10-19|4|13:00|school-event"
    blocks[event_key] = {
        "weekday": 4, "slot": {"start": "13:00", "end": "13:45"},
        "grade_scope": "7,8", "status": "resolved", "mode": "special_event",
        "derived_from": {"source_cells": ["fixture:event"], "structural_fingerprint": "fixture-event"},
        "assignments": [{"activity": "Synthetic campus event", "role": "special_event",
            "audience": {"type": "canonical_groups", "canonical_group_ids": [class_groups[label] for label in labels]},
            "teacher_ids": [teacher_ids[20], teacher_ids[21], teacher_ids[22]], "room": "Hall", "source_cells": ["fixture:event"]}],
    }
    uncertain_key = "2026-10-19|4|14:00|uncertain"
    blocks[uncertain_key] = {
        "weekday": 4, "slot": {"start": "14:00", "end": "14:45"},
        "grade_scope": "7", "status": "unresolved", "mode": "review_required",
        "derived_from": {"source_cells": ["fixture:uncertain"], "structural_fingerprint": "fixture-uncertain"},
        "unresolved": [{"state": "protected", "review_required": True, "deferred": True,
                        "reason": "Synthetic regression marker; excluded from authoritative projections."}],
        "assignments": [],
    }
    artifact = {
        "schema_version": "canonical-schedule-bootstrap-v1", "version_id": VERSION,
        "status": "authoritative", "source_snapshot": {"id": "synthetic-consolidation-snapshot-v1",
            "fingerprint": hashlib.sha256(json.dumps(blocks, sort_keys=True).encode()).hexdigest()},
        "summary": {"synthetic": True, "classes": 3, "students": 36, "teachers": 24},
        "blocks": blocks,
    }
    imported = import_canonical_artifact(db, artifact, status="authoritative", approved_at="2026-10-19T00:00:00Z")
    with db.connection() as c:
        c.execute("UPDATE canonical_schedule_versions SET metadata=? WHERE version_id=?",
            (json.dumps({"summary": artifact["summary"], "status": "authoritative", "authoritative": True}), VERSION))
    effective = materialize_effective_week(db, VERSION, WEEK, patches=[])

    seed_activities(db)
    initialize_publication_schema(db)
    initialize_sqlite_schema(db)
    color_seed = initialize_missing(db, "school", admin_id)
    lesson = {
        "id": "fixture-constructor-math", "subject": "Математика", "day": 0, "period": 0,
        "start": slots[0][0], "end": slots[0][1], "classId": class_groups[labels[0]],
        "classIds": [class_groups[labels[0]]], "groupIds": [math_groups[f"{labels[0]}:A"]],
        "teacherId": teacher_ids[0], "teacherIds": [teacher_ids[0]], "room": "101",
        "audience": {"kind": "groups", "group_ids": [math_groups[f"{labels[0]}:A"]]},
        "canonicalRole": "primary", "resolutionStatus": "resolved",
    }
    draft_payload = {"draft_kind": "school", "week_start": WEEK, "reference_date": WEEK,
        "days": [0, 1, 2, 3, 4], "slots": [{"start": start, "end": end} for start, end in slots],
        "classes": list(class_groups.values()), "lessons": [lesson,
            {"id": "fixture-lunch", "subject": "Обед", "day": 0, "period": 4,
             "start": "12:00", "end": "12:30", "classId": class_groups[labels[0]],
             "classIds": [class_groups[labels[0]]], "specialMark": "lunch", "protected": True},
            {"id": "fixture-unresolved", "subject": "Unresolved review fixture", "day": 4, "period": 7,
             "start": "14:00", "end": "14:45", "classId": class_groups[labels[0]],
             "classIds": [class_groups[labels[0]]], "resolutionStatus": "review_required",
             "deferred": True, "protected": True, "audience": {"kind": "unresolved"}}]}
    draft = create_experiment(db, name="Synthetic consolidation week", source="import",
                              payload=draft_payload, actor_user_id=admin_id)
    template_draft = create_experiment(db, name="Synthetic template publication", source="canonical",
                                       payload={"draft_kind": "school"}, actor_user_id=admin_id)
    week_draft = create_experiment(db, name="Synthetic independent week publication", source="canonical",
                                   payload={"draft_kind": "school", "week_start": "2026-10-26",
                                            "reference_date": "2026-10-26"}, actor_user_id=admin_id)
    template_preview = preview_publication(db, template_draft["id"], scope_kind="template",
                                           expected_revision=template_draft["revision"])
    week_preview = preview_publication(db, week_draft["id"], scope_kind="week",
                                      expected_revision=week_draft["revision"], week_start="2026-10-26")
    canonical_readback = canonical_version(db)
    template_readback = read_canonical_template(db)
    student_readback = project_student(db, student_ids[labels[0]][0], WEEK)
    teacher_readback = project_teacher(db, teacher_ids[0], WEEK)
    if not canonical_readback or canonical_readback["status"] != "authoritative":
        raise AssertionError("Synthetic baseline was not selected as authoritative canonical")
    if not canonical_readback.get("metadata", {}).get("authoritative"):
        raise AssertionError("Synthetic authoritative metadata was not persisted")
    if not template_readback or len(template_readback.get("blocks") or {}) != len(blocks):
        raise AssertionError("Canonical template readback is incomplete")
    if student_readback["status"] != "authoritative" or not student_readback["items"]:
        raise AssertionError("Student runtime projection did not consume authoritative canonical state")
    if teacher_readback["status"] != "authoritative" or not teacher_readback["items"]:
        raise AssertionError("Teacher runtime projection did not consume authoritative canonical state")
    fixture = {
        "schema": "synthetic-consolidation-v1", "synthetic": True, "week_start": WEEK,
        "canonical_version_id": VERSION, "effective_week_id": effective["effective_week_id"],
        "draft_id": draft["id"], "template_publication_draft_id": template_draft["id"],
        "week_publication_draft_id": week_draft["id"], "admin_user_id": str(admin_id),
        "classes": class_groups, "math_groups": math_groups, "english_group": english_group,
        "students": student_ids, "teachers": teacher_ids,
        "counts": {"classes": len(class_groups), "students": sum(len(v) for v in student_ids.values()),
                   "teachers": len(teacher_ids), "math_groups": len(math_groups),
                   "english_cross_class_memberships": sum(1 for values in student_ids.values() for _ in values[:6])},
        "canonical": {"status": imported["status"], "blocks": imported["blocks"], "assignments": imported["assignments"]},
        "draft": {"id": draft["id"], "revision": draft["revision"]},
        "publication_previews": {"template_can_publish": template_preview["can_publish"],
            "template_blockers": template_preview["blockers"], "template_token": template_preview["preview_token"],
            "week_can_publish": week_preview["can_publish"], "week_blockers": week_preview["blockers"],
            "week_token": week_preview["preview_token"], "week_start": "2026-10-26"},
        "teacher_color_registry": color_seed,
        "regression_states": ["protected", "unresolved", "review_required", "deferred"],
        "runtime_readback": {"canonical_status": canonical_readback["status"],
            "student_status": student_readback["status"], "student_item_count": len(student_readback["items"]),
            "teacher_status": teacher_readback["status"], "teacher_item_count": len(teacher_readback["items"])},
        "warning": "All names, identities, memberships, and schedule records are synthetic fixture data.",
    }
    fixture = ensure_dense_template(db, fixture)
    return fixture
