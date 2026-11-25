"""Real PostgreSQL 17 claim/lease test for the queue, with an in-memory Sheets API."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import TestCase, skipUnless
from urllib.parse import urlsplit
from uuid import uuid4

from backend.database import Database
from backend.services import published_week_sheets as delivery
from backend.tests.test_published_week_sheets import FakeSheets


@skipUnless(os.getenv("WEEK_SHEETS_TEST_DATABASE_URL"), "requires disposable PostgreSQL 17")
class SheetLeasePostgresTests(TestCase):
    def test_two_workers_and_expired_lease(self):
        from unittest.mock import patch
        from backend.services.schedule_sheet_export import build_sheet_plan, freeze_week
        dsn = os.environ["WEEK_SHEETS_TEST_DATABASE_URL"]
        parsed = urlsplit(dsn)
        self.assertIn(parsed.hostname, ("localhost", "127.0.0.1"))
        self.assertEqual((parsed.port, parsed.path), (55459, "/my_island_weekly_sheets_test"))
        db = Database(database_url=dsn)
        ident = uuid4().hex
        target = ident
        monday = "2026-12-28"
        with db.connection() as conn:
            version = conn.execute("SHOW server_version_num").fetchone()["server_version_num"]
            self.assertEqual(int(version)//10000, 17)
            # Tests own a dedicated database provisioned by the workflow.
            conn.execute("""CREATE TABLE IF NOT EXISTS schedule_publication_heads(
                scope_key TEXT PRIMARY KEY, revision INTEGER NOT NULL)""")
            conn.execute("INSERT INTO schedule_publication_heads VALUES(%s,1)", ("week:"+monday,))
            conn.execute("INSERT INTO published_week_sheet_bindings(target_id,week_start,sheet_id,delivered_revision) VALUES(%s,%s,101,0)", (target,monday))
            conn.execute("""INSERT INTO published_week_sheet_jobs(target_id,publication_id,week_start,head_revision,directory_json,state)
                VALUES(%s,%s,%s,1,'{}','working')""", (target,ident,monday))
            conn.execute("UPDATE published_week_sheet_jobs SET lease_until=100,lease_token='expired' WHERE publication_id=%s", (ident,))
        snapshot = freeze_week({"source":{"draft_id": ident, "name":"Test", "revision":1},
            "groups":[{"id":"c","group_type":"class","name":"9A"}], "teachers":[],
            "payload":{"classes":["c"],"days":[0],"slots":[{"start":"08:00","end":"09:00"}],"lessons":[]}}, monday)
        plan = build_sheet_plan(snapshot,title="Test week",sheet_id=101,mapping_sheet_id=0,layout="school-grid")
        remote = FakeSheets()
        entered, resume = Event(), Event()
        def render(*_):
            entered.set()
            if not resume.wait(15):
                raise RuntimeError("Test worker timed out")
            return plan
        try:
            with ThreadPoolExecutor(max_workers=2) as pool, patch.object(delivery, "_plan", render):
                self.assertFalse(delivery.deliver_one(db, lambda: remote, now=99))
                first = pool.submit(delivery.deliver_one, db, lambda: remote, 100)
                self.assertTrue(entered.wait(10))
                try:
                    self.assertFalse(pool.submit(delivery.deliver_one, db, lambda: remote, 101).result(timeout=10))
                finally:
                    resume.set()
                self.assertTrue(first.result(timeout=10))
            self.assertEqual(len(remote.history), 1)
            with db.connection() as conn:
                job = conn.execute("SELECT state,attempts FROM published_week_sheet_jobs WHERE publication_id=%s", (ident,)).fetchone()
            self.assertEqual(job, {"state":"delivered","attempts":1})
        finally:
            with db.connection() as conn:
                conn.execute("DELETE FROM published_week_sheet_jobs WHERE publication_id=%s", (ident,))
                conn.execute("DELETE FROM published_week_sheet_bindings WHERE target_id=%s", (target,))
                conn.execute("DELETE FROM schedule_publication_heads WHERE scope_key=%s", ("week:"+monday,))
