"""Exercise current-period switching under PostgreSQL's immediate partial index."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from threading import Event
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

from backend.database import Database

DATABASE_URL = os.getenv("MY_ISLAND_RELEASE_TEST_DATABASE_URL", "")
BOOK = "synthetic-period-book"
YEAR = "2026/27"


class PeriodDatabase(Database):
    def __init__(self, schema):
        super().__init__(database_url=DATABASE_URL)
        self.schema = schema

    @contextmanager
    def connection(self):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=5) as connection:
            connection.execute(f'SET search_path TO "{self.schema}"')
            yield connection


@unittest.skipUnless(DATABASE_URL, "requires isolated MY_ISLAND_RELEASE_TEST_DATABASE_URL")
class JournalCurrentPeriodPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        address = urlparse(DATABASE_URL)
        if (address.scheme not in ("postgres", "postgresql")
                or address.hostname not in ("localhost", "127.0.0.1", "::1")
                or address.port != 55432 or address.path != "/my_island_release_check" or address.query):
            raise RuntimeError("Use the dedicated disposable loopback release database")
        self.schema = "journal_period_it_" + uuid4().hex
        self.database = PeriodDatabase(self.schema)
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
            connection.execute(f'CREATE SCHEMA "{self.schema}"')

        def clean():
            with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
                connection.execute(f'DROP SCHEMA "{self.schema}" CASCADE')

        self.addCleanup(clean)
        with self.database.connection() as connection:
            connection.execute("""CREATE TABLE journal_sources (
                id int PRIMARY KEY, spreadsheet_id text NOT NULL, grade int NOT NULL,
                academic_year text NOT NULL, subject text NOT NULL, term_number int,
                period_status text NOT NULL CHECK (period_status IN ('current','archived','unassigned')));
            CREATE UNIQUE INDEX journal_sources_one_current_period_idx
                ON journal_sources(spreadsheet_id,grade,academic_year,subject)
                WHERE period_status='current';
            CREATE TABLE journal_term_settings (
                spreadsheet_id text NOT NULL, grade int NOT NULL, academic_year text NOT NULL,
                current_term int NOT NULL, updated_at timestamptz DEFAULT now(),
                PRIMARY KEY (spreadsheet_id,grade,academic_year));
            CREATE TABLE journal_results (
                id int PRIMARY KEY, source_id int NOT NULL REFERENCES journal_sources(id), score int NOT NULL);
            """)
            db = self.database
            # The target is physically before the old current row, the ordering
            # which exposed the premature unique key check in the old CASE.
            for row in [
                (1, BOOK, 9, YEAR, "Math", 2, "archived"),
                (2, BOOK, 9, YEAR, "Math", 1, "current"),
                (3, BOOK, 9, YEAR, "Math", 3, "archived"),
                (4, BOOK, 8, YEAR, "Math", 1, "current"),
                (5, BOOK, 9, "2025/26", "Math", 1, "current"),
                (6, BOOK + "-other", 9, YEAR, "Math", 1, "current"),
                (7, BOOK, 9, YEAR, "Biology", None, "unassigned"),
                (8, BOOK, 9, YEAR, "Math", 2, "unassigned"),  # duplicate evidence stays unassigned
            ]:
                db.execute(connection, "INSERT INTO journal_sources VALUES (?,?,?,?,?,?,?)", row)
            db.execute(connection, "INSERT INTO journal_term_settings VALUES (?,?,?,?,now())", (BOOK, 9, YEAR, 1))
            db.execute(connection, "INSERT INTO journal_results VALUES (1,2,5),(2,1,4),(3,4,3)")

    def snapshot(self):
        with self.database.connection() as connection:
            return (
                {row["id"]: (row["term_number"], row["period_status"])
                 for row in connection.execute("SELECT id,term_number,period_status FROM journal_sources").fetchall()},
                connection.execute("SELECT current_term FROM journal_term_settings").fetchone()["current_term"],
                connection.execute("SELECT * FROM journal_results ORDER BY id").fetchall(),
            )

    def test_promotion_demotion_and_no_target_leave_term_one_grades_and_other_scopes_intact(self):
        before, _, results = self.snapshot()
        self.assertFalse(self.database.set_journal_current_term(BOOK, 7, YEAR, 2))
        self.assertFalse(self.database.set_journal_current_term(BOOK, 9, "2027/28", 2))
        self.assertFalse(self.database.set_journal_current_term(BOOK, 9, "2025/26", 2))
        self.assertEqual(self.snapshot(), (before, 1, results))
        self.assertTrue(self.database.set_journal_current_term(BOOK, 9, YEAR, 2))
        current, term, after = self.snapshot()
        self.assertEqual(current, {**before, 1: (2, "current"), 2: (1, "archived")})
        self.assertEqual(term, 2)
        self.assertEqual(after, results)

    def test_failure_during_promotion_rolls_back_all_period_and_grade_data(self):
        before = self.snapshot()
        original = self.database.execute

        def fail_promote(connection, query, params=()):
            if "UPDATE journal_sources SET period_status='current'" in query:
                self.assertEqual(connection.execute("SELECT period_status FROM journal_sources WHERE id=2").fetchone()["period_status"], "archived")
                raise RuntimeError("synthetic failure after demotion")
            return original(connection, query, params)

        with patch.object(self.database, "execute", side_effect=fail_promote):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                self.database.set_journal_current_term(BOOK, 9, YEAR, 2)
        self.assertEqual(self.snapshot(), before)

    def test_competing_switch_and_source_binding_serialize_and_keep_grade_data(self):
        before = self.snapshot()
        first_in_transition, release, competing_entry = Event(), Event(), Event()
        original = self.database._switch_journal_period

        def transition(connection, scope, semester):
            if semester == 2:
                first_in_transition.set()
                if not release.wait(5):
                    raise RuntimeError("test release not signaled")
            else:
                competing_entry.set()
            return original(connection, scope, semester)

        with patch.object(self.database, "_switch_journal_period", side_effect=transition), ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.database.set_journal_current_term, BOOK, 9, YEAR, 2)
            self.assertTrue(first_in_transition.wait(4))
            second_started = Event()

            def bind_other_subject():
                second_started.set()
                return self.database.set_journal_source_term(7, 3, make_current=True)

            second = pool.submit(bind_other_subject)
            try:
                self.assertTrue(second_started.wait(4))
                self.assertFalse(competing_entry.wait(0.25))
                self.assertEqual(self.snapshot(), before)
            finally:
                release.set()
            self.assertTrue(first.result(timeout=5))
            self.assertTrue(second.result(timeout=5))
        status, term, grades = self.snapshot()
        self.assertEqual(term, 3)
        self.assertEqual(status, {**before[0], 1: (2, "archived"), 2: (1, "archived"),
                                  3: (3, "current"), 7: (3, "current")})
        self.assertEqual(grades, before[2])
