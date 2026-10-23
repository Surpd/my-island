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
            # LIKE INCLUDING ALL copies indexes and CHECK constraints, but PostgreSQL
            # deliberately omits foreign keys. Recreate the import path's key links
            # against the cloned tables so FK behavior is exercised locally.
            local_foreign_keys = {
                "memberships": ("FOREIGN KEY (group_id) REFERENCES {s}.groups(id)", "FOREIGN KEY (identity_id) REFERENCES {s}.identities(id)"),
                "journal_assessments": ("FOREIGN KEY (source_id) REFERENCES {s}.journal_sources(id) ON DELETE CASCADE",),
                "journal_students": ("FOREIGN KEY (source_id) REFERENCES {s}.journal_sources(id) ON DELETE CASCADE", "FOREIGN KEY (identity_id) REFERENCES {s}.identities(id)"),
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
            connection.execute("TRUNCATE school_source_mappings, school_sources, journal_group_mappings, journal_sync_runs, journal_summary_history, journal_student_summaries, journal_result_history, journal_results, journal_students, journal_assessments, journal_sources, memberships, groups, identities RESTART IDENTITY CASCADE")
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
