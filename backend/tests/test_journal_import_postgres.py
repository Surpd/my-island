"""Opt-in integration tests for journal imports on an isolated PostgreSQL schema.

Run only with MY_ISLAND_PG_JOURNAL_TESTS=1 and a DATABASE_URL for the approved
My Island project. The test role must be able to create and drop schemas.
"""

from __future__ import annotations

import os
import unittest
import uuid
from contextlib import contextmanager
from urllib.parse import unquote, urlparse

from backend.database import Database
from backend.services.journal_import import sync_journal_values
from backend.services.journal_matching import (
    confirm_match,
    matching_overview,
    preview_match,
    set_team_members,
)


OPT_IN = os.getenv("MY_ISLAND_PG_JOURNAL_TESTS") == "1"
DATABASE_URL = os.getenv("DATABASE_URL")
PROJECT_REF = "kmrcbewmxeqqxfauiwyy"


class IsolatedPostgresDatabase(Database):
    def __init__(self, database_url: str, schema: str) -> None:
        super().__init__(database_url=database_url)
        self.schema = schema

    @contextmanager
    def connection(self):
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(self.database_url, row_factory=dict_row)
        try:
            connection.execute(f'SET search_path TO "{self.schema}"')
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


@unittest.skipUnless(OPT_IN and DATABASE_URL, "requires explicit PostgreSQL journal-test opt-in and DATABASE_URL")
class JournalImportPostgresTests(unittest.TestCase):
    TABLES = (
        "identities",
        "groups",
        "users",
        "classroom_courses",
        "memberships",
        "journal_sources",
        "journal_assessments",
        "journal_students",
        "journal_results",
        "journal_result_history",
        "journal_student_summaries",
        "journal_summary_history",
        "journal_sync_runs",
        "journal_group_mappings",
        "school_sources",
        "school_source_mappings",
        "audit_log",
    )
    VALUES = [
        ["Дата", "01.09", "02.09", ""],
        ["Задание", "Входное тестирование", "ДЗ", "Итого"],
        ["Вес", "2", "1", ""],
        ["Оценка", "10", "5", ""],
        ["A", "", "", ""],
        ["Иванов Иван", "8,5", "н", "8,5"],
        ["Петрова Анна", "10", "5", "15"],
        ["B", "", "", ""],
        ["Сидоров Пётр", "7", "4", "11"],
    ]

    @classmethod
    def setUpClass(cls):
        from psycopg.conninfo import conninfo_to_dict
        import psycopg

        conninfo = conninfo_to_dict(DATABASE_URL)
        # Fail closed when explicitly enabled against a different configured project.
        if PROJECT_REF not in unquote(conninfo.get("user", "")):
            raise RuntimeError("DATABASE_URL username does not identify the approved My Island project")
        cls.schema = "journal_it_" + uuid.uuid4().hex
        from psycopg.rows import dict_row

        cls.admin_connection = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        try:
            cls.admin_connection.execute(f'CREATE SCHEMA "{cls.schema}"')
            for table in cls.TABLES:
                cls.admin_connection.execute(
                    f'CREATE TABLE "{cls.schema}"."{table}" (LIKE public."{table}" INCLUDING ALL)'
                )
                defaults = cls.admin_connection.execute(
                    "SELECT a.attname AS column_name, pg_get_expr(d.adbin, d.adrelid) AS expression "
                    "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum "
                    "WHERE n.nspname = 'public' AND c.relname = %s AND d.adbin IS NOT NULL",
                    (table,),
                ).fetchall()
                # LIKE INCLUDING DEFAULTS preserves a serial column's public sequence
                # reference. Point copied serial defaults at a sequence in the test schema.
                for default in defaults:
                    if "nextval(" not in default["expression"]:
                        continue
                    column = default["column_name"]
                    sequence = f"{table}_{column}_test_seq"
                    sequence_ref = f'"{cls.schema}"."{sequence}"'
                    cls.admin_connection.execute(f'CREATE SEQUENCE "{cls.schema}"."{sequence}"')
                    cls.admin_connection.execute(
                        f'ALTER TABLE "{cls.schema}"."{table}" ALTER COLUMN "{column}" '
                        f"SET DEFAULT nextval('{sequence_ref}'::regclass)"
                    )
            # Matching-specific schema is created only inside this disposable test
            # schema, so tests remain compatible with production until its migration.
            cls.admin_connection.execute(
                f'ALTER TABLE "{cls.schema}".journal_students '
                "ADD COLUMN entry_kind text NOT NULL DEFAULT 'individual', "
                "ADD COLUMN classification_method text"
            )
            cls.admin_connection.execute(
                f'''CREATE TABLE "{cls.schema}".journal_team_members (
                    journal_student_id uuid NOT NULL REFERENCES "{cls.schema}".journal_students(id) ON DELETE CASCADE,
                    identity_id uuid NOT NULL REFERENCES "{cls.schema}".identities(id),
                    created_by uuid REFERENCES "{cls.schema}".users(id),
                    created_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (journal_student_id, identity_id)
                )'''
            )
            # LIKE INCLUDING ALL copies indexes and CHECK constraints, but PostgreSQL
            # deliberately omits foreign keys. Recreate the import path's key links
            # against the cloned tables so FK behavior is exercised locally.
            local_foreign_keys = {
                "memberships": ("FOREIGN KEY (group_id) REFERENCES {s}.groups(id)", "FOREIGN KEY (identity_id) REFERENCES {s}.identities(id)"),
                "journal_assessments": ("FOREIGN KEY (source_id) REFERENCES {s}.journal_sources(id) ON DELETE CASCADE",),
                "journal_students": ("FOREIGN KEY (source_id) REFERENCES {s}.journal_sources(id) ON DELETE CASCADE", "FOREIGN KEY (identity_id) REFERENCES {s}.identities(id)"),
                "journal_team_members": ("FOREIGN KEY (journal_student_id) REFERENCES {s}.journal_students(id) ON DELETE CASCADE", "FOREIGN KEY (identity_id) REFERENCES {s}.identities(id)"),
                "journal_results": ("FOREIGN KEY (assessment_id) REFERENCES {s}.journal_assessments(id) ON DELETE CASCADE", "FOREIGN KEY (journal_student_id) REFERENCES {s}.journal_students(id) ON DELETE SET NULL", "FOREIGN KEY (identity_id) REFERENCES {s}.identities(id)"),
                "journal_student_summaries": ("FOREIGN KEY (journal_student_id) REFERENCES {s}.journal_students(id) ON DELETE CASCADE",),
                "journal_group_mappings": ("FOREIGN KEY (source_id) REFERENCES {s}.journal_sources(id) ON DELETE CASCADE", "FOREIGN KEY (group_id) REFERENCES {s}.groups(id)", "FOREIGN KEY (classroom_course_id) REFERENCES {s}.classroom_courses(id)", "FOREIGN KEY (created_by) REFERENCES {s}.users(id)"),
                "school_source_mappings": ("FOREIGN KEY (source_id) REFERENCES {s}.school_sources(id)", "FOREIGN KEY (identity_id) REFERENCES {s}.identities(id)", "FOREIGN KEY (group_id) REFERENCES {s}.groups(id)", "FOREIGN KEY (created_by) REFERENCES {s}.users(id)", "FOREIGN KEY (supersedes_mapping_id) REFERENCES {s}.school_source_mappings(id)"),
            }
            for table, definitions in local_foreign_keys.items():
                for index, definition in enumerate(definitions):
                    cls.admin_connection.execute(
                        f'ALTER TABLE "{cls.schema}"."{table}" ADD CONSTRAINT "it_fk_{table}_{index}" '
                        + definition.format(s=f'"{cls.schema}"')
                    )
            cls.admin_connection.commit()
        except Exception:
            cls.admin_connection.rollback()
            cls.admin_connection.close()
            raise

    @classmethod
    def tearDownClass(cls):
        if not hasattr(cls, "admin_connection"):
            return
        try:
            cls.admin_connection.execute(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE')
            cls.admin_connection.commit()
        finally:
            cls.admin_connection.close()

    def setUp(self):
        self.database = IsolatedPostgresDatabase(DATABASE_URL, self.schema)
        with self.database.connection() as connection:
            connection.execute("TRUNCATE audit_log, school_source_mappings, school_sources, journal_team_members, journal_group_mappings, journal_sync_runs, journal_summary_history, journal_student_summaries, journal_result_history, journal_results, journal_students, journal_assessments, journal_sources, memberships, groups, identities, users RESTART IDENTITY CASCADE")
            connection.execute(
                "INSERT INTO identities(kind, display_name, class_name, status) VALUES ('student', %s, '9-А', 'active')",
                ("Иванов Иван",),
            )
            connection.execute(
                "INSERT INTO identities(kind, display_name, class_name, status) VALUES ('student', %s, '9-А', 'active')",
                ("Петрова Анна",),
            )

    def _rows(self, query, params=()):
        with self.database.connection() as connection:
            return connection.execute(query, params).fetchall()

    def _matching_fixture(self, spreadsheet_id="synthetic-matching"):
        values = [
            ["Дата", "01.09"], ["Задание", "Проверочная"], ["Вес", "1"],
            ["Оценка", "10"], ["A", ""], ["Synthetic Student", "8"],
            ["Иванов Иван", "9"],
        ]
        for subject, sheet_id in (("Математика", 904), ("Физика", 905)):
            sync_journal_values(
                self.database, values, spreadsheet_id=spreadsheet_id,
                spreadsheet_title="Synthetic matching journal", grade=9,
                subject=subject, sheet_title=subject, sheet_id=sheet_id,
            )
        source = self._rows("SELECT id FROM journal_students WHERE display_name='Synthetic Student' ORDER BY id LIMIT 1")[0]
        identity = self._rows("SELECT id FROM identities WHERE display_name='Иванов Иван'")[0]
        with self.database.connection() as connection:
            actor = connection.execute(
                "INSERT INTO users(telegram_user_id, role) VALUES (%s, 'admin') RETURNING id",
                (900000 + int(uuid.uuid4().hex[:8], 16),),
            ).fetchone()["id"]
        return str(source["id"]), str(identity["id"]), str(actor)

    def test_matching_preview_is_read_only_and_confirmation_updates_only_journal_links(self):
        student_id, identity_id, actor_id = self._matching_fixture()
        before = self._rows("SELECT identity_id, match_status FROM journal_students WHERE id=%s", (student_id,))[0]
        overview = matching_overview(self.database, "synthetic-matching")
        self.assertEqual(len(overview["individuals"]), 1)
        preview = preview_match(self.database, "synthetic-matching", "synthetic student", identity_id)
        self.assertEqual(preview["record_count"], 2)
        self.assertEqual(preview["subject_count"], 2)
        self.assertEqual(preview["result_count"], 2)
        self.assertEqual(self._rows("SELECT identity_id, match_status FROM journal_students WHERE id=%s", (student_id,))[0], before)

        confirmed = confirm_match(
            self.database, "synthetic-matching", "Synthetic Student", identity_id,
            preview["preview_token"], actor_id,
        )
        self.assertEqual(confirmed, {"matched_records": 2, "updated_results": 2})
        student = self._rows("SELECT identity_id, match_status, match_method FROM journal_students WHERE id=%s", (student_id,))[0]
        self.assertEqual(str(student["identity_id"]), identity_id)
        self.assertEqual((student["match_status"], student["match_method"]), ("linked", "admin_source_alias"))
        self.assertEqual(len(self._rows("SELECT id FROM school_source_mappings WHERE external_key='alias:synthetic student' AND manually_confirmed IS TRUE")), 1)
        self.assertEqual(len(self._rows("SELECT id FROM audit_log WHERE action='journal_matching.confirmed'")), 1)
        unrelated = self._rows("SELECT id, identity_id FROM journal_students WHERE display_name='Иванов Иван' ORDER BY id")
        self.assertEqual(len(unrelated), 2)
        self.assertTrue(all(str(row["identity_id"]) == identity_id for row in unrelated))
        result_ids = self._rows("SELECT id FROM journal_results WHERE journal_student_id IN (SELECT id FROM journal_students WHERE display_name='Synthetic Student') ORDER BY id")
        with self.database.connection() as connection:
            connection.execute("UPDATE journal_students SET identity_id=NULL, match_status='unlinked', match_method=NULL WHERE display_name='Synthetic Student'")
            connection.execute("UPDATE journal_results SET identity_id=NULL WHERE journal_student_id IN (SELECT id FROM journal_students WHERE display_name='Synthetic Student')")
        self._matching_fixture("synthetic-matching")
        after_repeat = self._rows("SELECT id, match_method, identity_id FROM journal_students WHERE display_name='Synthetic Student' ORDER BY id")
        self.assertEqual(len(after_repeat), 2)
        self.assertTrue(all(str(row["identity_id"]) == identity_id and row["match_method"] == "saved_alias" for row in after_repeat))
        self.assertEqual(self._rows("SELECT id FROM journal_results WHERE journal_student_id IN (SELECT id FROM journal_students WHERE display_name='Synthetic Student') ORDER BY id"), result_ids)
        self.assertEqual(len(self._rows("SELECT id FROM school_source_mappings WHERE external_key='alias:synthetic student' AND manually_confirmed IS TRUE")), 1)
        with self.assertRaises(ValueError):
            confirm_match(self.database, "synthetic-matching", "Synthetic Student", identity_id, preview["preview_token"], actor_id)

    def test_matching_rejects_stale_previews_and_wrong_grade_candidates(self):
        student_id, identity_id, actor_id = self._matching_fixture("synthetic-stale")
        preview = preview_match(self.database, "synthetic-stale", "Synthetic Student", identity_id)
        with self.database.connection() as connection:
            group_id = connection.execute("INSERT INTO groups(name, group_type, base_class_name) VALUES ('Synthetic Group', 'class', '9-А') RETURNING id").fetchone()["id"]
            connection.execute("INSERT INTO memberships(group_id, identity_id, member_role, source, active) VALUES (%s, %s, 'student', 'official_import', TRUE)", (group_id, identity_id))
        with self.assertRaises(ValueError):
            confirm_match(self.database, "synthetic-stale", "Synthetic Student", identity_id, preview["preview_token"], actor_id)
        with self.database.connection() as connection:
            other_grade = connection.execute(
                "INSERT INTO identities(kind, display_name, class_name, status) VALUES ('student', 'Synthetic Other Grade', '8-Б', 'active') RETURNING id"
            ).fetchone()["id"]
        with self.assertRaises(ValueError):
            preview_match(self.database, "synthetic-stale", "Synthetic Student", other_grade)
        with self.database.connection() as connection:
            merged = connection.execute(
                "INSERT INTO identities(kind, display_name, class_name, status) VALUES ('student', 'Synthetic Merged', '9-А', 'merged') RETURNING id"
            ).fetchone()["id"]
        with self.assertRaises(ValueError):
            preview_match(self.database, "synthetic-stale", "Synthetic Student", merged)

    def test_conflicting_confirmed_alias_rejects_without_partial_changes(self):
        student_id, identity_id, actor_id = self._matching_fixture("synthetic-alias-conflict")
        other_identity_id = self._rows("SELECT id FROM identities WHERE display_name='Петрова Анна'")[0]["id"]
        preview = preview_match(self.database, "synthetic-alias-conflict", "Synthetic Student", identity_id)
        with self.database.connection() as connection:
            source_id = connection.execute("SELECT id FROM school_sources WHERE external_key='synthetic-alias-conflict'").fetchone()["id"]
            connection.execute(
                "INSERT INTO school_source_mappings(source_id, external_key, mapping_type, identity_id, status, manually_confirmed, evidence, created_by) "
                "VALUES (%s, 'alias:synthetic student', 'identity', %s, 'confirmed', TRUE, '{\"source\":\"test\"}'::jsonb, %s)",
                (source_id, other_identity_id, actor_id),
            )
        with self.assertRaises(ValueError):
            confirm_match(self.database, "synthetic-alias-conflict", "Synthetic Student", identity_id, preview["preview_token"], actor_id)
        self.assertTrue(all(row["identity_id"] is None for row in self._rows("SELECT identity_id FROM journal_students WHERE display_name='Synthetic Student'")))
        self.assertEqual(len(self._rows("SELECT id FROM audit_log WHERE action='journal_matching.confirmed'")), 0)
        self.assertEqual(str(self._rows("SELECT identity_id FROM school_source_mappings WHERE external_key='alias:synthetic student'")[0]["identity_id"]), str(other_identity_id))

    def test_team_membership_keeps_results_synthetic_and_survives_repeat_sync(self):
        student_id, identity_id, actor_id = self._matching_fixture("synthetic-team")
        with self.assertRaises(ValueError):
            set_team_members(self.database, student_id, [identity_id], actor_id)
        with self.database.connection() as connection:
            connection.execute("UPDATE journal_students SET entry_kind='team', display_name='Synthetic Team' WHERE id=%s", (student_id,))
            second = connection.execute("SELECT id FROM identities WHERE display_name='Петрова Анна'").fetchone()["id"]
        members = set_team_members(self.database, student_id, [identity_id, second, identity_id], actor_id)
        self.assertEqual(set(members["identity_ids"]), {identity_id, str(second)})
        with self.assertRaises(ValueError):
            preview_match(self.database, "synthetic-team", "Synthetic Team", identity_id)
        journal_counts = {str(row["id"]): row for row in self.database.journal_status() if row["spreadsheet_id"] == "synthetic-team"}
        team_source = self._rows("SELECT source_id FROM journal_students WHERE id=%s", (student_id,))[0]["source_id"]
        self.assertEqual(journal_counts[str(team_source)]["team_count"], 1)
        self.assertEqual(journal_counts[str(team_source)]["unmapped_count"], 0)
        before = self._rows("SELECT id, numeric_score, identity_id FROM journal_results WHERE journal_student_id=%s", (student_id,))[0]
        self.assertIsNone(before["identity_id"])

        self._matching_fixture("synthetic-team")
        student = self._rows("SELECT entry_kind FROM journal_students WHERE id=%s", (student_id,))[0]
        self.assertEqual(student["entry_kind"], "team")
        self.assertEqual(len(self._rows("SELECT identity_id FROM journal_team_members WHERE journal_student_id=%s", (student_id,))), 2)
        after = self._rows("SELECT id, numeric_score, identity_id FROM journal_results WHERE journal_student_id=%s", (student_id,))[0]
        self.assertEqual(after, before)

    def test_full_sync_repeat_reordered_changed_scores_jsonb_and_constraints(self):
        first = sync_journal_values(
            self.database, self.VALUES, spreadsheet_id="synthetic-journal",
            spreadsheet_title="Synthetic journal", grade=9, subject="Математика",
            sheet_title="Математика", sheet_id=904,
        )
        self.assertEqual(first["assessments"], 2)
        self.assertEqual(first["roster_students"], 3)
        initial_rows = self._rows("SELECT id, title, external_key, value_signature, group_marker, assessed_on FROM journal_assessments WHERE active IS TRUE")
        initial_assessments = {row["title"]: row["id"] for row in initial_rows}
        self.assertEqual(len(initial_assessments), 2)
        self.assertTrue(self._rows("SELECT 1 FROM school_sources WHERE source_type = 'journal' AND configuration ? 'grade'"))
        self.assertTrue(self._rows("SELECT 1 FROM journal_assessments WHERE jsonb_typeof(raw_source) = 'object' LIMIT 1"))

        repeated = sync_journal_values(
            self.database, self.VALUES, spreadsheet_id="synthetic-journal",
            spreadsheet_title="Synthetic journal", grade=9, subject="Математика",
            sheet_title="Математика", sheet_id=904,
        )
        self.assertEqual(repeated["results"], 6)
        self.assertEqual(len(self._rows("SELECT id FROM journal_results WHERE is_current IS TRUE")), 6)
        self.assertFalse(self._rows("SELECT id FROM journal_result_history"))

        changed = [list(row) for row in self.VALUES]
        changed[1][1] = "Домашняя работа"
        changed[5], changed[6] = changed[6], changed[5]
        for row in changed:
            row.insert(2, "")
        changed[0][2] = "03.09"
        changed[1][2] = "Новая работа"
        changed[2][2] = 1
        changed[3][2] = 10
        changed[5][2] = 5
        changed[6][2] = 3
        changed[8][2] = 2
        changed[5][3] = 4
        sync_journal_values(
            self.database, changed, spreadsheet_id="synthetic-journal",
            spreadsheet_title="Synthetic journal", grade=9, subject="Математика",
            sheet_title="Математика", sheet_id=904,
        )
        updated_rows = self._rows("SELECT id, title, external_key, value_signature, group_marker, assessed_on FROM journal_assessments WHERE active IS TRUE")
        updated = {row["title"]: row["id"] for row in updated_rows}
        self.assertEqual(updated["Домашняя работа"], initial_assessments["Входное тестирование"], f"initial={initial_rows!r}; updated={updated_rows!r}")
        self.assertEqual(updated["ДЗ"], initial_assessments["ДЗ"])
        self.assertIn("Новая работа", updated)
        history_count = len(self._rows("SELECT id FROM journal_result_history"))
        self.assertGreaterEqual(history_count, 1)
        self.assertTrue(self._rows("SELECT id FROM journal_results WHERE is_current IS TRUE AND jsonb_typeof(raw_source) = 'object' LIMIT 1"))

        sync_journal_values(
            self.database, changed, spreadsheet_id="synthetic-journal",
            spreadsheet_title="Synthetic journal", grade=9, subject="Математика",
            sheet_title="Математика", sheet_id=904,
        )
        self.assertEqual(len(self._rows("SELECT id FROM journal_result_history")), history_count)
        self.assertEqual(len(self._rows("SELECT id FROM journal_results WHERE is_current IS TRUE")), 9)

        with self.assertRaises(ValueError):
            sync_journal_values(
                self.database, [["Дата", "01.09"], ["Задание", "Broken"], ["Иванов Иван", 3]],
                spreadsheet_id="synthetic-journal", spreadsheet_title="Synthetic journal",
                grade=9, subject="Математика", sheet_title="Математика", sheet_id=904,
            )
        self.assertEqual(len(self._rows("SELECT id FROM journal_results WHERE is_current IS TRUE")), 9)

        source_id = self._rows("SELECT id FROM journal_sources WHERE spreadsheet_id = 'synthetic-journal'")[0]["id"]
        with self.assertRaises(Exception):
            with self.database.connection() as connection:
                connection.execute(
                    "INSERT INTO journal_students(source_id, student_key, display_name, group_marker, match_status) VALUES (%s, 'bad', 'Synthetic bad row', 'A', 'invalid')",
                    (source_id,),
                )
        self.assertFalse(self._rows("SELECT id FROM journal_students WHERE student_key = 'bad'"))

        run_id = self.database.start_journal_sync("synthetic-run", 9, "Математика")
        self.assertIsNotNone(run_id)
        self.assertIsNone(self.database.start_journal_sync("synthetic-run", 9, "Математика"))
        self.database.finish_journal_sync(run_id, status="success", counts={"results": 6})
        with self.assertRaises(Exception):
            self.database.start_journal_sync("synthetic-invalid-run", 4, "Математика")
        run = self._rows("SELECT status, counts FROM journal_sync_runs WHERE id = %s", (run_id,))[0]
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["counts"]["results"], 6)

    def test_legacy_source_without_sheet_id_is_upgraded_in_place(self):
        with self.database.connection() as connection:
            legacy_source_id = connection.execute(
                "INSERT INTO journal_sources(spreadsheet_id, spreadsheet_title, grade, subject, sheet_title, sheet_id, source_hash, status) "
                "VALUES ('legacy-synthetic', 'Synthetic journal', 9, 'Математика', 'Математика', NULL, 'legacy-hash', 'ready') RETURNING id"
            ).fetchone()["id"]
            legacy_assessment_id = connection.execute(
                "INSERT INTO journal_assessments(source_id, external_key, title, assessed_on, weight, max_score, source_column, raw_source, group_marker, active) "
                "VALUES (%s, 'legacy-assessment', 'Входное тестирование', '2026-09-01', 2, 10, 'B', '{\"legacy\":true}'::jsonb, 'all', TRUE) RETURNING id",
                (legacy_source_id,),
            ).fetchone()["id"]

        sync_journal_values(
            self.database, self.VALUES, spreadsheet_id="legacy-synthetic",
            spreadsheet_title="Synthetic journal", grade=9, subject="Математика",
            sheet_title="Математика", sheet_id=904,
        )

        sources = self._rows("SELECT id, sheet_id FROM journal_sources WHERE spreadsheet_id = 'legacy-synthetic'")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["id"], legacy_source_id)
        self.assertEqual(sources[0]["sheet_id"], "904")
        assessments = self._rows("SELECT id, active FROM journal_assessments WHERE source_id = %s AND title = 'Входное тестирование'", (legacy_source_id,))
        self.assertEqual(len(assessments), 1)
        self.assertEqual(assessments[0]["id"], legacy_assessment_id)
        self.assertIs(assessments[0]["active"], True)

    def test_workbook_lock_blocks_reclaim_until_lock_is_released(self):
        spreadsheet_id = "synthetic-lock"
        with self.database.journal_workbook_lock(spreadsheet_id) as acquired:
            self.assertTrue(acquired)
            with self.database.connection() as connection:
                stale_run_id = connection.execute(
                    "INSERT INTO journal_sync_runs(spreadsheet_id, grade, subject, trigger_kind, status, started_at) "
                    "VALUES (%s, 9, 'Математика', 'manual', 'running', CURRENT_TIMESTAMP - INTERVAL '31 minutes') RETURNING id",
                    (spreadsheet_id,),
                ).fetchone()["id"]

            self.assertIsNone(self.database.start_journal_sync(spreadsheet_id, 9, "Математика"))
            still_running = self._rows("SELECT status FROM journal_sync_runs WHERE id = %s", (stale_run_id,))[0]
            self.assertEqual(still_running["status"], "running")

        replacement_id = self.database.start_journal_sync(spreadsheet_id, 9, "Математика")
        self.assertIsNotNone(replacement_id)
        reclaimed = self._rows("SELECT status FROM journal_sync_runs WHERE id = %s", (stale_run_id,))[0]
        self.assertEqual(reclaimed["status"], "error")


if __name__ == "__main__":
    unittest.main()
