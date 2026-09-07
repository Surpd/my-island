from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.services.admin import add_membership
from backend.services.school_data import (
    diff_source_records,
    validate_audience_rule,
    validate_candidate_change,
)


class SchoolDataFoundationTests(unittest.TestCase):
    def test_diff_is_deterministic_and_marks_only_changed_units(self):
        changes = diff_source_records(
            {"same": {"name": "A"}, "changed": {"name": "B"}, "deleted": {"name": "C"}},
            {"same": {"name": "A"}, "changed": {"name": "B2"}, "new": {"name": "D"}},
        )
        self.assertEqual(
            [(item["record_key"], item["change_kind"]) for item in changes],
            [("changed", "changed"), ("deleted", "deleted"), ("new", "new"), ("same", "unchanged")],
        )

    def test_candidates_require_traceable_source_evidence(self):
        candidate = {
            "entity_type": "membership",
            "change_type": "create",
            "natural_key": "student:1/group:2",
            "proposed_payload": {"identity_id": 1, "group_id": 2},
            "evidence": {"source_ref": "sheet:Groups!C12"},
        }
        validate_candidate_change(candidate)
        with self.assertRaisesRegex(ValueError, "evidence"):
            validate_candidate_change({**candidate, "evidence": {}})

    def test_audience_rule_supports_complement_and_union(self):
        validate_audience_rule({
            "type": "complement",
            "base": {"type": "cohort", "group_id": "grade-11"},
            "exclude": {
                "type": "union",
                "rules": [
                    {"type": "cohort", "group_id": "informatics"},
                    {"type": "explicit", "identity_ids": ["exception"]},
                ],
            },
        })

    def test_teacher_membership_cannot_grant_assignment_access(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            teacher = database.create_identity("teacher", "Teacher")
            group = database.create_group("Math A", "subject_group")
            with database.connection() as connection:
                user_id = connection.execute(
                    "INSERT INTO users(telegram_user_id, role, identity_id) VALUES (1, 'teacher', ?) RETURNING id",
                    (teacher["id"],),
                ).fetchone()[0]
            with self.assertRaisesRegex(ValueError, "Student membership"):
                add_membership(database, group["id"], teacher["id"], member_role="teacher")
            self.assertFalse(database.teacher_can_access_group(user_id, group["id"]))
            database.set_teacher_assignment(teacher["id"], group["id"], "Математика", True, user_id)
            self.assertTrue(database.teacher_can_access_group(user_id, group["id"]))

    def test_expired_membership_is_history_not_current_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            student = database.create_identity("student", "Student")
            group = database.create_group("9-А", "class")
            with database.connection() as connection:
                user_id = connection.execute(
                    "INSERT INTO users(telegram_user_id, role, identity_id) VALUES (2, 'student', ?) RETURNING id",
                    (student["id"],),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO memberships(group_id, identity_id, member_role, source, valid_from, valid_until) VALUES (?, ?, 'student', 'official_import', '2025-09-01', '2026-06-30')",
                    (group["id"], student["id"]),
                )
            self.assertEqual(database.get_student_profile(user_id)["groups"], [])

    def test_admin_journal_mapping_survives_automatic_sync_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            admin_group = database.create_group("Math A", "subject_group")
            sync_group = database.create_group("Math B", "subject_group")
            with database.connection() as connection:
                source_id = connection.execute(
                    "INSERT INTO journal_sources(spreadsheet_id, spreadsheet_title, grade, subject, sheet_title) VALUES ('s', 'Journal', 9, 'Math', 'Math') RETURNING id"
                ).fetchone()[0]
            database.map_journal_group(source_id, "A", admin_group["id"], None, source="admin_override")
            mapping = database.map_journal_group(source_id, "A", sync_group["id"], None, source="school_mapping")
            self.assertEqual(mapping["group_id"], admin_group["id"])
            self.assertEqual(mapping["source"], "admin_override")

    def test_postgres_migration_contains_review_and_deny_by_default_boundaries(self):
        migration = Path(__file__).parents[1].joinpath("migrations", "013_school_directory_sync_foundation.sql").read_text(encoding="utf-8")
        for table in (
            "school_source_snapshots", "school_source_records", "school_candidate_changes",
            "school_resolution_issues", "school_semantic_interpretations", "school_source_mappings",
        ):
            self.assertIn(f"create table public.{table}", migration)
            self.assertIn(f"alter table public.{table} enable row level security", migration)
        self.assertIn("audience_rule jsonb", migration)
        self.assertIn("manual_decision boolean", migration)
        self.assertIn("set search_path = ''", migration)
        self.assertIn("protect_manual_school_mapping", migration)
        fk_indexes = Path(__file__).parents[1].joinpath("migrations", "014_school_directory_fk_indexes.sql").read_text(encoding="utf-8")
        self.assertIn("school_source_snapshots_previous_idx", fk_indexes)
        self.assertIn("school_source_mappings_supersedes_idx", fk_indexes)


if __name__ == "__main__":
    unittest.main()
