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

    def test_room_projection_is_normalized_without_rewriting_canonical_storage(self):
        artifact = self.artifact()
        artifact["blocks"][self.block_key]["assignments"][0]["room"] = "Кабинет 16"
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-09-07")

        projection = project_student(self.database, self.student["id"], "2026-09-07")
        first = next(item for item in projection["items"] if item["start_time"] == "09:00")
        restored = read_canonical_template(self.database, self.version_id)

        self.assertEqual(first["room"], "16")
        self.assertEqual(restored["blocks"][self.block_key]["assignments"][0]["metadata"]["room"], "Кабинет 16")

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
        self.assertEqual(result["blocks"][0]["baseline_weekday"], 0)
        self.assertEqual(result["blocks"][0]["baseline_start_time"], "09:00")
        self.assertEqual(result["blocks"][0]["baseline_end_time"], "09:45")
        self.assertEqual(result["blocks"][0]["baseline_grade_scope"], "9")
        self.assertEqual(result["blocks"][0]["effective_assignments"][0]["activity"], "Математика")
        self.assertEqual(result["blocks"][0]["affected_students"]["count"], 2)
        self.assertEqual(result["blocks"][0]["affected_students"]["states"], {"ACTIVITY": 1, "NO_LESSON": 1})

        editor_result = schedule_admin_observability(
            self.database, "2026-09-07", include_student_projection=False
        )
        self.assertTrue(editor_result["student_projection"]["skipped"])
        self.assertEqual(len(editor_result["blocks"]), 2)
        self.assertEqual(editor_result["blocks"][0]["affected_students"]["count"], 0)

    def test_admin_observability_keeps_template_visible_without_weekly_materialization(self):
        import_canonical_artifact(self.database, self.artifact())

        result = schedule_admin_observability(
            self.database, "2026-09-28", include_student_projection=False
        )

        self.assertEqual(result["status"], "canonical_not_materialized")
        self.assertEqual(result["selected_week"], "2026-09-28")
        self.assertEqual(result["canonical"]["blocks"], 2)
        self.assertIsNone(result["effective_week"]["effective_week_id"])
        self.assertEqual(len(result["blocks"]), 2)
        self.assertEqual(result["blocks"][0]["baseline_assignments"][0]["activity"], "Математика")
        self.assertEqual(result["blocks"][0]["effective_assignments"][0]["activity"], "Математика")
        self.assertEqual(result["blocks"][0]["change_kind"], "unchanged")

    def test_admin_history_preserves_versions_across_canonical_releases(self):
        import_canonical_artifact(self.database, self.artifact())
        original = materialize_effective_week(self.database, self.version_id, "2026-09-07")
        changed = materialize_effective_week(self.database, self.version_id, "2026-09-07",
            patches=[{"block_key": self.block_key, "change_kind": "cancelled"}], overlay_fingerprint="changed")
        newer = self.artifact()
        newer["version_id"] = "newer-template"
        newer["source_snapshot"] = {"id": "newer-snapshot", "fingerprint": "newer-fingerprint"}
        import_canonical_artifact(self.database, newer)
        materialize_effective_week(self.database, "newer-template", "2026-09-21")
        result = schedule_admin_observability(self.database, include_student_projection=False)
        self.assertEqual(len(result["effective_weeks"]), 3)
        self.assertEqual({item["week_start"] for item in result["effective_weeks"]}, {"2026-09-07", "2026-09-21"})
        self.assertEqual(result["week_publications"], [])
        self.assertFalse(any(item["is_current_publication"] for item in result["effective_weeks"]))
        historical = schedule_admin_observability(self.database, effective_week_id=original["effective_week_id"], include_student_projection=False)
        self.assertEqual(historical["effective_week"]["effective_week_id"], original["effective_week_id"])
        self.assertEqual(historical["canonical"]["version_id"], self.version_id)
        self.assertNotEqual(original["effective_week_id"], changed["effective_week_id"])

    def test_editing_historical_week_creates_detached_draft_of_exact_overlay(self):
        from backend.services.schedule_experiments import create_effective_week_experiment
        import_canonical_artifact(self.database, self.artifact())
        old = materialize_effective_week(self.database, self.version_id, "2026-09-07")
        materialize_effective_week(self.database, self.version_id, "2026-09-07",
            patches=[{"block_key": self.block_key, "change_kind": "cancelled"}], overlay_fingerprint="new-overlay")
        created = create_effective_week_experiment(self.database, old["effective_week_id"])
        self.assertEqual(created["payload"]["week_start"], "2026-09-07")
        self.assertEqual(len(created["payload"]["lessons"]), 2)
        self.assertEqual(created["payload"]["school_source"]["effective_week_id"], old["effective_week_id"])
        self.assertEqual(create_effective_week_experiment(self.database, old["effective_week_id"])["id"], created["id"])
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM canonical_effective_weeks").fetchone()[0], 2)

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

    def test_corrected_week_creates_a_new_immutable_overlay_revision(self):
        import_canonical_artifact(self.database, self.artifact())
        first = materialize_effective_week(
            self.database, self.version_id, "2026-09-14", patches=[],
            overlay_source_snapshot_id="weekly-snapshot-v1", overlay_fingerprint="source-v1",
        )
        corrected = materialize_effective_week(
            self.database, self.version_id, "2026-09-14", patches=[{
                "block_key": self.block_key, "change_kind": "cancelled",
            }], overlay_source_snapshot_id="weekly-snapshot-v2", overlay_fingerprint="source-v2",
        )
        retry = materialize_effective_week(
            self.database, self.version_id, "2026-09-14", patches=[{
                "block_key": self.block_key, "change_kind": "cancelled",
            }], overlay_source_snapshot_id="weekly-snapshot-v2", overlay_fingerprint="source-v2",
        )

        self.assertNotEqual(first["effective_week_id"], corrected["effective_week_id"])
        self.assertFalse(corrected["idempotent"])
        self.assertTrue(retry["idempotent"])
        projection = project_student(self.database, self.student["id"], "2026-09-14")
        item = next(value for value in projection["items"] if value["start_time"] == "09:00")
        self.assertEqual(item["state"], "NO_LESSON")

    def test_weekly_patch_preserves_baseline_teacher_when_audience_and_cell_match(self):
        import_canonical_artifact(self.database, self.artifact())
        materialize_effective_week(self.database, self.version_id, "2026-09-14", patches=[{
            "block_key": self.block_key,
            "change_kind": "replaced",
            "assignments": [{
                "activity": "Математика",
                "role": "primary",
                "audience": {"type": "canonical_groups", "canonical_group_ids": [str(self.math_group["id"])]},
                "teacher_ids": [],
                "source_cells": ["B3"],
            }],
        }])
        result = schedule_admin_observability(self.database, "2026-09-14")
        effective = result["blocks"][0]["effective_assignments"][0]
        self.assertEqual(effective["teacher_ids"], [str(self.teacher["id"])])
        self.assertEqual(effective["teachers"], ["Teacher One"])
        self.assertEqual(result["teacher_projection"]["unresolved"], 1)

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
