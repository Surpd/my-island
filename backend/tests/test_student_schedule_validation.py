import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact, materialize_effective_week, project_student
from backend.services.student_schedule_validation import validate_student_projections


class StudentScheduleValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "projection-validation.db")
        self.database.initialize()
        self.student = self.database.create_identity("student", "Student", "9A")
        self.base = self._group("9A", "class", "base_class", "grade9_base_class")
        self.math_a = self._group("Math A", "instructional", "instructional_partition", "grade9_math")
        self.math_b = self._group("Math B", "instructional", "instructional_partition", "grade9_math")
        self.english_6 = self._group("English 6", "instructional", "instructional_partition", "grade9_english")
        self.english_7 = self._group("English 7", "instructional", "instructional_partition", "grade9_english")
        self.oge = self._group("Physics OGE", "exam_track", "elective_or_special", "grade9_oge_physics")
        self.database.create_membership(self.base["id"], self.student["id"], "student", "test")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _group(self, name, group_type, role, dimension):
        group = self.database.create_group(name, group_type)
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE groups SET role=?,semantic_dimension=?,base_class_name='9A' WHERE id=?", (role, dimension, group["id"]))
        return self.database.get_group(group["id"])

    def _assignment(self, activity, group=None, role="primary"):
        groups = [] if group is None else [str(group["id"])]
        return {"activity": activity, "role": role, "audience": {"type": "canonical_groups", "canonical_group_ids": groups}, "source_cells": [f"{activity}:cell"], "teacher_ids": []}

    def _materialize(self, assignments, version="validation-v1", *, patches=None):
        artifact = {
            "schema_version": "canonical-schedule-bootstrap-v1", "version_id": version,
            "status": "approved_with_exceptions", "authoritative": False,
            "source_snapshot": {"id": "snapshot", "fingerprint": version},
            "blocks": {"block": {"weekday": 0, "slot": {"start": "09:00", "end": "09:45"}, "grade_scope": "9", "status": "resolved", "mode": "TEST", "derived_from": {"source_cells": ["A1"]}, "assignments": assignments}},
        }
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, version, "2026-09-21", patches=patches or [])

    def _item(self):
        return project_student(self.database, self.student["id"], "2026-09-21")["items"][0]

    def test_normal_membership_and_residual_assignment(self):
        self.database.create_membership(self.math_a["id"], self.student["id"], "student", "test")
        self._materialize([self._assignment("Math", self.math_a), self._assignment("Study hall", None, "residual"), self._assignment("NO_LESSON", None, "final_state")])
        self.assertEqual(self._item()["activity"], "Math")

    def test_base_plus_specific_selects_specific_without_conflict(self):
        self.database.create_membership(self.math_a["id"], self.student["id"], "student", "test")
        self._materialize([self._assignment("Class activity", self.base), self._assignment("Math", self.math_a)])
        projection = project_student(self.database, self.student["id"], "2026-09-21")
        self.assertEqual(projection["items"][0]["activity"], "Math")
        self.assertNotIn("INCOMPATIBLE_MEMBERSHIP_OVERLAP", {issue["code"] for issue in projection["issues"]})

    def test_groupless_primary_fails_closed(self):
        self._materialize([self._assignment("Mystery", None)])
        projection = project_student(self.database, self.student["id"], "2026-09-21")
        self.assertEqual(projection["items"][0]["state"], "UNRESOLVED")
        self.assertIn("GROUPLESS_PRIMARY_ASSIGNMENT", {issue["code"] for issue in projection["issues"]})

    def test_math_overlap_is_incompatible(self):
        self.database.create_membership(self.math_a["id"], self.student["id"], "student", "test")
        self.database.create_membership(self.math_b["id"], self.student["id"], "student", "test")
        self._materialize([self._assignment("Math A", self.math_a), self._assignment("Math B", self.math_b)])
        projection = project_student(self.database, self.student["id"], "2026-09-21")
        self.assertEqual(projection["items"][0]["state"], "UNRESOLVED")
        issue = next(item for item in projection["issues"] if item["code"] == "INCOMPATIBLE_MEMBERSHIP_OVERLAP")
        self.assertEqual(issue["dimension"], "grade9_math")
        self.assertEqual(len(issue["membership_ids"]), 2)

    def test_english_overlap_is_incompatible(self):
        self.database.create_membership(self.english_6["id"], self.student["id"], "student", "test")
        self.database.create_membership(self.english_7["id"], self.student["id"], "student", "test")
        self._materialize([self._assignment("English 6", self.english_6), self._assignment("English 7", self.english_7)])
        report = validate_student_projections(self.database, "2026-09-21")
        self.assertEqual(report["metrics"]["incompatible_membership_overlaps"], 1)

    def test_different_dimensions_are_not_membership_conflict(self):
        self.database.create_membership(self.math_a["id"], self.student["id"], "student", "test")
        self.database.create_membership(self.oge["id"], self.student["id"], "student", "test")
        self._materialize([self._assignment("Math", self.math_a), self._assignment("Physics OGE", self.oge, "secondary")])
        projection = project_student(self.database, self.student["id"], "2026-09-21")
        self.assertEqual(projection["items"][0]["activity"], "Math")
        self.assertNotIn("INCOMPATIBLE_MEMBERSHIP_OVERLAP", {issue["code"] for issue in projection["issues"]})

    def test_override_exclude_and_membership_validity_are_applied_by_lesson_date(self):
        membership = self.database.create_membership(self.math_a["id"], self.student["id"], "student", "test")
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE memberships SET valid_from='2026-09-22' WHERE id=?", (membership["id"],))
        self._materialize([self._assignment("Math", self.math_a), self._assignment("NO_LESSON", None, "final_state")])
        self.assertEqual(self._item()["state"], "NO_LESSON")
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE memberships SET valid_from='2026-09-01' WHERE id=?", (membership["id"],))
            self.database.execute(connection, "INSERT INTO membership_overrides(identity_id,group_id,member_role,action,reason,active) VALUES (?,?, 'student','exclude','test',TRUE)", (self.student["id"], self.math_a["id"]))
        self.assertEqual(self._item()["state"], "NO_LESSON")

    def test_inactive_membership_and_identity_do_not_match_and_are_reported_safely(self):
        membership = self.database.create_membership(self.math_a["id"], self.student["id"], "student", "test")
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE memberships SET active=FALSE WHERE id=?", (membership["id"],))
        self._materialize([self._assignment("Math", self.math_a), self._assignment("NO_LESSON", None, "final_state")])
        self.assertEqual(self._item()["state"], "NO_LESSON")
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE memberships SET active=TRUE WHERE id=?", (membership["id"],))
            self.database.execute(connection, "UPDATE identities SET status='inactive' WHERE id=?", (self.student["id"],))
        report = validate_student_projections(self.database, "2026-09-21")
        self.assertEqual(report["metrics"]["issues_by_code"]["STALE_OR_INVALID_MEMBERSHIP"], 2)

    def test_membership_mutation_is_visible_without_database_restart(self):
        self._materialize([self._assignment("Math", self.math_a), self._assignment("NO_LESSON", None, "final_state")])
        self.assertEqual(self._item()["state"], "NO_LESSON")
        self.database.create_membership(self.math_a["id"], self.student["id"], "student", "test")
        self.assertEqual(self._item()["activity"], "Math")

    def test_uncovered_partition_is_unresolved(self):
        self._materialize([self._assignment("Math A", self.math_a), self._assignment("Math B", self.math_b), self._assignment("NO_LESSON", None, "final_state")])
        projection = project_student(self.database, self.student["id"], "2026-09-21")
        self.assertEqual(projection["items"][0]["state"], "UNRESOLVED")
        self.assertIn("UNCOVERED_PARTITION_MEMBER", {issue["code"] for issue in projection["issues"]})

    def test_applicable_activity_cannot_become_unexpected_no_lesson(self):
        self.database.create_membership(self.math_a["id"], self.student["id"], "student", "test")
        self._materialize([self._assignment("NO_LESSON", None, "final_state"), self._assignment("Math", self.math_a)])
        projection = project_student(self.database, self.student["id"], "2026-09-21")
        self.assertEqual(projection["items"][0]["activity"], "Math")
        self.assertNotIn("UNEXPECTED_NO_LESSON", {issue["code"] for issue in projection["issues"]})

    def test_selection_is_independent_of_assignment_ordinal(self):
        self.database.create_membership(self.math_a["id"], self.student["id"], "student", "test")
        first = [self._assignment("Class activity", self.base), self._assignment("Math", self.math_a)]
        self._materialize(first, "order-one")
        one = project_student(self.database, self.student["id"], "2026-09-21")["items"][0]["activity"]
        self._materialize(list(reversed(first)), "order-two")
        two = project_student(self.database, self.student["id"], "2026-09-21")["items"][0]["activity"]
        self.assertEqual((one, two), ("Math", "Math"))

    def test_weekly_replacement_without_audience_is_reported(self):
        self._materialize(
            [self._assignment("Class activity", self.base)],
            patches=[{"block_key": "block", "change_kind": "replaced", "assignments": [self._assignment("Replacement", None)]}],
        )
        report = validate_student_projections(self.database, "2026-09-21")
        self.assertEqual(report["metrics"]["issues_by_code"]["WEEKLY_REPLACEMENT_LOST_AUDIENCE"], 1)
        self.assertIn("GROUPLESS_PRIMARY_ASSIGNMENT", report["metrics"]["issues_by_code"])

    def test_moved_slot_mismatch_is_reported(self):
        self._materialize(
            [self._assignment("Class activity", self.base)],
            patches=[{"block_key": "block", "change_kind": "moved", "weekday": 1, "slot": {"start": "10:00", "end": "10:45"}, "moved_to": [2, "11:00", "11:45", "9"]}],
        )
        report = validate_student_projections(self.database, "2026-09-21")
        self.assertEqual(report["metrics"]["issues_by_code"]["WRONG_EFFECTIVE_SLOT"], 1)


if __name__ == "__main__":
    unittest.main()
