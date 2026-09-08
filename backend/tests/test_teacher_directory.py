from __future__ import annotations

import os
import tempfile
import unittest

from backend.database import Database
from backend.services.teacher_directory import (
    build_assignment_candidates,
    canonical_teacher_name,
    normalize_subjects,
    parse_teacher_rows,
    sync_teacher_directory,
)


class TeacherDirectoryTests(unittest.TestCase):
    def _groups(self, database: Database) -> dict[str, dict]:
        with database.connection() as connection:
            rows = connection.execute("SELECT * FROM groups WHERE canonical IS TRUE").fetchall()
        return {row["name"]: dict(row) for row in rows}

    def test_live_sheet_rows_map_only_existing_canonical_groups(self):
        values = [
            ["Учитель", "Предмет", "Классы и группы"],
            ["Дмитрий Ф", "Математика", "9: группы А, В, С; 11: база"],
            ["Ангелина", "Английский язык", "группы 1, 3, 4, 6, 7, 9"],
            ["Тарас", "Информатика", "9: база и ОГЭ; ЕГЭ 10 и егэ 11"],
        ]
        rows, issues = parse_teacher_rows(values)
        self.assertFalse(issues)
        candidates, mapping_issues = build_assignment_candidates(rows, {
            "grade9-math-A": {"name": "grade9-math-A", "group_type": "subject_group", "subject": "Математика", "base_class_name": "9", "subject_subgroup": "A", "canonical": True},
            "grade9-math-B": {"name": "grade9-math-B", "group_type": "subject_group", "subject": "Математика", "base_class_name": "9", "subject_subgroup": "B", "canonical": True},
            "grade9-math-C": {"name": "grade9-math-C", "group_type": "subject_group", "subject": "Математика", "base_class_name": "9", "subject_subgroup": "C", "canonical": True},
            "math:11:base": {"name": "math:11:base", "group_type": "subject_group", "subject": "Математика", "base_class_name": "11", "subject_subgroup": "Base", "canonical": True},
            **{f"english:{n}": {"name": f"english:{n}", "group_type": "subject_group", "subject": "Английский язык", "subject_subgroup": str(n), "canonical": True} for n in (1, 3, 4, 6, 7, 9)},
            "instructional:информатика:9:база": {"name": "instructional:информатика:9:база", "group_type": "subject_group", "subject": "Информатика", "base_class_name": "9", "subject_subgroup": "База", "canonical": True},
            "instructional:информатика:9:огэ": {"name": "instructional:информатика:9:огэ", "group_type": "subject_group", "subject": "Информатика", "base_class_name": "9", "subject_subgroup": "ОГЭ", "exam_track": "ОГЭ", "canonical": True},
            "instructional:информатика:10:егэ": {"name": "instructional:информатика:10:егэ", "group_type": "subject_group", "subject": "Информатика", "base_class_name": "10", "subject_subgroup": "ЕГЭ", "exam_track": "ЕГЭ", "canonical": True},
            "instructional:информатика:11:егэ": {"name": "instructional:информатика:11:егэ", "group_type": "subject_group", "subject": "Информатика", "base_class_name": "11", "subject_subgroup": "ЕГЭ", "exam_track": "ЕГЭ", "canonical": True},
        })
        self.assertEqual(canonical_teacher_name("ДФ"), "Дмитрий Филиппов")
        self.assertEqual(canonical_teacher_name("Дмитрий Ф"), "Дмитрий Филиппов")
        self.assertEqual(normalize_subjects("Русский язык, литература, лаборатория"), ("Русский язык", "Литература"))
        self.assertEqual(len(candidates), 4 + 6 + 4)
        self.assertFalse(mapping_issues)

    def test_sync_is_idempotent_and_does_not_touch_student_memberships(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "school.db"))
            database.initialize()
            student = database.create_identity("student", "Ученик Тестов", "5")
            class_group = database.create_group("5", "class")
            database.create_membership(class_group["id"], student["id"], "student", "official_import", "base!A1")
            for name in ("5", "6"):
                group = database.get_group(database.create_group(name, "class")["id"])
                with database.connection() as connection:
                    connection.execute("UPDATE groups SET canonical=1, base_class_name=? WHERE id=?", (name, group["id"]))
            rows = [["Учитель", "Предмет", "Классы и группы"], ["Мария", "Математика", "5, 6"]]
            first = sync_teacher_directory(database, rows, spreadsheet_id="sheet", spreadsheet_title="Расписание 2026/27")
            second = sync_teacher_directory(database, rows, spreadsheet_id="sheet", spreadsheet_title="Расписание 2026/27")
            self.assertEqual(first["student_memberships_touched"], 0)
            self.assertEqual(second["student_memberships_touched"], 0)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) AS c FROM memberships WHERE identity_id=? AND active IS TRUE", (student["id"],)).fetchone()["c"], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) AS c FROM teacher_assignments WHERE active IS TRUE", ()).fetchone()["c"], 2)


if __name__ == "__main__":
    unittest.main()
