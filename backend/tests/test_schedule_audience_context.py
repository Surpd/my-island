import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact, materialize_effective_week, project_student
from backend.services.schedule_drafts import audience_preview


class ScheduleAudienceContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "audience.db")
        self.database.initialize()
        self.students = {
            name: self.database.create_identity("student", name, grade)
            for name, grade in (("Five A", "5"), ("Five B", "5"), ("Six A", "6"),
                                ("Nine A", "9"), ("Nine B", "9"), ("Nine C", "9"))
        }
        self.classes = {grade: self.database.create_group(grade, "class") for grade in ("5", "6", "9")}
        for name, student in self.students.items():
            self.database.create_membership(self.classes[student["class_name"]]["id"], student["id"], "student", "test")
        self.english = self.database.create_group("English 1", "instructional")
        for name in ("Five A", "Six A"):
            self.database.create_membership(self.english["id"], self.students[name]["id"], "student", "test")
        self.math = {label: self.database.create_group(f"Math {label}", "instructional") for label in "ABC"}
        for label in "ABC":
            self.database.create_membership(self.math[label]["id"], self.students[f"Nine {label}"]["id"], "student", "test")

    def tearDown(self):
        self.temp.cleanup()

    def names(self, rule):
        return {item["display_name"] for item in audience_preview(self.database, rule)["students"]}

    def test_whole_class_and_single_instructional_group(self):
        self.assertEqual(self.names({"kind": "whole_grade", "grade": "5"}), {"Five A", "Five B"})
        self.assertEqual(self.names({"kind": "groups", "grade": "5", "group_ids": [str(self.english["id"])]}), {"Five A"})

    def test_group_and_class_remainder_use_memberships(self):
        rule = {"kind": "remaining", "grade": "5", "partition_group_ids": [str(self.english["id"])]}
        self.assertEqual(self.names(rule), {"Five B"})
        self.assertNotIn("Six A", self.names(rule))

    def test_remainder_uses_exact_base_class_within_one_grade(self):
        base_a = self.database.create_group("7-А", "class")
        base_b = self.database.create_group("7-Б", "class")
        split = self.database.create_group("English 7", "instructional")
        for name, class_group in (("Seven A 1", base_a), ("Seven A 2", base_a), ("Seven B", base_b)):
            student = self.database.create_identity("student", name, class_group["name"])
            self.database.create_membership(class_group["id"], student["id"], "student", "test")
            if name != "Seven A 2":
                self.database.create_membership(split["id"], student["id"], "student", "test")
        self.assertEqual(self.names({"kind": "groups", "grade": "7", "class_group_id": str(base_a["id"]),
                                     "group_ids": [str(split["id"])]}), {"Seven A 1"})
        self.assertEqual(self.names({"kind": "remaining", "grade": "7", "class_group_id": str(base_a["id"]),
                                     "partition_group_ids": [str(split["id"])]}), {"Seven A 2"})

    def test_math_abc_and_two_groups_with_remainder(self):
        ids = [str(self.math[label]["id"]) for label in "ABC"]
        self.assertEqual(self.names({"kind": "groups", "grade": "9", "group_ids": ids}), {"Nine A", "Nine B", "Nine C"})
        self.assertEqual(self.names({"kind": "remaining", "grade": "9", "partition_group_ids": ids[:2]}), {"Nine C"})

    def test_unknown_group_does_not_become_whole_class(self):
        self.assertEqual(self.names({"kind": "groups", "grade": "5", "group_ids": ["missing"]}), set())

    def test_cross_class_english_projects_to_each_grid(self):
        group_id = str(self.english["id"])
        self.assertEqual(self.names({"kind": "groups", "grade": "5", "group_ids": [group_id]}), {"Five A"})
        self.assertEqual(self.names({"kind": "groups", "grade": "6", "group_ids": [group_id]}), {"Six A"})
        self.assertEqual(self.names({"kind": "groups", "group_ids": [group_id]}), {"Five A", "Six A"})
        blocks = {}
        for grade in ("5", "6"):
            key = f"2026-09-21|0|09:00|{grade}"
            blocks[key] = {"weekday": 0, "slot": {"start": "09:00", "end": "09:45"},
                           "grade_scope": grade, "status": "resolved",
                           "assignments": [{"activity": f"Английский {grade}",
                                            "audience": {"type": "canonical_groups", "canonical_group_ids": [group_id]}}]}
        import_canonical_artifact(self.database, {"schema_version": "test", "version_id": "cross-class",
            "source_snapshot": {"id": "cross-source", "fingerprint": "cross-source"}, "blocks": blocks}, status="approved_baseline")
        materialize_effective_week(self.database, "cross-class", "2026-09-21")
        five = project_student(self.database, str(self.students["Five A"]["id"]), "2026-09-21")
        six = project_student(self.database, str(self.students["Six A"]["id"]), "2026-09-21")
        self.assertEqual([item["activity"] for item in five["items"] if item.get("activity")], ["Английский 5"])
        self.assertEqual([item["activity"] for item in six["items"] if item.get("activity")], ["Английский 6"])

    def test_explicit_students_and_class_intersection(self):
        self.assertEqual(self.names({"kind": "students", "include_student_ids": [str(self.students["Five B"]["id"])]}), {"Five B"})
        self.assertEqual(self.names({"kind": "groups", "grade": "5", "class_group_id": str(self.classes["5"]["id"]),
                                     "group_ids": [str(self.english["id"])]}), {"Five A"})
        self.assertEqual(self.names({"kind": "groups", "grade": "5", "class_group_id": str(self.classes["6"]["id"]),
                                     "group_ids": [str(self.english["id"])]}), set())

    def test_cross_class_event_without_single_class_context_uses_full_group(self):
        key = "2026-09-21|0|09:00|5,6"
        import_canonical_artifact(self.database, {"schema_version": "test", "version_id": "joint",
            "source_snapshot": {"id": "joint-source", "fingerprint": "joint-source"},
            "blocks": {key: {"weekday": 0, "slot": {"start": "09:00", "end": "09:45"},
                "grade_scope": "5,6", "status": "resolved", "assignments": [{"activity": "Общий английский",
                    "audience": {"type": "canonical_groups", "canonical_group_ids": [str(self.english["id"])]}}]}}},
            status="approved_baseline")
        materialize_effective_week(self.database, "joint", "2026-09-21")
        for name in ("Five A", "Six A"):
            projected = project_student(self.database, str(self.students[name]["id"]), "2026-09-21")
            self.assertEqual([item["activity"] for item in projected["items"] if item.get("activity")], ["Общий английский"])

    def test_unknown_canonical_group_is_reported_unresolved(self):
        key = "2026-09-21|0|09:00|5"
        import_canonical_artifact(self.database, {"schema_version": "test", "version_id": "unknown",
            "source_snapshot": {"id": "unknown-source", "fingerprint": "unknown-source"},
            "blocks": {key: {"weekday": 0, "slot": {"start": "09:00", "end": "09:45"},
                "grade_scope": "5", "status": "unresolved", "assignments": [{"activity": "Английский",
                    "audience": {"type": "canonical_groups", "canonical_group_ids": ["missing"]}}]}}},
            status="approved_with_exceptions")
        materialize_effective_week(self.database, "unknown", "2026-09-21")
        projected = project_student(self.database, str(self.students["Five A"]["id"]), "2026-09-21")
        self.assertTrue(any(issue["code"] == "UNKNOWN_CANONICAL_GROUP" for issue in projected["issues"]))


if __name__ == "__main__":
    unittest.main()
