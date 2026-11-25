"""Synthetic two-session directory edits on SQLite and a dedicated PostgreSQL 17 database."""
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.services.directory_revisions import add_group_members, directory_edit, directory_history


class RevisionContract:
    def setUp(self):
        self.suffix = uuid4().hex[:14]
        self.actor = self.db.get_or_create_user(int(self.suffix, 16), "admin")["id"]
        self.teacher = self.db.create_identity("teacher", "Fixture " + self.suffix)["id"]
        self.group = self.db.create_group("G " + self.suffix, "instructional")["id"]

    def revision(self, entity=None):
        return self.db.get_identity(entity or self.teacher)["revision"]

    def test_concurrent_subject_replacement_has_one_winner_and_stale_retry(self):
        start = Barrier(2)
        rev = self.revision()

        def save(subject):
            start.wait(10)
            try:
                with directory_edit(self.db, "identities", self.teacher, rev, self.actor) as db:
                    db.update_admin_identity(self.teacher, {"subjects": [subject]}, self.actor)
                return 200, subject
            except HTTPException as error:
                self.assertEqual(error.detail["code"], "directory_revision_conflict")
                return error.status_code, subject

        with ThreadPoolExecutor(2) as pool:
            results = sorted(pool.map(save, ["Synthetic A", "Synthetic B"]))
        self.assertEqual([status for status, _ in results], [200, 409])
        self.assertEqual(self.db.list_teacher_subjects(self.teacher), [results[0][1]])
        history = directory_history(self.db, "identities", self.teacher)["items"]
        self.assertEqual(history[0]["actor_id"], str(self.actor))
        self.assertEqual(history[0]["changed_fields"], ["subjects"])
        self.assertTrue(history[0]["changed_at"])
        current = self.revision()
        with directory_edit(self.db, "identities", self.teacher, current, self.actor) as db:
            db.update_admin_identity(self.teacher, {"subjects": [results[0][1]]}, self.actor)
        self.assertEqual(current, self.revision())

    def test_related_assignment_invalidates_both_cards_and_rolls_back(self):
        before = self.revision()
        group_revision = self.db.get_group(self.group)["revision"]
        with self.assertRaisesRegex(ValueError, "synthetic"):
            with directory_edit(self.db, "identities", self.teacher, before, self.actor) as db:
                db.set_teacher_assignment(self.teacher, self.group, "Math", True, self.actor)
                raise ValueError("synthetic failure after relation write")
        self.assertEqual(before, self.revision())
        self.assertEqual(group_revision, self.db.get_group(self.group)["revision"])
        with directory_edit(self.db, "identities", self.teacher, before, self.actor) as db:
            db.set_teacher_assignment(self.teacher, self.group, "Math", True, self.actor)
        with self.assertRaises(HTTPException) as stale:
            with directory_edit(self.db, "groups", self.group, group_revision, self.actor):
                self.fail("stale group opened")
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(directory_history(self.db, "groups", self.group)["items"][0]["changed_fields"], ["assignments"])

    def test_batch_memberships_and_group_fields_roll_back_together(self):
        student = self.db.create_identity("student", "Fixture student " + self.suffix)["id"]
        group_rev = self.db.get_group(self.group)["revision"]
        with self.assertRaises(HTTPException):
            with directory_edit(self.db, "groups", self.group, group_rev, self.actor) as db:
                db.update_admin_group(self.group, {"comment": "rollback"}, self.actor)
                add_group_members(db, self.group, [{"identity_id": str(student), "expected_revision": self.revision(student) + 1}], self.actor)
        self.assertEqual(self.db.get_group(self.group)["revision"], group_rev)
        self.assertIsNone(self.db.get_group(self.group)["admin_note"])
        student_rev = self.revision(student)
        with directory_edit(self.db, "groups", self.group, group_rev, self.actor) as db:
            add_group_members(db, self.group, [{"identity_id": str(student), "expected_revision": student_rev}], self.actor)
        self.assertGreater(self.revision(student), student_rev)
        self.assertGreater(self.db.get_group(self.group)["revision"], group_rev)


class SQLiteDirectoryTests(RevisionContract, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Database(Path(temporary.name) / "directory.db")
        self.db.initialize()
        super().setUp()

    def test_history_permissions_conflict_and_missing_revision(self):
        with self.db.connection() as connection:
            connection.execute("INSERT OR IGNORE INTO user_roles(user_id,role) VALUES (?,'admin')", (self.actor,))
        code, _ = self.db.create_admin_login_challenge(self.actor)
        token, _ = self.db.consume_admin_login_challenge(code)
        app = FastAPI()
        app.include_router(create_router(self.db))
        client = TestClient(app)
        headers = {"X-Dev-Auth": f"browser:{token}"}
        path = f"/api/admin/people/{self.teacher}"
        history = f"/api/admin/directory/history/identities/{self.teacher}"
        self.assertIn(client.get(history).status_code, (401, 403))
        self.assertEqual(client.patch(path, headers=headers, json={"comment": "x"}).status_code, 428)
        rev = self.revision()
        first = client.patch(path, headers=headers, json={"comment": "private fixture", "expected_revision": rev})
        self.assertEqual(first.status_code, 200, first.text)
        conflict = client.patch(path, headers=headers, json={"comment": "other", "expected_revision": rev})
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["detail"]["current"]["revision"], first.json()["revision"])
        result = client.get(history, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["items"][0]["changed_fields"], ["admin_note"])
        self.assertNotIn("private fixture", result.text)


URL = os.getenv("MY_ISLAND_DIRECTORY_TEST_DATABASE_URL", "")


@unittest.skipUnless(URL, "requires isolated directory PostgreSQL fixture")
class PostgresDirectoryTests(RevisionContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        target = urlsplit(URL)
        if (target.hostname not in {"127.0.0.1", "localhost"} or target.port != 55438
                or target.path != "/my_island_directory_check" or target.query):
            raise RuntimeError("Use the dedicated loopback directory fixture")
        cls.db = Database(database_url=URL)
        with cls.db.connection() as connection:
            version = connection.execute("SELECT current_setting('server_version_num')::integer AS v").fetchone()["v"]
            if version // 10000 != 17:
                raise RuntimeError("Requires PostgreSQL 17")
