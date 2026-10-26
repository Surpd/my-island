from __future__ import annotations

import unittest

from backend.services.journal_import import parse_journal_values


class JournalImportParserTests(unittest.TestCase):
    def test_empty_duplicate_placeholders_collapse_and_hidden_roster_is_valid(self):
        values = [
            ["Дата", "01.09", "01.09"],
            ["Задание", "Контрольная", "Контрольная"],
            ["Вес", "1", "1"],
            ["Оценка", "10", "10"],
        ]

        parsed = parse_journal_values(values, grade=9, subject="Биология", sheet_title="Synthetic")

        self.assertEqual(len(parsed["assessments"]), 1)
        self.assertEqual(parsed["students"], [])
        self.assertEqual(parsed["results"], [])

    def test_initial_identical_populated_columns_keep_distinct_occurrence_identity(self):
        values = [
            ["Дата", "01.09", "01.09"],
            ["Задание", "Контрольная", "Контрольная"],
            ["Вес", "1", "1"],
            ["Оценка", "10", "10"],
            ["Иванов Иван", "8", "8"],
        ]

        parsed = parse_journal_values(values, grade=9, subject="История", sheet_title="Synthetic")

        self.assertEqual(len(parsed["assessments"]), 2)
        self.assertEqual(len({item["external_key"] for item in parsed["assessments"]}), 2)
        self.assertEqual(len(parsed["results"]), 2)
        self.assertEqual({item["assessment_key"] for item in parsed["results"]}, {item["external_key"] for item in parsed["assessments"]})
        self.assertTrue(all(item["raw_source"]["identical_source_columns"] == 2 for item in parsed["assessments"]))

    def test_multiline_headers_are_discovered(self):
        values = [
            ["Журнал\nДата", "01.09", "02.09"],
            ["Работа\nЗадание", "Контрольная", "Домашняя"],
            ["Вес работы\nВес", 2, 1],
            ["Максимум\nОценка", 10, 5],
            ["Иванов Иван", 8, 4],
        ]

        parsed = parse_journal_values(values, grade=9, subject="История", sheet_title="Synthetic")

        self.assertEqual([(item["title"], item["max_score"]) for item in parsed["assessments"]], [("Контрольная", 10.0), ("Домашняя", 5.0)])
        self.assertEqual(len(parsed["results"]), 2)

    def test_blank_date_label_is_recognized_from_date_row_and_following_title(self):
        values = [
            ["", "01.09"],
            ["Задание", "Проверочная"],
            ["Вес", 1],
            ["Оценка", 10],
            ["Иванов Иван", 7],
        ]

        parsed = parse_journal_values(values, grade=9, subject="История", sheet_title="Synthetic")

        self.assertEqual(parsed["assessments"][0]["assessed_on"], "2026-09-01")
        self.assertEqual(parsed["results"][0]["numeric_score"], 7.0)

    def test_projects_without_weight_headers_preserve_boolean_status_and_team_rows(self):
        values = [
            ["Дата", "01.09"],
            ["Задание", "Командный проект"],
            ["Участники", "Оценка из10"],
            ["Ученик Один", False],
            ["Команда Роботы", True],
        ]

        parsed = parse_journal_values(values, grade=8, subject="Проекты", sheet_title="Проекты 8 класс")

        self.assertEqual(len(parsed["assessments"]), 1)
        self.assertIsNone(parsed["assessments"][0]["weight"])
        self.assertEqual(parsed["assessments"][0]["max_score"], 10.0)
        self.assertEqual([item['entry_kind'] for item in parsed['students']], ['individual','team'])
        self.assertEqual([(item['numeric_score'],item['status']) for item in parsed['results']], [(None,'false'),(None,'true')])

    def test_art_score_header_extracts_maximum_from_score_label(self):
        values = [
            ["Дата", "01.09"],
            ["Задание", "Набросок"],
            ["Вес", 1],
            ["Участники", "Оценка из10"],
            ["Петрова Анна", 8],
        ]

        parsed = parse_journal_values(values, grade=7, subject="Искусство", sheet_title="Искусство")

        self.assertEqual(parsed["assessments"][0]["max_score"], 10.0)
        self.assertEqual(parsed["results"][0]["numeric_score"], 8.0)

    def test_repeated_blocks_keep_distinct_local_labels(self):
        values = [
            ["Group Alpha"],
            ["Дата", "01.09"],
            ["Задание", "Test"],
            ["Вес", "1"],
            ["Оценка", "10"],
            ["Student One", "8"],
            [],
            ["Group Beta"],
            ["Дата", "01.09"],
            ["Задание", "Test"],
            ["Вес", "1"],
            ["Оценка", "10"],
            ["Student Two", "9"],
        ]

        parsed = parse_journal_values(values, grade=9, subject="Английский язык", sheet_title="Synthetic")

        self.assertEqual({item["group_marker"] for item in parsed["assessments"]}, {"Group Alpha", "Group Beta"})
        self.assertEqual({item["group_marker"] for item in parsed["students"]}, {"Group Alpha", "Group Beta"})
        self.assertEqual({item["group_marker"] for item in parsed["results"]}, {"Group Alpha", "Group Beta"})


    def test_auxiliary_exercise_table_does_not_extend_main_gradebook(self):
        values = [
            ["Дата", 46310], ["Задание", "Контрольная"], ["Вес", 1], ["Оценка", 10],
            ["Иванов Иван", 8],
            ["Дата", 46314], ["Задание", "Домашняя работа"], ["Вес", 1], ["Оценка", 10],
            ["Иванов Иван", 9],
            [46317], ["Ученик", "✓", "✗"], ["Иванов Иван", "✓", "✗"],
            ["Верных по заданию", 1, 0],
        ]

        parsed = parse_journal_values(values, grade=11, subject="Математика", sheet_title="Математика")

        self.assertEqual([item["title"] for item in parsed["assessments"]], ["Контрольная", "Домашняя работа"])
        self.assertEqual({item["student_name"] for item in parsed["students"]}, {"Иванов Иван"})
        self.assertEqual(len(parsed["results"]), 2)

    def test_paired_art_history_names_are_teams_and_comments_are_hidden(self):
        values = [
            ["Дата", "01.09"], ["Задание", "Проект"], ["Вес", 1], ["Оценка", 10],
            ["Агата и Тая (личный комментарий)", 8],
        ]

        parsed = parse_journal_values(values, grade=10, subject="История искусства", sheet_title="История искусства")

        student = parsed["students"][0]
        self.assertEqual(student["student_name"], "Агата и Тая")
        self.assertEqual(student["entry_kind"], "team")
        self.assertEqual(student["raw_source"]["source_name"], "Агата и Тая (личный комментарий)")
        self.assertEqual(parsed["results"][0]["student_name"], "Агата и Тая")


    def test_appended_multiline_comment_is_hidden_but_preserved_raw(self):
        values = [
            ["Дата", "01.09"], ["Задание", "Работа"], ["Вес", 1], ["Оценка", 10],
            ["Иванов Иван\nличная пометка", 8],
        ]

        parsed = parse_journal_values(values, grade=10, subject="История", sheet_title="История")

        self.assertEqual(parsed["students"][0]["student_name"], "Иванов Иван")
        self.assertEqual(parsed["students"][0]["raw_source"]["source_name"], "Иванов Иван\nличная пометка")


if __name__ == "__main__":
    unittest.main()
