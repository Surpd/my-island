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

        ddl = (Path(__file__).parents[1] / "migrations/053_codex_diagnostic_exchange_budget.sql").read_text()
        with psycopg.connect(URL, connect_timeout=5) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
        self.addCleanup(clean)
        with IsolatedDatabase(database_url=URL).connection() as connection:
            # Use the actual migration table shape, isolated from public.
            connection.execute(ddl.split("ALTER TABLE", 1)[0].replace("public.", ""))
        gate = Barrier(12)

        def attempt(_):
            gate.wait(timeout=10)
            return _take_exchange_attempt(IsolatedDatabase(database_url=URL))

        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(sum(pool.map(attempt, range(12))), 5)
        with IsolatedDatabase(database_url=URL).connection() as connection:
            self.assertEqual(connection.execute("SELECT id, attempts FROM codex_diagnostic_exchange_budget").fetchall(),
                             [{"id": 1, "attempts": 5}])
