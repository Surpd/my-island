from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sqlite3

from backend.database import Database
from backend.services.teacher_ui_colors import (
    APPROVED_SOURCE_COLORS, TeacherColorConflict, TeacherColorError,
    confirm_settings_migration, get_color, initialize_missing,
    initialize_sqlite_schema, list_colors, preview_settings_migration,
    reset_manual_color, seed_approved, set_manual_color,
)


class TeacherUiColorsTests(unittest.TestCase):
    def test_integer_directory_ids_normalize_before_migration_revision_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "native-schema.sqlite3")
            database.initialize()
            teacher = database.create_identity("teacher", "Synthetic integer ID")
            identity = str(teacher["id"])
            initialize_sqlite_schema(database)
            initialize_missing(database, "school")
            first = get_color(database, "school", identity)
            settings = {"overrides": {identity: "#123456"}}
            preview = preview_settings_migration(database, "school", settings)
            self.assertEqual(preview["plan"][0]["expected_revision"], first["revision"])
            self.assertTrue(all(isinstance(row["teacher_id"], str) for row in list_colors(database, "school")))
            result = confirm_settings_migration(database, "school", settings, {identity: first["revision"]})
            self.assertEqual(result["imported"], 1)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "colors.sqlite3")
        connection = sqlite3.connect(self.db.path)
        try:
            connection.execute("CREATE TABLE identities (id TEXT PRIMARY KEY, kind TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL)")
            connection.execute("CREATE TABLE lessons (id INTEGER PRIMARY KEY, teacher_identity_id TEXT, payload TEXT)")
        finally:
            connection.close()
        initialize_sqlite_schema(self.db)
        self.ids = list(APPROVED_SOURCE_COLORS)
        with self.db.connection() as connection:
            for index, identity in enumerate(self.ids):
                connection.execute("INSERT INTO identities(id,kind,display_name,status) VALUES(?,?,?,'active')",
                                   (identity, "teacher", f"Teacher {index + 1}"))
            connection.execute("INSERT INTO identities(id,kind,display_name,status) VALUES(100,'teacher','Extra','active')")

    def tearDown(self):
        self.tmp.cleanup()

    def test_shared_registry_is_scoped_to_school_and_revision_conflicts(self):
        teacher_id = self.ids[0]
        first_client = self.db
        second_client = Database(self.db.path)
        initial = get_color(first_client, "school-a", teacher_id)
        self.assertEqual(initial["revision"], 0)
        saved = set_manual_color(first_client, "school-a", teacher_id, "#AABBCC", 0, "admin-a")
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(get_color(second_client, "school-a", teacher_id)["color"], "#AABBCC")
        with self.assertRaises(TeacherColorConflict):
            set_manual_color(second_client, "school-a", teacher_id, "#ABCDEF", 0, "admin-b")
        self.assertNotEqual(get_color(second_client, "school-b", teacher_id)["color"], "#AABBCC")

    def test_unknown_teacher_and_invalid_hex_rejected(self):
        with self.assertRaises(TeacherColorError):
            set_manual_color(self.db, "school-a", "999", "#AABBCC", 0)
        with self.assertRaises(TeacherColorError):
            set_manual_color(self.db, "school-a", self.ids[0], "red", 0)
        with self.assertRaises(TeacherColorConflict):
            set_manual_color(self.db, "school-a", self.ids[0], "#AABBCC", 5)

    def test_reset_is_revisioned_and_clears_only_the_manual_override(self):
        saved = set_manual_color(self.db, "school-a", self.ids[0], "#123456", 0)
        reset = reset_manual_color(self.db, "school-a", self.ids[0], saved["revision"])
        self.assertIsNone(reset["manual_color"])
        self.assertEqual(reset["revision"], 2)
        self.assertEqual(reset["color"], reset["base_color"])
        with self.assertRaises(TeacherColorConflict):
            reset_manual_color(self.db, "school-a", self.ids[0], saved["revision"])

    def test_initialization_and_approved_seed_are_idempotent_and_preserve_manual(self):
        before = initialize_missing(self.db, "school-a", "admin")
        self.assertEqual(before["created"], len(self.ids) + 1)
        initial = get_color(self.db, "school-a", self.ids[0])
        self.assertEqual(initial["source"], "fallback")
        manual_teacher = self.ids[0]
        set_manual_color(self.db, "school-a", manual_teacher, "#ABCDEF", 1, "admin")
        seeded = seed_approved(self.db, "school-a", "admin")
        self.assertEqual(seeded["updated"], len(self.ids))
        self.assertEqual(get_color(self.db, "school-a", manual_teacher)["base_color"], APPROVED_SOURCE_COLORS[manual_teacher]["color"])
        self.assertEqual(get_color(self.db, "school-a", manual_teacher)["color"], "#ABCDEF")
        repeated = seed_approved(self.db, "school-a", "admin")
        self.assertEqual(repeated["inserted"], 0)
        self.assertEqual(repeated["updated"], 0)
        chosen_id = self.ids[-1]
        set_manual_color(self.db, "school-a", chosen_id, "#123456", 2, "admin")
        seed_approved(self.db, "school-a", "admin")
        chosen = get_color(self.db, "school-a", chosen_id)
        self.assertEqual(chosen["color"], "#123456")
        self.assertEqual(chosen["manual_color"], "#123456")

    def test_approved_source_color_keeps_original_hex_and_allows_duplicates(self):
        self.assertEqual(len(APPROVED_SOURCE_COLORS), 14)
        seed_approved(self.db, "school-a")
        teacher_id = list(APPROVED_SOURCE_COLORS)[1]
        self.assertEqual(get_color(self.db, "school-a", teacher_id)["base_color"], "#4285F4")
        self.assertEqual(get_color(self.db, "school-a", teacher_id)["source"], "approved_source")

    def test_settings_migration_preview_is_read_only_and_rejects_unknown_ids(self):
        before = list_colors(self.db, "school-a")
        invalid = preview_settings_migration(self.db, "school-a", {"overrides": {"demo-teacher": "#ABCDEF"}})
        self.assertFalse(invalid["ready"])
        self.assertEqual(invalid["issues"][0]["teacher_id"], "demo-teacher")
        self.assertEqual(list_colors(self.db, "school-a"), before)
        valid_settings = {"assignments": {"100": 4}, "overrides": {"100": "#FEDCBA"}}
        preview = preview_settings_migration(self.db, "school-a", valid_settings)
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["plan"][0]["status"], "will_add")
        self.assertTrue(preview["plan"][0]["current_color"].startswith("#"))
        result = confirm_settings_migration(self.db, "school-a", valid_settings,
                                            {item["teacher_id"]: item["expected_revision"] for item in preview["plan"]},
                                            "admin")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(get_color(self.db, "school-a", "100")["color"], "#FEDCBA")
        replacement = preview_settings_migration(self.db, "school-a", {"overrides": {"100": "#ABCDEF"}})
        self.assertEqual(replacement["plan"][0]["status"], "replace_manual")
        self.assertEqual(replacement["plan"][0]["current_color"], "#FEDCBA")
        with self.assertRaises(TeacherColorConflict):
            confirm_settings_migration(self.db, "school-a", valid_settings, {"100": 0}, "admin")

    def test_registry_never_writes_lesson_tables(self):
        with self.db.connection() as connection:
            before = connection.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        initialize_missing(self.db, "school-a")
        seed_approved(self.db, "school-a")
        set_manual_color(self.db, "school-a", self.ids[0], "#ABCDEF", 2)
        with self.db.connection() as connection:
            after = connection.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
            names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertEqual(before, after)
        self.assertIn("teacher_ui_colors", names)


if __name__ == "__main__":
    unittest.main()
