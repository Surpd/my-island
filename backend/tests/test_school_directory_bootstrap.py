import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from backend.database import Database
from backend.services.school_directory_bootstrap import (
    assess_bulk_change,
    clean_name,
    name_without_note,
    parse_class_lists,
    parse_group_rosters,
    parse_exam_selections,
    english_group_key,
    ManifestItem,
    render_manifest_examples,
)


class SchoolDirectoryBootstrapTests(unittest.TestCase):
    def test_class_list_parser_keeps_real_class_and_strips_profile_note(self):
        people, groups, memberships = parse_class_lists([
            ["x", "5 класс", "x", "6 класс"],
            ["1", "Иванов Иван (профмат)", "1", "Петров Пётр"],
        ])
        self.assertEqual({item.display_name for item in people}, {"Иванов Иван", "Петров Пётр"})
        self.assertEqual({item.class_name for item in people}, {"5", "6"})
        self.assertEqual(len(groups), 8)
        self.assertEqual(len(memberships), 2)

    def test_name_normalisation(self):
        self.assertEqual(clean_name("  Иван\u00a0  Петров  "), "Иван Петров")
        self.assertEqual(name_without_note("Иван Петров (профиль)"), "Иван Петров")

    def test_bulk_guard_blocks_mass_deletion(self):
        result = assess_bulk_change({"students": 100, "groups": 20, "memberships": 100}, {"students": 70, "groups": 10, "memberships": 50})
        self.assertTrue(result.blocked)
        self.assertTrue(any("автоматическое применение остановлено" in item for item in result.reasons))

    def test_people_library_is_account_independent(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            identity = database.create_identity("student", "Каталог Ученик", "6")
            group = database.create_group("6", "class")
            database.create_membership(group["id"], identity["id"], "student", "official_import", "sheet!D2")
            people = database.list_people_library(kind="student")
            person = next(item for item in people if item["display_name"] == "Каталог Ученик")
            self.assertFalse(person["has_account"])
            self.assertEqual(person["groups"][0]["name"], "6")

    def test_group_parser_propagates_merged_grade_heading_and_keeps_blank_rows(self):
        rows = [[] for _ in range(18)]
        for row in rows:
            row.extend([None] * 22)
        rows[2][1] = "Математика"
        rows[3][11] = "9 класс"
        rows[4][11], rows[4][13], rows[4][15] = "9-A Дмитрий", "9-B Дмитрий", "9-C Дмитрий"
        rows[5][11], rows[5][13], rows[5][15] = "Иванов Иван", "Петров Пётр", "Сидоров Саша"
        rows[6][13] = "Второй Б"
        groups, memberships, _ = parse_group_rosters(rows)
        self.assertEqual({item.subject_subgroup for item in groups if item.subject == "Математика"}, {"A", "B", "C"})
        self.assertEqual(len([item for item in memberships if item.group.endswith(":9 класс:9-B Дмитрий")]), 2)

    def test_group_parser_keeps_all_english_columns_under_merged_headings(self):
        rows = [[] for _ in range(42)]
        for row in rows:
            row.extend([None] * 22)
        rows[23][1] = "Английский язык"
        rows[24][1], rows[24][5], rows[24][11], rows[24][17] = "5-6 класс", "7-8 класс", "9 класс", "10-11 класс"
        labels = ("1 Ангелина", "2 Игорь", "3 Ангелина", "4 Ангелина", "5 Игорь", "6 Ангелина", "7 Ангелина", "8 ОГЭ Игорь", "9 Ангелина", "10 ЕГЭ Игорь")
        for col, label in zip(range(1, 20, 2), labels):
            rows[25][col] = label
        groups, _, _ = parse_group_rosters(rows)
        self.assertEqual(
            {item.display_name.rsplit(" · ", 1)[-1] for item in groups if item.subject == "Английский язык"},
            set(labels),
        )
        self.assertEqual(english_group_key("7 Ангелина"), "english:7")

    def test_sparse_subject_and_grade_headers_carry_across_adjacent_groups(self):
        rows = [[None] * 16 for _ in range(99)]
        rows[47][1] = "Обществознание"
        rows[48][1] = "9 класс"
        rows[49][1], rows[49][3] = "База Антон", "ОГЭ Антон"
        rows[50][1], rows[50][3] = "Базов Иван", "Экзаменов Пётр"
        groups, memberships, _ = parse_group_rosters(rows)
        self.assertIn("subject:обществознание:9 класс:ОГЭ Антон", {group.name for group in groups})
        self.assertIn("Экзаменов Пётр", {membership.person for membership in memberships})

    def test_exam_parser_accepts_literal_true_only(self):
        rows = [[None] * 12 for _ in range(40)]
        rows[1][1], rows[2][2], rows[2][3] = "9 класс", "Физика", "Химия"
        rows[3][1], rows[3][2], rows[3][3] = "Иванов Иван", "TRUE", "химия по желанию"
        selections, issues = parse_exam_selections(rows)
        self.assertEqual([(item.subject, item.person) for item in selections], [("Физика", "Иванов Иван")])
        self.assertEqual([item.issue_type for item in issues], ["non_boolean_selection_value"])

    def test_report_examples_are_derived_from_deduplicated_manifest(self):
        items = [
            ManifestItem("KEEP", "student:group", {}, {"source_refs": ["sheet!A1"]}),
            ManifestItem("KEEP", "student:group", {}, {"source_refs": ["sheet!A2"]}),
        ]
        report = render_manifest_examples(items)
        self.assertEqual(report.count("| KEEP | student:group |"), 1)
        self.assertIn("sheet!A1, sheet!A2", report)


if __name__ == "__main__":
    unittest.main()
