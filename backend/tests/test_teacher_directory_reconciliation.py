import unittest

from backend.services.teacher_directory_reconciliation import (
    WHOLE_CLASS,
    build_teacher_reconciliation_plan,
    normalize_audience,
    normalize_grade,
    parse_structured_teacher_rows,
    render_teacher_reconciliation_report,
)


def group(name, group_type="subject_group", subject=None, base=None, subgroup=None, track=None):
    return {
        "id": f"id:{name}",
        "name": name,
        "group_type": group_type,
        "subject": subject,
        "base_class_name": base,
        "subject_subgroup": subgroup,
        "exam_track": track,
        "canonical": True,
    }


class TeacherDirectoryReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.groups = {
            name: group(name, "class", base=name)
            for name in ("5", "6", "7-1", "7-2", "8", "9-А", "9-Д", "10", "11")
        }
        self.groups.update({
            "grade9-math-A": group("grade9-math-A", subject="Математика", base="9", subgroup="A"),
            "grade9-math-B": group("grade9-math-B", subject="Математика", base="9", subgroup="B"),
            "grade9-math-C": group("grade9-math-C", subject="Математика", base="9", subgroup="C"),
            "english:8": group("english:8", subject="Английский язык", subgroup="8"),
            "math:11:base": group("math:11:base", subject="Математика", base="11", subgroup="Base"),
            "math:11:advanced": group("math:11:advanced", subject="Математика", base="11", subgroup="Advanced"),
            "instructional:география:9:огэ": group("instructional:география:9:огэ", subject="География", base="9", subgroup="ОГЭ", track="ОГЭ"),
            "instructional:обществознание:9:база": group("instructional:обществознание:9:база", subject="Обществознание", base="9", subgroup="База"),
            "instructional:обществознание:9:огэ": group("instructional:обществознание:9:огэ", subject="Обществознание", base="9", subgroup="ОГЭ", track="ОГЭ"),
            "instructional:обществознание:10:база": group("instructional:обществознание:10:база", subject="Обществознание", base="10", subgroup="База"),
            "instructional:обществознание:10:угл": group("instructional:обществознание:10:угл", subject="Обществознание", base="10", subgroup="Угл"),
            "instructional:физика:10:егэ": group("instructional:физика:10:егэ", subject="Физика", base="10", subgroup="ЕГЭ", track="ЕГЭ"),
            "instructional:физика:11:егэ": group("instructional:физика:11:егэ", subject="Физика", base="11", subgroup="ЕГЭ", track="ЕГЭ"),
        })

    def test_normalization_and_blank_rows(self):
        self.assertEqual(normalize_grade("9-а"), "9-А")
        self.assertEqual(normalize_grade("9 — д"), "9-Д")
        self.assertEqual(normalize_audience("весь класс")[0], WHOLE_CLASS)
        rows, issues, blanks = parse_structured_teacher_rows([
            ["Учитель", "Предмет", "Класс", "Группа / аудитория", "Совместный урок", "Примечание"],
            ["Иван", "Математика", "9-а", "Группа C", None, None],
            [],
            ["Ангелина", "Английский язык", None, "Группа 8 / ОГЭ", None, None],
        ])
        self.assertEqual(len(rows), 2)
        self.assertFalse(issues)
        self.assertEqual(blanks, [3])

    def test_exact_teacher_matching_does_not_fuzzy_merge(self):
        values = [["Учитель", "Предмет", "Класс", "Группа / аудитория", "Совместный урок", "Примечание"], ["Иванн", "Математика", "9", "Весь класс", None, None]]
        plan = build_teacher_reconciliation_plan(values, canonical_teachers=[{"id": "t1", "display_name": "Иван"}], canonical_groups=self.groups, canonical_subjects={"Математика"}, current_assignments=[])
        self.assertEqual(plan.counts["unmatched_teacher"], 1)
        self.assertEqual(plan.counts["proposed_create_assignments"], 0)

    def test_structural_rules_cover_groups_shared_lesson_and_computed_audience(self):
        values = [
            ["Учитель", "Предмет", "Класс", "Группа / аудитория", "Совместный урок", "Примечание"],
            ["Дмитрий Ф", "Математика", "9", "Группа A", None, None],
            ["Игорь", "Английский язык", None, "Группа 8 / ОГЭ", None, None],
            ["Антон", "География", "9", "ОГЭ", None, "база+доп"],
            ["Леонид", "Обществознание", "10", "ЕГЭ", "База +доп", "занимаются вместе"],
            ["Антон", "География", "11", "Дополнительная группа", None, "только те, кто не ходит на ЕГЭ по обществознанию"],
        ]
        teachers = [
            {"id": "t-d", "display_name": "Дмитрий Филиппов"},
            {"id": "t-i", "display_name": "Игорь"},
            {"id": "t-a", "display_name": "Антон"},
            {"id": "t-l", "display_name": "Леонид"},
        ]
        current = [{"teacher_id": "t-d", "group_id": "id:grade9-math-A", "subject": "Математика", "source": "manual_confirmation", "source_ref": "manual"}]
        plan = build_teacher_reconciliation_plan(values, canonical_teachers=teachers, canonical_groups=self.groups, canonical_subjects={"Математика", "Английский язык", "География", "Обществознание"}, current_assignments=current)
        names = {(item.teacher_name, item.group_name, item.subject) for item in plan.assignments}
        self.assertIn(("Дмитрий Филиппов", "grade9-math-A", "Математика"), names)
        self.assertIn(("Игорь", "english:8", "Английский язык"), names)
        self.assertIn(("Антон", "9-А", "География"), names)
        self.assertIn(("Антон", "9-Д", "География"), names)
        self.assertIn(("Антон", "instructional:география:9:огэ", "География"), names)
        society = [item for item in plan.assignments if item.teacher_name == "Леонид"]
        self.assertEqual({item.group_name for item in society}, {"instructional:обществознание:10:база", "instructional:обществознание:10:угл"})
        self.assertEqual(plan.counts["computed_schedule_audiences"], 1)
        self.assertEqual(plan.counts["student_memberships_touched"], 0)
        self.assertEqual(plan.counts["production_apply_performed"], 0)
        report = render_teacher_reconciliation_report(plan, spreadsheet_id="sheet", spreadsheet_title="Расписание")
        self.assertIn("grade 11 students without Society EGE join Geography 10", report)
        self.assertIn("Student memberships touched: **0**", report)

    def test_math_11_base_and_ege_are_distinct(self):
        values = [["Учитель", "Предмет", "Класс", "Группа / аудитория", "Совместный урок", "Примечание"], ["Дмитрий Ф", "Математика", "11", "База", None, None], ["Анна", "Математика", "11", "ЕГЭ", None, None]]
        plan = build_teacher_reconciliation_plan(values, canonical_teachers=[{"id": "t-d", "display_name": "Дмитрий Филиппов"}, {"id": "t-a", "display_name": "Анна Владимировна"}], canonical_groups=self.groups, canonical_subjects={"Математика"}, current_assignments=[])
        self.assertEqual({item.group_name for item in plan.assignments}, {"math:11:base", "math:11:advanced"})

    def test_unresolved_audience_guards_old_assignment_from_deactivation(self):
        values = [["Учитель", "Предмет", "Класс", "Группа / аудитория", "Совместный урок", "Примечание"], ["Ирина Анатольевна", "Литература", "11", "База", "отдельная группа", None]]
        current = [{"teacher_id": "t-i", "teacher_name": "Ирина Анатольевна", "group_id": "id:11", "group_name": "11", "base_class_name": "11", "subject": "Литература", "source": "official_import", "source_ref": "Учителя и группы!A20:C20"}]
        plan = build_teacher_reconciliation_plan(values, canonical_teachers=[{"id": "t-i", "display_name": "Ирина Анатольевна"}], canonical_groups=self.groups, canonical_subjects={"Литература"}, current_assignments=current)
        self.assertEqual(plan.counts["proposed_deactivate_assignments"], 0)
        self.assertEqual(plan.counts["manual_assignments_needing_confirmation"], 1)


if __name__ == "__main__":
    unittest.main()
