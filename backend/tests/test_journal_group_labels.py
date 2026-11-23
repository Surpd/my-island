from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.database import Database

from backend.services.journal_group_labels import journal_group_labels, verify_group_labels
from backend.services.journal_import import parse_journal_values


def parse(headers, *, grade=9, subject="Математика", names=("Альфа Пример", "Бета Пример", "Гамма Пример")):
    values = []
    for index, (header, student) in enumerate(zip(headers, names)):
        values.extend([
            ["Дата", header, f"0{index + 1}.12"],
            ["Задание", "", "Самостоятельная работа"],
            ["Вес", "", 1],
            ["Категория (У/О/П)", "", "У"],
            [student, 5, 10],
        ])
    return parse_journal_values(values, grade=grade, subject=subject, sheet_title="II · Математика", sheet_id=42)


HEADERS = ("II триместр · 9A", "II триместр · 9B", "II триместр · 9C")


class JournalGroupLabelTests(unittest.TestCase):
    def test_real_shape_and_letter_script_does_not_change_persistent_keys(self):
        legacy = parse(("", "", ""))
        explicit = parse(HEADERS)
        self.assertEqual(journal_group_labels(explicit["assessments"]), {"block 1": "9A", "block 2": "9B", "block 3": "9C"})
        self.assertEqual(explicit["students"], legacy["students"])
        self.assertEqual(explicit["results"], legacy["results"])
        self.assertEqual(explicit["summaries"], legacy["summaries"])
        self.assertEqual([row["external_key"] for row in explicit["assessments"]], [row["external_key"] for row in legacy["assessments"]])
        russian = parse(tuple(f"II триместр · 9{letter}" for letter in "АВС"))
        self.assertEqual(explicit, russian)

    def test_not_inferred_by_order_or_from_plain_other_subject_headers(self):
        self.assertEqual(journal_group_labels(parse(HEADERS[::-1])["assessments"]),
                         {"block 1": "9C", "block 2": "9B", "block 3": "9A"})
        self.assertFalse(journal_group_labels(parse(HEADERS, grade=8)["assessments"]))
        self.assertFalse(journal_group_labels(parse(HEADERS, subject="Биология")["assessments"]))
        wrong = parse(("Группа 1", "II триместр · 9Б", "II триместр · 9С/9А"))
        self.assertFalse(journal_group_labels(wrong["assessments"]))
        self.assertEqual(wrong["assessments"][0]["group_marker"], "Группа 1")

    def test_explicit_numbered_groups_and_english_across_other_grades(self):
        numeric = parse(("II триместр · Группа 1", "II триместр · Группа 2", "II триместр · Группа 3"),
                        grade=5, subject="Русский язык")
        self.assertEqual([row["group_marker"] for row in numeric["students"]], ["Группа 1", "Группа 2", "Группа 3"])
        english = parse(("II триместр · Английский 3  · Пример", "II триместр · Английский 1", "II триместр · ПРЕПОДАВАТЕЛЬ"),
                        grade=8, subject="Английский язык")
        self.assertEqual(journal_group_labels(english["assessments"]), {"block 1": "Английский 3", "block 2": "Английский 1"})
        self.assertNotIn("Пример", str(english["assessments"]))
        self.assertFalse(journal_group_labels(parse(("Итог", "", "Итог"), grade=7, subject="Математика/Физика")["assessments"]))
        self.assertFalse(journal_group_labels(parse(("II триместр · Английский 3  · Пример", "", ""),
                                                   grade=8, subject="Химия")["assessments"]))
        with self.assertRaisesRegex(ValueError, "одно название группы"):
            parse(("II триместр · Группа 1", "II триместр · группа 1", "Итог"), grade=6, subject="Математика")

    def test_duplicate_labels_never_leak_into_display(self):
        parsed = parse((HEADERS[0], "II триместр · 9А", HEADERS[2]))
        self.assertEqual(journal_group_labels(parsed["assessments"]), {"block 3": "9C"})

    def test_first_adoption_and_reimport_reject_ambiguous_or_reordered_groups(self):
        explicit = parse(HEADERS)
        old = parse(("", "", ""))
        new_labels = journal_group_labels(explicit["assessments"])
        verify_group_labels({}, new_labels, old["students"], explicit["students"])
        verify_group_labels(new_labels, new_labels, explicit["students"], explicit["students"])
        reordered = parse(HEADERS[::-1], names=("Гамма Пример", "Бета Пример", "Альфа Пример"))
        reorder_labels = journal_group_labels(reordered["assessments"])
        with self.assertRaisesRegex(ValueError, "Прежние оценки и связи сохранены"):
            verify_group_labels({}, reorder_labels, old["students"], reordered["students"])
        with self.assertRaisesRegex(ValueError, "Прежние оценки и связи сохранены"):
            verify_group_labels(new_labels, reorder_labels, explicit["students"], reordered["students"])
        with self.assertRaises(ValueError):
            verify_group_labels(new_labels, {}, explicit["students"], old["students"])
        duplicate_roster = parse(HEADERS, names=("Альфа Пример", "Альфа Пример", "Гамма Пример"))
        with self.assertRaises(ValueError):
            verify_group_labels({}, new_labels, duplicate_roster["students"], duplicate_roster["students"])

    def test_existing_journal_links_scores_history_survive_label_adoption(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "journal.db")
            database.initialize()

            def save(parsed):
                source = dict(spreadsheet_id="synthetic-label-test", spreadsheet_title="Synthetic",
                              grade=9, subject="Математика", sheet_title="II · Математика",
                              sheet_id=42, term_number=2, academic_year="2026/27", source_hash=parsed["source_hash"])
                database.save_journal_snapshot(source, parsed["assessments"], parsed["results"], parsed["students"], parsed["summaries"])

            save(parse(("", "", "")))
            source_id = database.journal_status()[0]["id"]
            group_id = database.create_group("Проверочная группа", "instructional", subject="Математика")
            database.map_journal_group(source_id, "block 1", group_id["id"], None)
            before = database.journal_detail(source_id)
            save(parse(HEADERS))
            explicit = database.journal_detail(source_id)
            self.assertEqual(explicit["group_labels"], {"block 1": "9A", "block 2": "9B", "block 3": "9C"})
            for field in ("assessments", "students", "mappings", "groups"):
                self.assertEqual(explicit[field], before[field])
            with self.assertRaises(ValueError):
                save(parse(HEADERS[::-1], names=("Гамма Пример", "Бета Пример", "Альфа Пример")))
            self.assertEqual(database.journal_detail(source_id), explicit)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_result_history").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
