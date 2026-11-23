"""Concurrency smoke on a dedicated disposable PostgreSQL instance."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
from threading import Barrier
import unittest
from urllib.parse import urlparse
from uuid import uuid4

from backend.database import Database
from backend.services.codex_diagnostics import _take_exchange_attempt

URL = os.getenv("MY_ISLAND_RELEASE_TEST_DATABASE_URL", "")


@unittest.skipUnless(URL, "requires isolated MY_ISLAND_RELEASE_TEST_DATABASE_URL")
class CodexDiagnosticBudgetPostgresTests(unittest.TestCase):
    def test_five_of_twelve_concurrent_attempts_admitted(self):
        import psycopg
        from psycopg.rows import dict_row

        address = urlparse(URL)
        if (address.scheme not in ("postgres", "postgresql")
                or address.hostname not in ("localhost", "127.0.0.1", "::1")
                or address.port != 55432 or address.path != "/my_island_release_check" or address.query):
            raise RuntimeError("Use the dedicated disposable loopback release database")
        schema = "diagnostic_budget_it_" + uuid4().hex

        class IsolatedDatabase(Database):
            @contextmanager
            def connection(self):
                with psycopg.connect(URL, row_factory=dict_row, connect_timeout=5) as connection:
                    connection.execute(f'SET search_path TO "{schema}"')
                    yield connection

        def clean():
            with psycopg.connect(URL, connect_timeout=5) as connection:
                connection.execute(f'DROP SCHEMA "{schema}" CASCADE')

        migrations = Path(__file__).parents[1] / "migrations"
        names = ("052_codex_diagnostic_sessions.sql", "053_codex_diagnostic_exchange_budget.sql")
        session_table = "codex_diagnostic_sessions"
        budget_table = "codex_diagnostic_exchange_budget"
        with psycopg.connect(URL, connect_timeout=5) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
        self.addCleanup(clean)
        with IsolatedDatabase(database_url=URL).connection() as connection:
            # Run full migration SQL, including RLS and GRANT boundaries, in
            # a disposable schema. Supply normal Supabase roles in the
            # disposable cluster setup only; never mutate roles here.
            for name in names:
                text = (migrations / name).read_text().replace("public.", f'"{schema}".')
                connection.execute(text)
            for table in (session_table, budget_table):
                rel = f"{schema}.{table}"
                access = connection.execute("""SELECT relrowsecurity AS rls,
                    has_table_privilege('anon', %s, 'SELECT,INSERT,UPDATE,DELETE') AS anon_access,
                    has_table_privilege('authenticated', %s, 'SELECT,INSERT,UPDATE,DELETE') AS authenticated_access,
                    has_table_privilege(current_user, %s, 'SELECT') AS owner_read,
                    has_table_privilege(current_user, %s, 'INSERT') AS owner_insert,
                    has_table_privilege(current_user, %s, 'UPDATE') AS owner_update
                    FROM pg_class WHERE oid=to_regclass(%s)""", (rel, rel, rel, rel, rel, rel)).fetchone()
                self.assertEqual(access, {"rls": True, "anon_access": False, "authenticated_access": False,
                                          "owner_read": True, "owner_insert": True, "owner_update": True})
        gate = Barrier(12)

        def attempt(_):
            gate.wait(timeout=10)
            return _take_exchange_attempt(IsolatedDatabase(database_url=URL))

        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(sum(pool.map(attempt, range(12))), 5)
        with IsolatedDatabase(database_url=URL).connection() as connection:
            self.assertEqual(connection.execute("SELECT id, attempts FROM codex_diagnostic_exchange_budget").fetchall(),
                             [{"id": 1, "attempts": 5}])
