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

    def test_scored_indistinguishable_assignments_remain_diagnostic(self):
        values = [
            ["Дата", "01.09", "01.09"],
            ["Задание", "Контрольная", "Контрольная"],
            ["Вес", "1", "1"],
            ["Оценка", "10", "10"],
            ["Иванов Иван", "8", "8"],
        ]

        with self.assertRaisesRegex(ValueError, "неразличимые признаки"):
            parse_journal_values(values, grade=9, subject="История", sheet_title="Synthetic")

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


if __name__ == "__main__":
    unittest.main()
