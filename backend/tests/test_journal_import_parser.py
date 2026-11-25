from __future__ import annotations

import unittest

from backend.services.journal_import import classify_journal_values, parse_journal_values


class JournalImportParserTests(unittest.TestCase):
    def test_modern_second_period_sheet_uses_c_to_ah_and_typed_ai_to_ao_summaries(self):
        width = 41  # A:AO
        row = lambda: [None] * width
        dates, titles, weights, categories, student = row(), row(), row(), row(), row()
        dates[0], titles[0], weights[0], categories[0], student[0] = (
            "Дата", "Задание", "Вес", "Категория (У/О/П)", "Ученик Пример")
        titles[1], student[1] = "Итог", 5  # Mirrored final; not a second summary.
        dates[2], dates[3] = "25.11", "26.11"
        titles[2], titles[3] = "Работа с нулём", "Работа со статусом"
        weights[2], weights[3] = 1, 2
        categories[2], categories[3] = "У", "О"
        student[2], student[3] = 0, "Р"
        labels = ["Учебные 40%", "Обязательные 60%", "Общий балл", "Округление",
                  "Повысить на 1", "Итог", "Долги по работам"]
        summary_values = [0, "Р", 3.6, 4, 1, 5, "2 работы"]
        for column, (label, value) in enumerate(zip(labels, summary_values), 34):
            dates[column], student[column] = label, value

        parsed = parse_journal_values([dates, titles, weights, categories, student], grade=8,
                                      subject="История", sheet_title="II триместр · История", sheet_id=88)

        self.assertEqual([item["source_column"] for item in parsed["assessments"]], ["C", "D"])
        self.assertEqual([item["max_score"] for item in parsed["assessments"]], [5.0, 5.0])
        self.assertEqual([(item["numeric_score"], item["status"]) for item in parsed["results"]],
                         [(0.0, None), (None, "р")])
        summaries = {item["summary_type"]: item for item in parsed["summaries"]}
        self.assertEqual(set(summaries), {"learning_grade", "required_grade", "calculated_grade",
                                          "rounded_grade", "teacher_adjustment", "term_grade",
                                          "assessment_debt"})
        self.assertEqual(summaries["learning_grade"]["numeric_value"], "0")
        self.assertEqual(summaries["required_grade"]["text_value"], "Р")
        self.assertEqual(summaries["assessment_debt"]["text_value"], "2 работы")
        self.assertEqual(len([item for item in parsed["summaries"] if item["summary_type"] == "term_grade"]), 1)
        rebound = parse_journal_values([dates, titles, weights, categories, student], grade=8,
                                       subject="История", sheet_title="История", sheet_id=88, period_number=2)
        self.assertEqual([(item["source_column"], item["max_score"]) for item in rebound["assessments"]],
                         [("C", 5.0), ("D", 5.0)])
        self.assertEqual({item["summary_type"] for item in rebound["summaries"]}, set(summaries))

    def test_multiline_term_two_headers_keep_parts_and_empty_final(self):
        values = [["Дата", "Итог", "01.12", "02.12", "03.12"], ["", "", "", "", ""],
                  ["", "", "", "", ""], ["Блок\nЗадание / работа", "", "A", "B", "C"],
                  ["Вес", "", 1, 3, 1], ["Категория (У/О/П)", "", "У", "О", "П"],
                  ["Synthetic Student", "", 5, 2, "н"]]
        result = parse_journal_values(values, grade=7, subject="Предмет", sheet_title="II · Предмет")
        self.assertEqual([item["category"] for item in result["assessments"]], ["У", "О", "П"])
        self.assertEqual([item["weight"] for item in result["assessments"]], [1, 3, 1])
        self.assertEqual(result["results"][-1]["status"], "н")
        self.assertEqual(result["summaries"], [])

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

    def test_literature_layout_uses_horizontal_dates_even_with_term_marker_in_a1(self):
        values = [
            ["2", "02.09", "07.09", "08.09", "Итого", "Средняя отметка"],
            ["Задание", "Рассказ", "Рассказ", "Письменная работа", "Сумма баллов за триместр", "Средняя отметка"],
            ["Вес", None, "1", None, None, None],
            ["Оценка", None, "5", None, None, None],
            ["Иванов Иван", None, 4, 3, 7, 3.5],
        ]
        parsed = parse_journal_values(values, grade=9, subject="Литература", sheet_title="Литература")
        self.assertEqual([item["assessed_on"] for item in parsed["assessments"]], ["2026-09-02", "2026-09-07", "2026-09-08"])
        self.assertEqual(len(parsed["assessments"]), 3)
        self.assertEqual(len(parsed["results"]), 2)

    def test_derived_art_results_are_classified_by_headers(self):
        values = [["Фамилия", "Имя", "Практикум 1 — Греция и Египет", "Практикум 2 — Портрет", "Итог (средний балл из 10)"]]
        classification = classify_journal_values(values, sheet_title="Любое название")
        self.assertEqual(classification["kind"], "derived_results")
        self.assertIn("сводный итог", classification["reason"])

    def test_external_key_stays_stable_when_result_values_change(self):
        first = [["Дата", "01.09"], ["Задание", "Проверочная"], ["Вес", 1], ["Оценка", 10], ["Student One", 8]]
        changed = [["Дата", "01.09"], ["Задание", "Проверочная"], ["Вес", 1], ["Оценка", 10], ["Student One", 9]]
        first_key = parse_journal_values(first, grade=9, subject="История", sheet_title="История", sheet_id=4)["assessments"][0]["external_key"]
        changed_key = parse_journal_values(changed, grade=9, subject="История", sheet_title="История", sheet_id=4)["assessments"][0]["external_key"]
        self.assertEqual(first_key, changed_key)

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
