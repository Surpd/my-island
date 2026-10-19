import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.school_directory_api import create_school_directory_router


class SchoolGroupArchiveTests(unittest.TestCase):
    def test_archive_requires_confirmation_and_restore_preserves_membership_identity(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "archive.db")
            database.initialize()
            group = database.create_group("Math A", "study", subject="Математика")
            student = database.create_identity("student", "Ученик Тестов")
            teacher = database.create_identity("teacher", "Учитель Тестов")
            included_student = database.create_identity("student", "Ученик по включению")
            excluded_student = database.create_identity("student", "Ученик по исключению")
            with database.connection() as connection:
                connection.execute("INSERT INTO memberships(group_id, identity_id, member_role, source, source_ref, active) VALUES (?, ?, 'student', 'test', 'roster-1', TRUE)", (group["id"], student["id"]))
                connection.execute("INSERT INTO memberships(group_id, identity_id, member_role, source, source_ref, active) VALUES (?, ?, 'student', 'test', 'roster-2', TRUE)", (group["id"], excluded_student["id"]))
                connection.execute("INSERT INTO membership_overrides(identity_id, group_id, member_role, action, reason) VALUES (?, ?, 'student', 'include', 'confirmed include')", (included_student["id"], group["id"]))
                connection.execute("INSERT INTO membership_overrides(identity_id, group_id, member_role, action, reason) VALUES (?, ?, 'student', 'exclude', 'confirmed exclude')", (excluded_student["id"], group["id"]))
                source_id = connection.execute("INSERT INTO school_sources(source_type, external_key, display_name) VALUES ('schedule', 'archive-test', 'Archive test') RETURNING id").fetchone()[0]
                sync_id = connection.execute("INSERT INTO school_sync_runs(source_id, mode, idempotency_key) VALUES (?, 'bootstrap', 'archive-test-sync') RETURNING id", (source_id,)).fetchone()[0]
                snapshot_id = connection.execute("INSERT INTO school_source_snapshots(source_id, sync_run_id, fingerprint, observed_at) VALUES (?, ?, 'archive-test-snapshot', CURRENT_TIMESTAMP) RETURNING id", (source_id, sync_id)).fetchone()[0]
                for index in range(2):
                    connection.execute("INSERT INTO schedule_lessons(source_snapshot_id, record_key, version_kind, resolved_group_ids) VALUES (?, ?, 'weekly', ?)", (snapshot_id, f'archive-lesson-{index}', json.dumps([str(group["id"])])))
                connection.execute("INSERT INTO canonical_schedule_versions(version_id, source_snapshot_id, source_fingerprint, status) VALUES ('archive-template', 'archive-source', 'archive-fingerprint', 'authoritative')")
                block_id = connection.execute("INSERT INTO canonical_schedule_blocks(version_id, block_key, status) VALUES ('archive-template', 'archive-block', 'resolved') RETURNING id").fetchone()[0]
                connection.execute("INSERT INTO canonical_schedule_assignments(block_id, ordinal, activity, metadata) VALUES (?, 0, 'История', ?)", (block_id, json.dumps({"resolved_group_id": str(group["id"])})))
            database.set_teacher_assignment(teacher["id"], group["id"], "Математика", True, None)
            database.set_teacher_assignment(teacher["id"], group["id"], "Литература", False, None)

            preview = database.group_archive_preview(group["id"])
            self.assertEqual(preview["counts"]["active_memberships"], 2)
            self.assertEqual(preview["counts"]["assignments"], 1)
            self.assertEqual(preview["counts"]["historical_assignments"], 1)
            self.assertEqual(preview["counts"]["dated_weeks"], 2)
            self.assertEqual(preview["counts"]["canonical_template"], 1)
            self.assertFalse(preview["safe_to_archive"])
            with self.assertRaisesRegex(ValueError, "explicit confirmation"):
                database.archive_admin_group(group["id"], None)
            with database.connection() as connection:
                self.assertIsNone(connection.execute("SELECT archived_at FROM groups WHERE id = ?", (group["id"],)).fetchone()["archived_at"])

            archived = database.archive_admin_group(group["id"], None, confirm_used=True)
            self.assertTrue(archived["archived_at"])
            with self.assertRaisesRegex(ValueError, "Archived groups cannot receive new memberships"):
                database.update_student_memberships(student["id"], None, group["id"], None)
            with self.assertRaisesRegex(ValueError, "Archived groups cannot receive new memberships"):
                database.create_membership(group["id"], student["id"], "student", "test-new")
            with self.assertRaisesRegex(ValueError, "Archived groups cannot receive new teacher assignments"):
                database.set_teacher_assignment(teacher["id"], group["id"], "Математика", True, None)
            with self.assertRaisesRegex(ValueError, "archive or restore endpoint"):
                database.update_admin_group(group["id"], {"canonical": False}, None)
            restored = database.restore_admin_group(group["id"], None)
            self.assertIsNone(restored["archived_at"])
            with database.connection() as connection:
                row = connection.execute("SELECT group_id, identity_id, active FROM memberships WHERE group_id = ?", (group["id"],)).fetchone()
            self.assertEqual((row["group_id"], row["identity_id"], bool(row["active"])), (group["id"], student["id"], True))

    def test_archive_preview_counts_references_before_migration_037(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "pre-migration.db")
            database.initialize()
            group = database.create_group("History OGE", "exam_track", subject="История")
            with database.connection() as connection:
                connection.execute("ALTER TABLE groups DROP COLUMN archived_at")
                connection.execute("INSERT INTO schedule_entries(entry_key, lesson_date, start_time, subject, source_hash, resolved_audience) VALUES ('h1','2026-10-01','09:00','История','hash-1',?)", (json.dumps({"canonical_group_ids": [str(group["id"])]}),))
                connection.execute("INSERT INTO schedule_entries(entry_key, lesson_date, start_time, subject, source_hash, resolved_audience) VALUES ('h2','2026-10-08','09:00','История','hash-2',?)", (json.dumps({"canonical_group_ids": [str(group["id"])]}),))
            preview = database.group_archive_preview(group["id"])
            self.assertIsNotNone(preview)
            self.assertFalse(preview["archived"])
            self.assertEqual(preview["counts"]["dated_weeks"], 2)

    def test_archive_http_route_enforces_confirmation_and_supports_restore(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "archive-api.db")
            database.initialize()
            admin = database.get_or_create_user(4491201, "admin")
            with database.connection() as connection:
                connection.execute("INSERT INTO user_roles(user_id, role, source) VALUES (?, 'admin', 'test')", (admin["id"],))
            group = database.create_group("English 7", "study", subject="Английский")
            student = database.create_identity("student", "Ученик API")
            with database.connection() as connection:
                connection.execute("INSERT INTO memberships(group_id, identity_id, member_role, source, source_ref) VALUES (?, ?, 'student', 'test', 'api-roster')", (group["id"], student["id"]))
            app = FastAPI()
            app.include_router(create_school_directory_router())
            with patch.dict("os.environ", {"MY_ISLAND_SCHOOL_WORKSPACE": "1"}, clear=False), \
                    patch("backend.handlers.school_directory_api.Database", return_value=database), \
                    patch("backend.handlers.school_directory_api.school_source", return_value=database), \
                    patch("backend.handlers.school_directory_api.get_settings", return_value=SimpleNamespace()), \
                    patch.object(database, "get_admin_browser_session", return_value={"user_id": admin["id"]}):
                client = TestClient(app)
                preview = client.get(f"/api/admin/school/groups/{group['id']}/archive-preview", headers={"X-School-Admin-Session": "session"})
                self.assertEqual(preview.status_code, 200)
                self.assertTrue(preview.json()["archive_ready"])
                self.assertEqual(preview.json()["counts"]["active_memberships"], 1)
                denied = client.post(f"/api/admin/school/groups/{group['id']}/archive", json={}, headers={"X-School-Admin-Session": "session"})
                self.assertEqual(denied.status_code, 409)
                archived = client.post(f"/api/admin/school/groups/{group['id']}/archive", json={"confirm_used": True}, headers={"X-School-Admin-Session": "session"})
                self.assertEqual(archived.status_code, 200, archived.text)
                self.assertEqual(database.list_groups_admin_summary(), [])
                self.assertIsNotNone(database.list_groups_admin_summary(include_archived=True)[0]["archived_at"])
                restored = client.post(f"/api/admin/school/groups/{group['id']}/restore", headers={"X-School-Admin-Session": "session"})
                self.assertEqual(restored.status_code, 200, restored.text)
                with database.connection() as connection:
                    connection.execute("ALTER TABLE groups DROP COLUMN archived_at")
                pending = client.get(f"/api/admin/school/groups/{group['id']}/archive-preview", headers={"X-School-Admin-Session": "session"})
                self.assertEqual(pending.status_code, 200)
                self.assertFalse(pending.json()["archive_ready"])
                self.assertEqual(pending.json()["counts"]["active_memberships"], 1)


if __name__ == "__main__":
    unittest.main()
