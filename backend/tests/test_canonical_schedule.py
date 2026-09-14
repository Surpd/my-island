import json
import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.services.canonical_schedule import (
    import_canonical_artifact,
    materialize_effective_week,
    project_student,
    project_teacher_range,
    read_canonical_template,
    schedule_admin_observability,
)


class CanonicalScheduleRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "runtime.db")
        self.database.initialize()
        self.student = self.database.create_identity("student", "Student One", "9A")
        self.other_student = self.database.create_identity("student", "Student Two", "9A")
        self.teacher = self.database.create_identity("teacher", "Teacher One")
        self.base_group = self.database.create_group("9A", "class")
        self.math_group = self.database.create_group("9A · Math A", "instructional")
        self.database.create_membership(self.base_group["id"], self.student["id"], "student", "test")
        self.database.create_membership(self.base_group["id"], self.other_student["id"], "student", "test")
        self.database.create_membership(self.math_group["id"], self.student["id"], "student", "test")
        self.version_id = "runtime-version-1"
        self.block_key = "2026-09-07|0|09:00|9A"

    def tearDown(self):
        self.temp_dir.cleanup()

    def artifact(self):
        base_id = str(self.base_group["id"])
        math_id = str(self.math_group["id"])
        teacher_id = str(self.teacher["id"])
        return {
            "schema_version": "canonical-schedule-bootstrap-v1",
            "version_id": self.version_id,
            "status": "unresolved",
            "authoritative": False,
            "source_snapshot": {"id": "snapshot-1", "fingerprint": "fingerprint-1"},
            "summary": {"blocks": 2},
            "blocks": {
                self.block_key: {
                    "weekday": 0,
                    "slot": {"start": "09:00", "end": "09:45"},
                    "grade_scope": "9",
                    "status": "resolved",
                    "mode": "SINGLE",
                    "derived_from": {"source_cells": ["B3"], "structural_fingerprint": "layout-1"},
                    "evidence": {"raw_cells": ["Математика"]},
                    "assignments": [{
                        "activity": "Математика",
                        "role": "primary",
                        "audience": {"type": "canonical_groups", "canonical_group_ids": [math_id]},
                        "teacher_ids": [teacher_id],
                        "teachers": ["Teacher One"],
                        "room": "101",
                        "source_cells": ["B3"],
                    }],
                },
                "2026-09-07|1|10:00|9A": {
                    "weekday": 1,
                    "slot": {"start": "10:00", "end": "10:45"},
                    "grade_scope": "9",
                    "status": "resolved",
                    "mode": "SINGLE",
                    "derived_from": {"source_cells": ["C4"], "structural_fingerprint": "layout-2"},
                    "assignments": [{
                        "activity": "Общий курс",
                        "role": "primary",
                        "audience": {"type": "canonical_groups", "canonical_group_ids": [base_id]},
                        "teacher_ids": [],
                        "source_cells": ["C4"],
                    }],
                },
            },
        }

    def test_import_is_idempotent_and_round_trips_without_student_sets(self):
        first = import_canonical_artifact(self.database, self.artifact())
        second = import_canonical_artifact(self.database, self.artifact())
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        restored = read_canonical_template(self.database, self.version_id)
        self.assertEqual(restored["source_snapshot"]["fingerprint"], "fingerprint-1")
        assignment = restored["blocks"][self.block_key]["assignments"][0]
        self.assertEqual(assignment["activity"], "Математика")
        self.assertEqual(assignment["canonical_group_ids"], [str(self.math_group["id"])])
        self.assertNotIn("student_ids", json.dumps(restored, ensure_ascii=False))

        with self.database.connection() as connection:
            audit = connection.execute("SELECT action, details FROM audit_log WHERE action='canonical_schedule.imported'").fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual(json.loads(audit["details"])["version_id"], self.version_id)

    def test_projection_derives_membership_and_final_no_lesson(self):
        import_canonical_artifact(self.database, self.artifact())
        materialize_effective_week(self.database, self.version_id, "2026-09-07")
        projection = project_student(self.database, self.student["id"], "2026-09-07")
        first = next(item for item in projection["items"] if item["start_time"] == "09:00")
        self.assertEqual(first["state"], "ACTIVITY")
        self.assertEqual(first["activity"], "Математика")
        self.assertEqual(first["room"], "101")
        self.assertEqual(first["teacher_ids"], [str(self.teacher["id"])])
        self.assertEqual(first["provenance"]["source_cells"], ["B3"])

        other = project_student(self.database, self.other_student["id"], "2026-09-07")
        other_first = next(item for item in other["items"] if item["start_time"] == "09:00")
        self.assertEqual(other_first["state"], "NO_LESSON")
        self.assertEqual(other_first["activity"], "NO_LESSON")

    def test_admin_observability_reads_canonical_and_effective_state(self):
        import_canonical_artifact(self.database, self.artifact())
        materialize_effective_week(self.database, self.version_id, "2026-09-07")

        result = schedule_admin_observability(self.database, "2026-09-07")

        self.assertEqual(result["canonical"]["blocks"], 2)
        self.assertEqual(result["canonical"]["assignments"], 2)
        self.assertEqual(result["effective_week"]["effective_blocks"], 2)
        self.assertEqual(result["student_projection"]["states"]["ACTIVITY"], 3)
        self.assertEqual(result["student_projection"]["states"]["NO_LESSON"], 1)
        self.assertEqual(result["student_projection"]["unresolved"], 0)
        self.assertEqual(result["teacher_projection"]["resolved"], 1)
        self.assertEqual(result["teacher_projection"]["unresolved"], 1)
        self.assertEqual(result["teacher_projection"]["orphan"], 1)
        self.assertEqual(len(result["blocks"]), 2)
        self.assertEqual(result["blocks"][0]["source_cells"], ["B3"])
        self.assertEqual(result["blocks"][0]["effective_assignments"][0]["activity"], "Математика")
        self.assertEqual(result["blocks"][0]["affected_students"]["count"], 2)
        self.assertEqual(result["blocks"][0]["affected_students"]["states"], {"ACTIVITY": 1, "NO_LESSON": 1})

    def test_optional_activity_without_teacher_is_not_teacher_issue(self):
        artifact = self.artifact()
        artifact["version_id"] = "runtime-optional-teacher"
        assignment = artifact["blocks"][self.block_key]["assignments"][0]
        assignment["activity"] = "Творчество"
        assignment["teacher_ids"] = []
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, artifact["version_id"], "2026-09-07")

        result = schedule_admin_observability(self.database, "2026-09-07")

        self.assertEqual(result["teacher_projection"]["unresolved"], 1)
        self.assertEqual(result["teacher_projection"]["orphan"], 1)
        self.assertEqual(result["blocks"][0]["effective_assignments"][0]["teacher_issue"], "")

    def test_cancellation_is_week_overlay_and_teacher_range_is_date_scoped(self):
        import_canonical_artifact(self.database, self.artifact())
        materialize_effective_week(self.database, self.version_id, "2026-09-14", patches=[{
            "block_key": self.block_key,
            "change_kind": "cancelled",
        }], overlay_source_snapshot_id="weekly-snapshot-1")
        projection = project_student(self.database, self.student["id"], "2026-09-14")
        cancelled = next(item for item in projection["items"] if item["start_time"] == "09:00")
        self.assertEqual(cancelled["state"], "NO_LESSON")

        materialize_effective_week(self.database, self.version_id, "2026-09-07")
        teacher_projection = project_teacher_range(self.database, self.teacher["id"], "2026-09-09", "2026-09-09")
        self.assertEqual(teacher_projection["items"], [])

    def test_unknown_canonical_group_is_unresolved_not_no_lesson(self):
        artifact = self.artifact()
        artifact["blocks"][self.block_key]["assignments"][0]["audience"]["canonical_group_ids"] = ["missing-group"]
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-09-07")
        projection = project_student(self.database, self.student["id"], "2026-09-07")
        item = next(item for item in projection["items"] if item["start_time"] == "09:00")
        self.assertEqual(item["state"], "UNRESOLVED")
        self.assertNotEqual(item["activity"], "NO_LESSON")

    def test_parallel_duplicate_assignments_with_same_activity_are_not_conflict(self):
        artifact = self.artifact()
        artifact["blocks"][self.block_key]["assignments"].append(dict(artifact["blocks"][self.block_key]["assignments"][0]))
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-09-07")
        projection = project_student(self.database, self.student["id"], "2026-09-07")
        item = next(item for item in projection["items"] if item["start_time"] == "09:00")
        self.assertEqual(item["state"], "ACTIVITY")
        self.assertEqual(item["activity"], "Математика")
        self.assertEqual(projection["issues"], [])

    def test_secondary_assignment_does_not_reclaim_student_already_routed(self):
        overlay_group = self.database.create_group("9A · OGE Physics", "instructional")
        self.database.create_membership(overlay_group["id"], self.student["id"], "student", "test")
        artifact = self.artifact()
        artifact["version_id"] = "runtime-secondary-filter"
        artifact["blocks"][self.block_key]["assignments"].append({
            "activity": "Физика ОГЭ", "role": "secondary",
            "audience": {"type": "canonical_groups", "canonical_group_ids": [str(overlay_group["id"])]},
            "teacher_ids": [], "source_cells": ["C3"],
        })
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, artifact["version_id"], "2026-09-07")
        projection = project_student(self.database, self.student["id"], "2026-09-07")
        item = next(item for item in projection["items"] if item["start_time"] == "09:00")
        self.assertEqual(item["state"], "ACTIVITY")
        self.assertEqual(item["activity"], "Математика")
        self.assertEqual(projection["issues"], [])

    def test_moved_and_weekly_only_overlay_fields_are_effective(self):
        artifact = self.artifact()
        import_canonical_artifact(self.database, artifact)
        assignment = artifact["blocks"][self.block_key]["assignments"][0]
        weekly_key = "2026-09-14|4|12:00|9A|weekly"
        materialize_effective_week(self.database, self.version_id, "2026-09-14", patches=[
            {"block_key": self.block_key, "change_kind": "moved", "slot": {"start": "11:00", "end": "11:45"}},
            {"block_key": weekly_key, "change_kind": "weekly_only", "weekday": 4,
             "slot": {"start": "12:00", "end": "12:45"}, "grade_scope": "9",
             "assignments": [assignment]},
        ])
        moved = project_student(self.database, self.student["id"], "2026-09-14")
        self.assertTrue(any(item["start_time"] == "11:00" for item in moved["items"]))
        self.assertTrue(any(item["start_time"] == "12:00" for item in moved["items"]))

    def test_candidate_block_shape_is_normalized_without_changing_audience(self):
        artifact = self.artifact()
        artifact["blocks"] = [{
            "block_key": "candidate|row-1",
            "weekday": "Пн",
            "slot": {"start": "9:00 - 9:45", "end": None},
            "grade_scope": "9",
            "row_routing_dimension": "SINGLE",
            "assignments": artifact["blocks"][self.block_key]["assignments"],
        }]
        artifact["version_id"] = "candidate-shape-version"
        imported = import_canonical_artifact(self.database, artifact)
        self.assertEqual(imported["blocks"], 1)
        restored = read_canonical_template(self.database, artifact["version_id"])
        block = restored["blocks"]["candidate|row-1"]
        self.assertEqual(block["weekday"], 0)
        self.assertEqual(block["start_time"], "09:00")
        self.assertEqual(block["end_time"], "09:45")
        self.assertEqual(block["mode"], "SINGLE")


if __name__ == "__main__":
    unittest.main()
