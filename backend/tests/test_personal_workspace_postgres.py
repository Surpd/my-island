import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlsplit
from uuid import uuid4

from backend.database import Database
from backend.services.personal_workspace import LayoutUpdate, read_layout, save_layout


@unittest.skipUnless(os.getenv("MY_ISLAND_LAYOUT_TEST_URL"), "requires the isolated layout database")
class PostgresLayoutTests(unittest.TestCase):
    def test_concurrent_create_update_and_independent_owner(self):
        url = os.environ["MY_ISLAND_LAYOUT_TEST_URL"]
        parsed = urlsplit(url)
        self.assertIn(parsed.hostname, {"127.0.0.1", "localhost"})
        self.assertEqual(parsed.path, "/my_island_layout_test")
        db = Database(database_url=url)
        with db.connection() as conn:
            version = db.execute(conn, "SHOW server_version_num").fetchone()["server_version_num"]
            self.assertEqual(int(version) // 10000, 17)
            actors = [uuid4(), uuid4()]
            for actor in actors:
                db.execute(conn, "INSERT INTO users(id) VALUES (?)", (actor,))
        today = date(2026, 12, 31)
        try:
            for rev in (0, 1):
                change = LayoutUpdate(expected_revision=rev, widgets=[])
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = [pool.submit(save_layout, db, actors[0], change, today) for _ in range(2)]
                    self.assertEqual(sorted(job.result()[0] for job in results), [False, True])
            self.assertEqual(read_layout(db, actors[0], today)["revision"], 2)
            self.assertEqual(read_layout(db, actors[1], today)["revision"], 0)
        finally:
            with db.connection() as conn:
                db.execute(conn, "DELETE FROM users WHERE id IN (?,?)", tuple(actors))
