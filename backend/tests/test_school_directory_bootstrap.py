import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from backend.database import Database
from backend.services.school_directory_bootstrap import (
    assess_bulk_change,
    clean_name,
    name_without_note,
    parse_class_lists,
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


if __name__ == "__main__":
    unittest.main()
