from __future__ import annotations

import os
import tempfile
import unittest

from backend.database import Database


class SchoolDirectoryAdminProjectionTests(unittest.TestCase):
    def test_people_projection_separates_class_instructional_and_exam_memberships(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "school.db"))
            database.initialize()
            student = database.create_identity("student", "Тестова Алиса", "9-А")
            stale = database.create_identity("student", "Старая Запись", "9-А")
            base = database.create_group("9-А", "class")
            instructional = database.create_group("grade9-math-A", "subject_group")
            exam = database.create_group("9-А · Биология · ОГЭ", "subject_group")
            with database.connection() as connection:
                connection.execute(
                    "UPDATE groups SET display_name = '9-А · базовый класс', base_class_name = '9-А' WHERE id = ?",
                    (base["id"],),
                )
                connection.execute(
                    "UPDATE groups SET display_name = 'Математика · группа A · 9 класс', subject = 'Математика', subject_subgroup = 'A' WHERE id = ?",
                    (instructional["id"],),
                )
                connection.execute(
                    "UPDATE groups SET display_name = 'Биология · 9 класс · ОГЭ', subject = 'Биология', base_class_name = '9', exam_track = 'ОГЭ' WHERE id = ?",
                    (exam["id"],),
                )
            database.create_membership(base["id"], student["id"], "student", "official_import", "base!A1")
            database.create_membership(instructional["id"], student["id"], "student", "official_import", "groups!A1")
            database.create_membership(instructional["id"], student["id"], "student", "admin_override", "")
            database.create_membership(exam["id"], student["id"], "student", "official_import", "exam!A1")
            with database.connection() as connection:
                connection.execute(
                    """INSERT INTO student_selection_facts
                       (identity_id, academic_year, grade_level, subject, selection_kind, source, source_ref)
                       VALUES (?, '2026/27', '9', 'Биология', 'oge', 'official_import', 'choices!H2')""",
                    (student["id"],),
                )
            database.create_membership(base["id"], stale["id"], "student", "official_import", "base!A2")
            with database.connection() as connection:
                connection.execute("UPDATE identities SET status = 'inactive' WHERE id = ?", (stale["id"],))
                connection.execute("UPDATE groups SET canonical = 0 WHERE id = ?", (exam["id"],))

            people = database.list_people_library(kind="student")
            self.assertEqual([item["display_name"] for item in people], ["Тестова Алиса"])
            person = people[0]
            self.assertEqual(person["base_class"]["name"], "9-А")
            self.assertEqual([item["name"] for item in person["instructional_memberships"]], ["9-А · Биология · ОГЭ", "grade9-math-A"])
            math_membership = next(
                item for item in person["instructional_memberships"] if item["name"] == "grade9-math-A"
            )
            self.assertTrue(math_membership["is_manual"])
            self.assertEqual(len(person["groups"]), 3)
            self.assertEqual([item["subject"] for item in person["exam_profile_memberships"]], ["Биология"])
            self.assertEqual([item["name"] for item in person["instructional_memberships"]], ["9-А · Биология · ОГЭ", "grade9-math-A"])
            self.assertEqual(len(database.list_people_library(kind="student", class_name="9-А")), 1)
            self.assertEqual(len(database.list_people_library(kind="student", group_id=instructional["id"])), 1)
            self.assertNotIn(exam["id"], {item["id"] for item in database.list_groups_admin()})

            group = next(item for item in database.list_groups_admin() if item["id"] == instructional["id"])
            self.assertEqual([item["display_name"] for item in group["students"]], ["Тестова Алиса"])
            self.assertEqual(group["relationship_kind"], "instructional")


if __name__ == "__main__":
    unittest.main()
