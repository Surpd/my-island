import unittest

from backend.services.ninth_grade_resolver import NinthGradeResolver, format_row_diagnostic, learn_partition_lanes
from backend.services.ninth_grade_resolver import compare_v1_v2
from backend.services.schedule_parser_v2 import NinthGradeContext, ScheduleAssignment, ScheduleRowMode, detect_sdep_day, group_schedule_rows
from backend.services.schedule_pipeline import _parse_cell
from backend.tests.fixtures.schedule_parser_v2_fixtures import REAL_GRADE9_ROWS, REGRESSION_SCENARIOS


def cell(source_cell, column, text, audience="9-Д", weekday=1, source_day_label=""):
    return {
        "record_key": source_cell, "sheet_id": "sheet", "tab_title": "8-12 Сентября",
        "source_cell": source_cell, "source_column": column, "source_row": 3,
        "lesson_date": "2026-09-08", "weekday": weekday, "start_time": "09:00", "end_time": "09:45",
        "audience": audience, "raw_text": text, "subject": text, "modifiers": {},
        "source_day_label": source_day_label,
    }


class NinthGradeResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = NinthGradeResolver()

    def context(self):
        base_a = {"id": "base-a", "name": "9-А", "display_name": "9-А", "group_type": "class"}
        base_d = {"id": "base-d", "name": "9-Д", "display_name": "9-Д", "group_type": "class"}
        math = [{"id": f"math-{lane}", "name": f"Math {lane}", "display_name": f"Math {lane}", "subject": "Математика", "subject_subgroup": lane} for lane in "ABC"]
        english = [{"id": f"eng-{lane}", "name": f"English {lane}", "display_name": f"English {lane}", "subject": "Английский", "subject_subgroup": lane} for lane in ("6", "7", "8")]
        exam = [{"id": f"oge-{subject.casefold()}", "name": f"{subject} OGE", "display_name": f"{subject} OGE", "subject": subject, "exam_track": "ОГЭ"} for subject in ("Химия", "Физика", "География", "Литература")]
        memberships = {
            "base-a": frozenset({"a1", "a2", "a3", "a4"}), "base-d": frozenset({"d1", "d2", "d3", "d4", "d5"}),
            "math-A": frozenset({"d1"}), "math-B": frozenset({"d2"}), "math-C": frozenset({"d3"}),
            "eng-6": frozenset({"d1"}), "eng-7": frozenset({"d2"}), "eng-8": frozenset({"d3"}),
            "oge-химия": frozenset({"d4"}), "oge-физика": frozenset({"d2", "d5"}), "oge-география": frozenset({"d1"}), "oge-литература": frozenset({"d3"}),
        }
        return NinthGradeContext(frozenset({"a1", "a2", "a3", "a4", "d1", "d2", "d3", "d4", "d5"}), (base_a, base_d), tuple(math), tuple(english), tuple(exam), memberships)

    def row(self, cells):
        return group_schedule_rows(cells)[0]

    def learned(self, context, family="math", columns=(12, 13, 14), lanes=("A", "B", "C")):
        prefix = "Math" if family == "math" else "English"
        calibration = self.row([cell(f"{chr(76 + index)}2", column, f"{prefix} {lane}")
                                for index, (column, lane) in enumerate(zip(columns, lanes))])
        return learn_partition_lanes(context, [calibration])

    def real_row(self, name, weekday=0):
        values = []
        for source, column, text, audience in REAL_GRADE9_ROWS[name]:
            parsed = cell(source, column, text, audience, weekday)
            parsed.update(_parse_cell(text, audience))
            parsed["raw_text"] = text
            values.append(parsed)
        return self.row(values)

    def test_math_anchor_does_not_interpret_neighbouring_cells(self):
        result = NinthGradeResolver(self.context()).interpret(self.row([
            cell("X3", 23, "Math A"), cell("Y3", 24, "Обед"), cell("Z3", 25, "История искусства"),
        ]))
        self.assertEqual(result.mode, ScheduleRowMode.MATH)
        self.assertEqual(result.evidence["math_anchor"], "A")
        self.assertEqual(result.evidence["no_lesson_cells"], ["Y3"])
        self.assertEqual(result.evidence["source_columns"], {"X3": 23, "Y3": 24, "Z3": 25})

    def test_english_anchor_and_oge_are_diagnostic_only(self):
        result = self.resolver.interpret(self.row([cell("X3", 23, "English 7"), cell("Y3", 24, "Physics OGE")]))
        self.assertEqual(result.mode, ScheduleRowMode.UNKNOWN)
        self.assertEqual(result.evidence["english_anchor"], "7")
        self.assertEqual(result.evidence["oge_cells"], ["Y3"])
        self.assertEqual(result.primary_assignments, [])

    def test_simple_activity_has_no_teacher_requirement(self):
        result = self.resolver.interpret(self.row([cell("X3", 23, "Тренинг")]))
        self.assertEqual(result.mode, ScheduleRowMode.SPECIAL)
        self.assertEqual(result.evidence["simple_activities"], {"X3": "Тренинг"})

    def test_sdep_requires_explicit_source_day_marker(self):
        row = self.row([cell("X3", 23, "Самостоятельная работа", audience="10", weekday=4,
                                  source_day_label="Пт SDEP")])
        self.assertTrue(detect_sdep_day([row]))
        plain_friday = self.row([cell("X4", 23, "SDEP весь день", audience="10", weekday=4,
                                           source_day_label="Пт")])
        self.assertFalse(detect_sdep_day([plain_friday]))
        self.assertEqual(self.resolver.interpret(row).mode, ScheduleRowMode.CLASS)

    def test_diagnostic_is_human_readable(self):
        row = self.row([cell("X3", 23, "Математика A"), cell("Y3", 24, "Обед"), cell("Z3", 25, "История искусства")])
        result = NinthGradeResolver(self.context()).interpret(row)
        diagnostic = format_row_diagnostic(row, result)
        self.assertIn('col 23: X3 "Математика A"', diagnostic)
        self.assertIn("possible mode: MATH", diagnostic)
        self.assertIn("math: A", diagnostic)

    def test_math_lane_mapping_and_no_lesson_default(self):
        resolver = NinthGradeResolver(self.learned(self.context()))
        row = self.row([cell("L3", 12, "Math A"), cell("M3", 13, "Обед"), cell("N3", 14, "История искусства")])
        result = resolver.interpret(row)
        self.assertEqual(result.mode, ScheduleRowMode.MATH)
        self.assertEqual([(item.activity, set(item.student_ids)) for item in result.primary_assignments], [("Math A", {"d1"})])
        self.assertEqual(result.default_assignment.activity, "История искусства")
        self.assertEqual(set(result.default_assignment.student_ids), {"d2", "d3", "d4", "d5"})
        self.assertEqual(set(result.evidence["remaining_student_ids"]), {"a1", "a2", "a3", "a4"})
        self.assertEqual(set(result.final_assignments[0].student_ids), set(result.evidence["remaining_student_ids"]))

    def test_math_oge_overlay_only_uses_remaining_students(self):
        resolver = NinthGradeResolver(self.context())
        result = resolver.interpret(self.row([cell("L3", 12, "Math B"), cell("M3", 13, "Химия ОГЭ"), cell("N3", 14, "свободны")]))
        self.assertEqual(result.mode, ScheduleRowMode.MATH_WITH_ELECTIVES)
        self.assertEqual(set(result.primary_assignments[0].student_ids), {"d2"})
        self.assertEqual(set(result.secondary_assignments[0].student_ids), {"d4"})
        self.assertIsNone(result.default_assignment)
        self.assertEqual(set(result.evidence["remaining_student_ids"]), {"a1", "a2", "a3", "a4", "d1", "d3", "d5"})

    def test_math_c_multiple_oge_and_pure_electives(self):
        resolver = NinthGradeResolver(self.context())
        mixed = resolver.interpret(self.row([cell("L3", 12, "Math C"), cell("M3", 13, "Физика OGE"), cell("N3", 14, "Химия ОГЭ"), cell("O3", 15, "Перерыв")]))
        self.assertEqual(set(mixed.primary_assignments[0].student_ids), {"d3"})
        self.assertEqual([set(item.student_ids) for item in mixed.secondary_assignments], [{"d2", "d5"}, {"d4"}])
        self.assertIsNone(mixed.default_assignment)
        self.assertEqual(set(mixed.evidence["remaining_student_ids"]), {"a1", "a2", "a3", "a4", "d1"})
        pure = resolver.interpret(self.row([cell("L4", 12, "Физика OGE"), cell("M4", 13, "География OGE"), cell("N4", 14, "Литература ОГЭ")]))
        self.assertEqual(pure.mode, ScheduleRowMode.ELECTIVES)
        self.assertEqual(pure.primary_assignments, [])
        self.assertIsNone(pure.default_assignment)
        self.assertEqual(set(pure.evidence["remaining_student_ids"]), {"a1", "a2", "a3", "a4", "d4"})

    def test_english_complement_is_default_not_group_guess(self):
        resolver = NinthGradeResolver(self.context())
        result = resolver.interpret(self.row([cell("L3", 12, "English 7"), cell("M3", 13, "English 8"), cell("N3", 14, "Русский")]))
        self.assertEqual(result.mode, ScheduleRowMode.ENGLISH)
        self.assertEqual(set(result.default_assignment.student_ids), {"d1", "d4", "d5"})
        self.assertEqual(result.default_assignment.activity, "Русский")

    def test_ordinary_subject_in_partition_row_uses_its_class_audience(self):
        context = self.learned(self.context(), family="english", columns=(11, 12, 13), lanes=("6", "7", "8"))
        result = NinthGradeResolver(context).interpret(self.row([
            cell("L10", 11, "Англ 6 Ангелина", audience="9-А"),
            cell("M10", 12, "Русский ЕВ", audience="9-Д"),
        ]))
        self.assertEqual(result.mode, ScheduleRowMode.ENGLISH)
        self.assertEqual([(item.activity, set(item.student_ids)) for item in result.primary_assignments], [("Англ 6 Ангелина", {"d1"})])
        self.assertEqual(result.default_assignment.activity, "Русский ЕВ")
        self.assertEqual(set(result.default_assignment.student_ids), {"d2", "d3", "d4", "d5"})
        self.assertEqual(result.default_assignment.group_ids, ("base-d",))

    def test_class_split_does_not_use_subject_groups(self):
        resolver = NinthGradeResolver(self.context())
        result = resolver.interpret(self.row([cell("L3", 12, "Литература", "9-А"), cell("M3", 13, "Классный час", "9-Д")]))
        self.assertEqual(result.mode, ScheduleRowMode.CLASS)
        self.assertEqual([set(item.student_ids) for item in result.primary_assignments], [{"a1", "a2", "a3", "a4"}, {"d1", "d2", "d3", "d4", "d5"}])

    def test_missing_group_is_unresolved_and_never_mass_assigned(self):
        resolver = NinthGradeResolver(self.context())
        result = resolver.interpret(self.row([cell("L3", 12, "Math D")]))
        self.assertTrue(result.unresolved_blocks)
        self.assertEqual(result.primary_assignments, [])
        self.assertIsNone(result.default_assignment)

    def test_overlapping_primary_memberships_are_reported(self):
        context = self.context()
        memberships = dict(context.memberships)
        memberships["math-B"] = frozenset({"d1"})
        result = NinthGradeResolver(NinthGradeContext(context.all_students, context.base_classes, context.math_groups, context.english_groups, context.exam_groups, memberships)).interpret(self.row([cell("L3", 12, "Math A"), cell("M3", 13, "Math B"), cell("N3", 14, "История")]))
        self.assertTrue(result.evidence.get("conflicting_memberships"))

    def test_shadow_comparison_is_non_mutating(self):
        context = self.context()
        result = NinthGradeResolver(context).interpret(self.row([cell("L3", 12, "Math C")]))
        self.assertEqual(compare_v1_v2({"mode": "MATH", "student_ids": ["d3"]}, result), "equivalent")
        self.assertEqual(compare_v1_v2(None, result), "not_comparable")

    def test_math_subgroup_names_are_not_hardcoded(self):
        context = self.context()
        groups = tuple({**group, "subject_subgroup": lane} for group, lane in zip(context.math_groups, ("X", "Y", "Z")))
        changed = NinthGradeContext(context.all_students, context.base_classes, groups, context.english_groups, context.exam_groups, context.memberships)
        changed = self.learned(changed, columns=(30, 31, 32), lanes=("X", "Y", "Z"))
        result = NinthGradeResolver(changed).interpret(self.row([cell("A3", 30, "Math X"), cell("B3", 31, "Обед"), cell("C3", 32, "История")]))
        self.assertEqual(result.mode, ScheduleRowMode.MATH)
        self.assertEqual(result.evidence["lane_columns"], {"30": "math-A", "31": "math-B", "32": "math-C"})

    def test_partition_row_can_route_multiple_residual_class_activities(self):
        resolver = NinthGradeResolver(self.context())
        # Two ordinary class cells around an explicit Math B anchor each keep
        # their own base audience instead of making the whole row unresolved.
        row = self.row([
            cell("L55", 11, "Пластика Вадим", "9-А"),
            cell("M55", 12, "Матем В ДФ", "9-Д"),
            cell("N55", 13, "География Антон", "9-Д"),
        ])
        result = resolver.interpret(row)
        self.assertEqual(result.mode, ScheduleRowMode.MATH)
        self.assertEqual(set(result.primary_assignments[0].student_ids), {"d2"})
        self.assertEqual(set(result.default_assignment.student_ids), {"a1", "a2", "a3", "a4"})
        self.assertEqual(set(result.secondary_assignments[0].student_ids), {"d1", "d3", "d4", "d5"})

    def test_english_ids_are_not_hardcoded_and_complement_can_be_history(self):
        context = self.context()
        groups = tuple({**group, "subject_subgroup": lane} for group, lane in zip(context.english_groups, ("42", "99", "105")))
        result = NinthGradeResolver(NinthGradeContext(context.all_students, context.base_classes, context.math_groups, groups, context.exam_groups, context.memberships)).interpret(
            self.row([cell("A3", 4, "English 42"), cell("B3", 8, "История")]))
        self.assertEqual(result.mode, ScheduleRowMode.ENGLISH)
        self.assertEqual(result.default_assignment.activity, "История")

    def test_unseen_exam_subject_uses_generic_exam_context(self):
        context = self.context()
        art = {"id": "oge-art", "name": "Art OGE", "display_name": "Art OGE", "subject": "Искусство", "exam_track": "ОГЭ"}
        memberships = {**context.memberships, "oge-art": frozenset({"d4"})}
        context = NinthGradeContext(context.all_students, context.base_classes, context.math_groups, context.english_groups, (*context.exam_groups, art), memberships)
        result = NinthGradeResolver(context).interpret(self.row([cell("A3", 4, "Искусство OGE")]))
        self.assertEqual(result.mode, ScheduleRowMode.ELECTIVES)
        self.assertEqual(set(result.secondary_assignments[0].student_ids), {"d4"})

    def test_extracurricular_cell_is_excluded_before_oge_residual_resolution(self):
        circle = cell("L3", 11, "⚪АНГЛИЙСКИЙ КЛУБ", audience="9-А")
        circle["activity_type"] = "extracurricular"
        result = NinthGradeResolver(self.context()).interpret(self.row([
            circle,
            cell("M3", 12, "Физика ОГЭ", audience="9-Д"),
        ]))
        self.assertEqual(result.mode, ScheduleRowMode.ELECTIVES)
        self.assertIsNone(result.default_assignment)
        self.assertEqual(result.evidence["excluded_source_cells"], ["L3"])
        self.assertEqual(result.secondary_assignments[0].activity, "Физика")

    def test_partition_without_explicit_anchor_is_not_guessed(self):
        result = self.resolver.interpret(self.row([cell("A3", 4, "Математика"), cell("B3", 8, "История")]))
        self.assertEqual(result.mode, ScheduleRowMode.CLASS)
        # A plain subject can still be a legitimate base-class row; it must
        # not be upgraded to a Math partition without an anchor.
        self.assertFalse(result.evidence.get("math_anchors"))

    def test_required_source_shapes_are_available_as_regression_fixtures(self):
        expected = {
            "class_split_literature_class_hour", "class_split_class_hour_literature",
            "math_c_remainder_no_lesson", "math_a_b_c_mixed_activities",
            "english_6_remainder_russian", "english_7_8_remainder_russian",
            "math_b_chemistry_oge_remainder", "math_c_multiple_oge", "pure_oge_electives",
            "no_lesson_source_variants", "simple_activities", "sdep_grade_10_11_friday",
        }
        self.assertEqual(set(REGRESSION_SCENARIOS), expected)
        for scenario, cells in REGRESSION_SCENARIOS.items():
            weekday = 4 if scenario == "sdep_grade_10_11_friday" else 1
            parsed = [cell(source, column, text, audience, weekday) for source, column, text, audience in cells]
            rows = group_schedule_rows(parsed)
            expected_rows = 2 if scenario == "sdep_grade_10_11_friday" else 1
            self.assertEqual(len(rows), expected_rows, scenario)
            self.assertEqual(sum(len(row.cells) for row in rows), len(cells), scenario)
            self.assertEqual(len({item.source_cell for row in rows for item in row.cells}), len(cells), scenario)

    def test_real_template_rows_use_learned_lanes_group_sets_and_final_no_lesson(self):
        context = self.context()
        english_8 = {**context.english_groups[2], "exam_track": "ОГЭ"}
        context = NinthGradeContext(context.all_students, context.base_classes, context.math_groups,
                                    (*context.english_groups[:2], english_8), (*context.exam_groups, english_8), context.memberships)
        rows = [self.real_row(name, index % 5) for index, name in enumerate(REAL_GRADE9_ROWS)]
        context = learn_partition_lanes(context, rows)
        self.assertEqual(context.lane_columns["math"], {11: "math-A", 12: "math-B", 13: "math-C"})
        self.assertEqual(context.lane_columns["english"], {11: "eng-6", 12: "eng-7", 13: "eng-8"})
        resolver = NinthGradeResolver(context)

        english = resolver.interpret(self.real_row("monday_english_7_8_residual"))
        self.assertEqual(english.mode, ScheduleRowMode.ENGLISH)
        self.assertEqual([set(item.student_ids) for item in english.primary_assignments], [{"d2"}, {"d3"}])
        self.assertEqual(english.default_assignment.activity, "Русский язык")
        self.assertEqual(set(english.default_assignment.student_ids), {"a1", "a2", "a3", "a4"})
        self.assertEqual(set(english.final_assignments[0].student_ids), {"d1", "d4", "d5"})

        group_set = resolver.interpret(self.real_row("tuesday_math_group_set", 1))
        self.assertEqual(group_set.mode, ScheduleRowMode.MATH)
        # "группы А, В" belongs to История искусства, not to the
        # neighbouring Math lane.  Only the explicit Math C cell is a Math
        # assignment; the ordinary history cell is the base-class residual.
        self.assertEqual(set(group_set.primary_assignments[0].student_ids), {"d3"})
        self.assertEqual(set(group_set.default_assignment.student_ids), {"a1", "a2", "a3", "a4"})

        mixed = resolver.interpret(self.real_row("wednesday_math_and_oge", 2))
        self.assertEqual(mixed.mode, ScheduleRowMode.MATH_WITH_ELECTIVES)
        self.assertEqual(set(mixed.primary_assignments[0].student_ids), {"d2"})
        self.assertEqual(set(mixed.secondary_assignments[0].student_ids), {"d4"})
        self.assertTrue(mixed.final_assignments)

        training = resolver.interpret(self.real_row("friday_oge_training_residual", 4))
        self.assertEqual(training.mode, ScheduleRowMode.ELECTIVES)
        self.assertEqual(training.default_assignment.activity, "Тренинг")
        self.assertEqual(training.default_assignment.source_cells, ("M55", "N55"))
        self.assertEqual(set(training.default_assignment.student_ids), {"d2", "d3", "d4", "d5"})
        self.assertEqual(set(training.final_assignments[0].student_ids), {"a1", "a2", "a3", "a4"})


if __name__ == "__main__":
    unittest.main()
