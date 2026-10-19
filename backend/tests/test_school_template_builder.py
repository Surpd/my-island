from __future__ import annotations

import unittest

from backend.services.school_template_builder import project_source_lessons


class SchoolTemplateBuilderTests(unittest.TestCase):
    def setUp(self):
        self.groups = [
            {"id": "c9a", "name": "9-А", "display_name": "9-А", "group_type": "class"},
            {"id": "c9d", "name": "9-Д", "display_name": "9-Д", "group_type": "class"},
            {"id": "c10", "name": "10", "display_name": "10", "group_type": "class"},
            {"id": "e6", "name": "english:6", "group_type": "instructional"},
            *({"id": f"m{letter}", "name": f"grade9-math-{letter}", "group_type": "instructional"}
              for letter in "ABC"),
            {"id": "course", "name": "generic:course-choice", "group_type": "instructional"},
            {"id": "digital", "name": "generic:digital-track", "group_type": "instructional"},
        ]
        self.classes = {"e6": {"c9a", "c9d"}, **{f"m{letter}": {"c9a", "c9d"} for letter in "ABC"}}

    def cell(self, coordinate, raw, owner="9-Ан"):
        return {"source_cell": coordinate, "raw_text": raw, "audience": owner,
                "merged_audiences": [], "weekday": 0, "start_time": "09:00", "end_time": "09:45"}

    def build(self, cells):
        semantics = [{"source_cell": cell["source_cell"], "subject": "Математика" if "Мат" in cell["raw_text"] else "Английский язык",
                      "resolved_identity_ids": ["teacher"], "room": "17"} for cell in cells]
        pairs = {("teacher", group["id"]) for group in self.groups}
        return project_source_lessons(cells, semantics, groups=self.groups,
            teachers=[{"id": "teacher"}], teacher_pairs=pairs, group_classes=self.classes,
            old_source_text={}, old_lessons=[], slots=[{"start": "09:00", "end": "09:45"}], sheet_id="approved")

    def test_cross_class_english_is_one_object_with_two_source_cells(self):
        lessons, issues = self.build([self.cell("B4", "Англ 6", "9-Ан"), self.cell("D4", "Англ 6", "9-Д")])
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0]["groupIds"], ["e6"])
        self.assertEqual(lessons[0]["classIds"], ["c9a", "c9d"])
        self.assertEqual(lessons[0]["source"]["cells"], ["B4", "D4"])
        self.assertFalse(issues)

    def test_math_abc_remain_three_parallel_lessons(self):
        lessons, _ = self.build([self.cell("B4", "Мат A"), self.cell("C4", "Мат B"), self.cell("D4", "Мат C")])
        self.assertEqual({lesson["groupId"] for lesson in lessons}, {"mA", "mB", "mC"})
        self.assertEqual(len(lessons), 3)

    def test_confirmed_class_alias_lunch_has_no_teacher_or_subject_requirement(self):
        lessons, issues = self.build([self.cell("L7", "🥨")])
        self.assertEqual(lessons[0]["classIds"], ["c9a"])
        self.assertEqual(lessons[0]["specialMark"], "lunch")
        self.assertFalse(lessons[0]["teacherIds"])
        self.assertEqual(len(issues), 1)  # Lunch audience still needs confirmation.

    def test_course_direction_is_not_assigned_to_generic_group(self):
        lessons, issues = self.build([self.cell("P4", "Курс по выбору: кино", "10")])
        self.assertEqual(lessons[0]["groupIds"], [])
        self.assertEqual(lessons[0]["audience"]["kind"], "")
        self.assertTrue(issues)

    def test_programming_does_not_silently_take_entire_class_or_digital_roster(self):
        lessons, issues = self.build([self.cell("P4", "Программирование", "10")])
        self.assertEqual(lessons[0]["groupIds"], [])
        self.assertEqual(lessons[0]["classIds"], ["c10"])
        self.assertEqual(lessons[0]["audience"]["kind"], "")
        self.assertTrue(any("цифровому треку" in issue["reason"] for issue in issues))


if __name__ == "__main__":
    unittest.main()
