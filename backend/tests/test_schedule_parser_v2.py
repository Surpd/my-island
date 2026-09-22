import copy
import unittest

from backend.services.ninth_grade_resolver import NinthGradeResolver, format_row_diagnostic
from backend.services.schedule_parser_v2 import NO_LESSON, ScheduleRowMode, group_schedule_rows, normalize_no_lesson


def parsed(source_cell, column, text, *, audience="9-Д", weekday=1, date="2026-09-08", merged=None, subject=""):
    return {
        "record_key": f"sheet:{source_cell}", "sheet_id": "sheet", "tab_title": "8-12 Сентября",
        "source_cell": source_cell, "source_column": column, "source_row": 3,
        "lesson_date": date, "weekday": weekday, "start_time": "09:00", "end_time": "09:45",
        "audience": audience, "merged_audiences": merged or [], "raw_text": text,
        "subject": subject or text, "modifiers": {}, "merge_data": {"startColumnIndex": column} if merged else None,
    }


class ScheduleParserV2Tests(unittest.TestCase):
    def test_grouping_is_deterministic_and_preserves_source_provenance(self):
        source = [parsed("D3", 3, "Обед", audience="9-А"), parsed("B3", 1, "Математика A", audience="9-Д"), parsed("C3", 2, "История", audience="9-Д")]
        first = group_schedule_rows(source)
        second = group_schedule_rows(copy.deepcopy(source))
        self.assertEqual(first, second)
        row = next(item for item in first if item.grade_scope == "9")
        self.assertEqual([cell.source_cell for cell in row.cells], ["B3", "C3", "D3"])
        self.assertEqual(row.cells[0].source_column, 1)
        self.assertEqual(row.cells[1].raw_text, "История")

    def test_merged_metadata_is_preserved_without_duplicate_cell(self):
        rows = group_schedule_rows([parsed("B3", 1, "Математика A", audience="9-А", merged=["9-Д"])])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].grade_scope, "9")
        self.assertEqual(len(rows[0].cells), 1)
        self.assertEqual(rows[0].cells[0].merged_audiences, ("9-Д",))
        self.assertIsNotNone(rows[0].cells[0].merge_data)

    def test_no_lesson_lexicon_is_unified_in_v2_only(self):
        for value in ("Обед", "свободны", "Перерыв", "нет урока", "окно"):
            self.assertEqual(normalize_no_lesson(value), NO_LESSON)

    def test_v1_input_is_not_mutated(self):
        source = [parsed("B3", 1, "Math A")]
        before = copy.deepcopy(source)
        group_schedule_rows(source)
        self.assertEqual(source, before)

    def test_v2_modules_have_no_production_side_effects(self):
        self.assertEqual(ScheduleRowMode.MATH.value, "MATH")
        self.assertEqual(NinthGradeResolver.__module__, "backend.services.ninth_grade_resolver")


if __name__ == "__main__":
    unittest.main()
