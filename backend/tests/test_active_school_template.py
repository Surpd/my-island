import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from backend.database import Database
from backend.services.schedule_experiments import (
    create_experiment,
    create_experiment_week,
    delete_experiment,
    duplicate_experiment,
    get_experiment,
    save_experiment,
)
from backend.services.school_template_selection import open_active_school_template
from backend.scripts.restore_school_editor_template import digest, restore


class ActiveSchoolTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "school-template.sqlite3")
        self.database.initialize()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def school_payload(*, subject="Предмет", school_source=None):
        class_ids = [str(uuid4()) for _ in range(9)]
        lessons = []
        for index in range(386):
            lunch = index >= 337
            class_id = class_ids[index % len(class_ids)]
            lessons.append({
                "id": f"sheet:synthetic:A{index + 1}",
                "day": index % 5,
                "period": index % 9,
                "start": "11:50" if lunch else "09:00",
                "end": "12:35" if lunch else "09:45",
                "classId": class_id,
                "classIds": [class_id],
                "groupId": class_id,
                "groupIds": [class_id],
                "audience": {"kind": "classes", "class_ids": [class_id]},
                "subject": "Обед" if lunch else subject,
                "teacherId": "",
                "teacherIds": [],
                "room": "",
                "specialMark": "lunch" if lunch else "",
                "needsClarification": False,
                "source": {
                    "sheet_id": "synthetic",
                    "cells": [f"A{index + 1}"],
                    "raw_text": "🥨" if lunch else subject,
                },
            })
        return {
            "days": [0, 1, 2, 3, 4],
            "slots": [{"start": "09:00", "end": "09:45"}],
            "classes": class_ids,
            "lessons": lessons,
            "draft_kind": "school",
            "school_source": school_source or {
                "kind": "current_google_template",
                "role": "source_led_school_template",
                "spreadsheet_id": "synthetic-spreadsheet",
                "sheet_id": "synthetic-sheet",
            },
            "import_review": {
                "status": "source_led_applied",
                "source_cell_count": 386,
                "source_block_count": 386,
            },
            "transfer_result": {
                "transferred": 337,
                "explicit_lunch": 49,
                "needs_clarification": 0,
                "import_errors": 0,
            },
        }

    def create_draft(self, name, payload):
        return create_experiment(self.database, name=name, source="import", payload=payload)

    def activate(self, draft):
        payload_json = json.dumps(draft["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.database.connection() as connection:
            self.database.execute(connection, """INSERT INTO school_template_selection
                (scope,draft_id,source_draft_id,source_revision,source_payload_sha256,activated_at)
                VALUES ('school',?,?,?,?,?)""", (
                draft["id"], draft["id"], draft["revision"],
                hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                "2026-10-19T00:00:00Z",
            ))

    def selection_row(self):
        with self.database.connection() as connection:
            row = self.database.execute(connection,
                "SELECT * FROM school_template_selection WHERE scope='school'").fetchone()
            return dict(row) if row else None

    def test_missing_pointer_fails_without_creating_canonical_copy(self):
        with self.assertRaisesRegex(ValueError, "Основной школьный шаблон не назначен"):
            open_active_school_template(self.database)
        with self.database.connection() as connection:
            count = self.database.execute(connection,
                "SELECT COUNT(*) AS count FROM schedule_experiment_drafts").fetchone()["count"]
        self.assertEqual(count, 0)
        self.assertIsNone(self.selection_row())

    def test_opens_explicit_source_led_draft_even_with_an_old_canonical_primary(self):
        old_primary = self.create_draft(
            "Школа · canonical template",
            self.school_payload(school_source={
                "kind": "canonical_template",
                "role": "primary_school_template",
                "canonical_version_id": "old-canonical-v1",
            }),
        )
        active = self.create_draft(
            "Проверенный исходный шаблон",
            self.school_payload(),
        )
        active = save_experiment(
            self.database,
            active["id"],
            expected_revision=active["revision"],
            payload=active["payload"],
            name="Переименованный активный шаблон",
        )
        self.activate(active)

        opened = open_active_school_template(self.database)

        self.assertNotEqual(active["id"], old_primary["id"])
        self.assertEqual(opened["id"], active["id"])
        self.assertEqual(opened["name"], "Переименованный активный шаблон")
        self.assertTrue(opened["already_exists"])
        self.assertEqual(opened["payload"]["import_review"]["status"], "source_led_applied")
        self.assertEqual(len(opened["payload"]["lessons"]), 386)

    def test_saves_reloads_duplicates_and_weeks_without_changing_selection(self):
        active = self.create_draft("Исходный шаблон", self.school_payload())
        self.activate(active)
        selected_before = self.selection_row()

        duplicate = duplicate_experiment(self.database, active["id"], name="Копия шаблона")
        week = create_experiment_week(
            self.database, active["id"], week_start="2026-10-19",
            expected_revision=active["revision"],
        )
        self.assertNotEqual(week["id"], active["id"])
        self.assertEqual(week["payload"]["source_template"], {
            "id": active["id"], "revision": active["revision"],
        })

        self.assertEqual(open_active_school_template(self.database)["id"], active["id"])
        self.assertEqual(open_active_school_template(self.database)["id"], active["id"])
        self.assertEqual(self.selection_row(), selected_before)

        edited_payload = dict(get_experiment(self.database, active["id"])["payload"])
        edited_payload["lessons"] = list(edited_payload["lessons"])
        edited_payload["lessons"][0] = {**edited_payload["lessons"][0], "subject": "Изменённый предмет"}
        edited = save_experiment(
            self.database, active["id"], expected_revision=active["revision"],
            payload=edited_payload,
        )

        self.assertEqual(open_active_school_template(self.database)["id"], active["id"])
        self.assertEqual(open_active_school_template(self.database)["payload"]["lessons"][0]["subject"],
                         "Изменённый предмет")
        self.assertEqual(get_experiment(self.database, week["id"])["payload"]["lessons"][0]["subject"],
                         "Предмет")
        self.assertEqual(get_experiment(self.database, duplicate["id"])["payload"]["lessons"][0]["subject"],
                         "Предмет")
        self.assertEqual(self.selection_row(), selected_before)
        self.assertEqual(edited["revision"], active["revision"] + 1)

    def test_foreign_key_prevents_deleting_the_active_draft(self):
        active = self.create_draft("Активный шаблон", self.school_payload())
        self.activate(active)

        with self.assertRaises(sqlite3.IntegrityError):
            delete_experiment(self.database, active["id"])

        self.assertEqual(open_active_school_template(self.database)["id"], active["id"])

    @staticmethod
    def restore_source():
        lessons = []
        for index in range(386):
            lunch = index >= 337
            lessons.append({
                "id": f"sheet:638874869:synthetic-{index + 1}",
                "day": index % 5,
                "start": "11:50" if lunch else "09:00",
                "end": "12:35" if lunch else "09:45",
                "subject": "Обед" if lunch else "Предмет",
                "specialMark": "lunch" if lunch else "lesson",
                "source": {
                    "sheet_id": "638874869",
                    "sheet_title": "2026/27 шаблон",
                    "cells": [f"A{index + 1}"],
                    "raw_text": "🥨" if lunch else "Оригинальная подпись",
                    "unknown_source_annotation": {"line_breaks": ["line one", "line two"]},
                },
            })
        return {
            "id": "4dc886ce-db12-4faf-bbc7-e614a2fce673",
            "name": "ШКОЛА · Реальный шаблон 2026/27 · На проверке",
            "revision": 45,
            "payload": {
                "days": [0, 1, 2, 3, 4],
                "slots": [{"start": "09:00", "end": "09:45"}],
                "classes": [],
                "lessons": lessons,
                "draft_kind": "school",
                "school_source": {
                    "kind": "current_google_template",
                    "canonical_version_id": "source-version-token",
                    "spreadsheet_id": "1j5dI2VbT0RoW2A5ZkCpMYVdt7cSY4ZjNxQaMWtu--Fo",
                    "sheet_id": "638874869",
                    "sheet_title": "2026/27 шаблон",
                    "observed_at": "2026-10-14",
                    "unusual_provenance_field": {"preserve": True},
                },
                "import_review": {
                    "status": "source_led_applied",
                    "source_cell_count": 395,
                    "source_block_count": 265,
                },
                "transfer_result": {"transferred": 238, "import_errors": 0},
                "unusual_top_level_metadata": {"sequence": ["first", "second"]},
            },
        }

    def draft_count(self):
        with self.database.connection() as connection:
            return self.database.execute(connection,
                "SELECT COUNT(*) AS count FROM schedule_experiment_drafts").fetchone()["count"]

    def test_restore_dry_run_validates_without_inserting(self):
        source = self.restore_source()

        report = restore(self.database, source, expected_sha256=digest(source["payload"]))

        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["records"], 386)
        self.assertEqual(report["lunch"], 49)
        self.assertEqual(report["other_records"], 337)
        self.assertEqual(self.draft_count(), 0)
        self.assertIsNone(self.selection_row())

    def test_restore_apply_inserts_exact_original_payload_and_pointer_once(self):
        source = self.restore_source()
        expected_payload = json.loads(json.dumps(source["payload"], ensure_ascii=False))
        checksum = digest(source["payload"])

        first = restore(self.database, source, expected_sha256=checksum, apply=True)
        second = restore(self.database, source, expected_sha256=checksum, apply=True)

        self.assertTrue(first["valid"], first["errors"])
        self.assertFalse(first["already_restored"])
        self.assertEqual(first["revision"], 45)
        self.assertNotEqual(first["draft_id"], source["id"])
        self.assertTrue(second["already_restored"])
        self.assertEqual(second["draft_id"], first["draft_id"])
        self.assertEqual(self.draft_count(), 1)
        saved = get_experiment(self.database, first["draft_id"])
        self.assertEqual(saved["revision"], 45)
        self.assertEqual(saved["payload"], expected_payload)
        self.assertEqual(saved["payload"]["school_source"]["unusual_provenance_field"], {"preserve": True})
        self.assertEqual(saved["payload"]["lessons"][0]["source"]["unknown_source_annotation"],
                         {"line_breaks": ["line one", "line two"]})
        self.assertEqual(saved["payload"]["unusual_top_level_metadata"], {"sequence": ["first", "second"]})
        selection = self.selection_row()
        self.assertEqual(selection["draft_id"], first["draft_id"])
        self.assertEqual(selection["source_draft_id"], source["id"])
        self.assertEqual(selection["source_revision"], 45)
        self.assertEqual(selection["source_payload_sha256"], checksum)

    def test_malformed_group_reference_is_invalid_and_not_inserted(self):
        source = self.restore_source()
        source["payload"]["lessons"][0]["groupId"] = "missing-group-id"

        report = restore(self.database, source, expected_sha256=digest(source["payload"]), apply=True)

        self.assertFalse(report["valid"])
        self.assertIn({"missing_groups": ["missing-group-id"]}, report["errors"])
        self.assertEqual(self.draft_count(), 0)
        self.assertIsNone(self.selection_row())

    def test_different_existing_selection_is_not_overwritten(self):
        source = self.restore_source()
        existing_id = "existing-selected-draft"
        with self.database.connection() as connection:
            self.database.execute(connection, """INSERT INTO schedule_experiment_drafts
                (id,name,revision,payload) VALUES (?,?,1,'{}')""", (existing_id, "Existing"))
            self.database.execute(connection, """INSERT INTO school_template_selection
                (scope,draft_id,source_draft_id,source_revision,source_payload_sha256)
                VALUES ('school',?,?,?,?)""", (existing_id, "different-source", 4, "different-checksum"))

        with self.assertRaisesRegex(ValueError, "different active selection"):
            restore(self.database, source, expected_sha256=digest(source["payload"]), apply=True)

        self.assertEqual(self.draft_count(), 1)
        self.assertEqual(self.selection_row()["draft_id"], existing_id)

    def test_checksum_mismatch_raises_without_inserting(self):
        source = self.restore_source()

        with self.assertRaisesRegex(ValueError, "checksum differs"):
            restore(self.database, source, expected_sha256="0" * 64, apply=True)

        self.assertEqual(self.draft_count(), 0)
        self.assertIsNone(self.selection_row())


if __name__ == "__main__":
    unittest.main()
