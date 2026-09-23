import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact, project_student
from backend.services.schedule_drafts import (
    DraftConflict,
    audience_preview,
    get_draft,
    merge_import_changes,
    preview_changes,
    publish_draft,
    save_draft,
)


class ScheduleDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "drafts.db")
        self.database.initialize()
        self.student = self.database.create_identity("student", "Student One", "9A")
        self.other = self.database.create_identity("student", "Student Two", "9A")
        self.teacher = self.database.create_identity("teacher", "Teacher One")
        self.base = self.database.create_group("9A", "class")
        self.english = self.database.create_group("9A · English 6", "instructional")
        self.database.create_membership(self.base["id"], self.student["id"], "student", "test")
        self.database.create_membership(self.base["id"], self.other["id"], "student", "test")
        self.database.create_membership(self.english["id"], self.student["id"], "student", "test")
        self.block_key = "2026-09-21|0|09:00|9"
        import_canonical_artifact(self.database, {
            "schema_version": "test-v1", "version_id": "base-v1",
            "source_snapshot": {"id": "source-1", "fingerprint": "source-fp-1"},
            "blocks": {self.block_key: {
                "weekday": 0, "slot": {"start": "09:00", "end": "09:45"}, "grade_scope": "9",
                "status": "resolved", "mode": "SINGLE", "derived_from": {"source_cells": ["B3"]},
                "assignments": [{"activity": "Английский", "audience": {"type": "canonical_groups", "canonical_group_ids": [str(self.english["id"])]}, "teacher_ids": [str(self.teacher["id"])], "source_cells": ["B3"]}],
            }},
        }, status="approved_baseline")

    def tearDown(self):
        self.temp_dir.cleanup()

    def change(self, *, activity="Русский", fingerprint="cell-v1"):
        return {"operation": "upsert", "block_key": self.block_key, "manual": True, "audience_changed": True, "lesson": {
            "activity": activity, "weekday": 0, "start_time": "09:00", "end_time": "09:45", "grade": "9",
            "teacher_ids": [str(self.teacher["id"])], "room": "401", "source_cells": ["B3"],
            "source_identity": {"cells": ["B3"], "fingerprint": fingerprint},
            "audience": {"kind": "remaining", "grade": "9", "partition_group_ids": [str(self.english["id"])], "partition_dimension": ""},
        }}

    def test_autosave_recovery_scope_isolation_and_revision_conflict(self):
        saved = save_draft(self.database, "week", "2026-09-21", expected_revision=0, payload={"changes": [self.change()]}, base_version_id="base-v1")
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(get_draft(self.database, "week", "2026-09-21")["payload"]["changes"][0]["lesson"]["activity"], "Русский")
        self.assertFalse(get_draft(self.database, "week", "2026-09-28")["has_changes"])
        self.assertFalse(get_draft(self.database, "template", "base-v1")["has_changes"])
        updated = save_draft(self.database, "week", "2026-09-21", expected_revision=1, payload={"changes": [self.change(activity="Литература")]})
        self.assertEqual(updated["revision"], 2)
        with self.assertRaises(DraftConflict):
            save_draft(self.database, "week", "2026-09-21", expected_revision=1, payload={"changes": [self.change(activity="История")]})
        self.assertEqual(get_draft(self.database, "week", "2026-09-21")["payload"]["changes"][0]["lesson"]["activity"], "Литература")

    def test_publish_creates_immutable_effective_revision_and_clears_draft(self):
        saved = save_draft(self.database, "week", "2026-09-21", expected_revision=0, payload={"changes": [self.change()]}, base_version_id="base-v1")
        result = publish_draft(self.database, "week", "2026-09-21", expected_revision=saved["revision"])
        self.assertEqual(result["status"], "published")
        self.assertFalse(get_draft(self.database, "week", "2026-09-21")["has_changes"])
        with self.database.connection() as connection:
            rows = connection.execute("SELECT effective_week_id,overlay_fingerprint FROM canonical_effective_weeks WHERE week_start='2026-09-21'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertTrue(str(rows[0]["overlay_fingerprint"]).startswith("manual:"))
        projected = project_student(self.database, str(self.other["id"]), "2026-09-21")
        self.assertEqual(projected["items"][0]["activity"], "Русский")
        self.assertEqual(project_student(self.database, str(self.student["id"]), "2026-09-21")["items"][0]["state"], "NO_LESSON")

    def test_manual_audience_survives_import_and_material_change_requires_review(self):
        saved = save_draft(self.database, "week", "2026-09-21", expected_revision=0, payload={"changes": [self.change()]}, base_version_id="base-v1")
        same = merge_import_changes(self.database, "2026-09-21", expected_revision=saved["revision"], proposed_changes=[self.change(activity="Русский")], source_context={"fingerprint": "sheet-v1"})
        self.assertFalse(same["payload"]["changes"][0].get("review_required", False))
        changed = merge_import_changes(self.database, "2026-09-21", expected_revision=same["revision"], proposed_changes=[self.change(activity="Литература", fingerprint="cell-v2")], source_context={"fingerprint": "sheet-v2"})
        item = changed["payload"]["changes"][0]
        self.assertTrue(item["review_required"])
        self.assertEqual(item["lesson"]["audience"]["kind"], "remaining")

    def test_membership_change_immediately_affects_audience_preview(self):
        rule = {"kind": "remaining", "grade": "9", "partition_group_ids": [str(self.english["id"])]}
        first = audience_preview(self.database, rule)
        self.assertEqual([item["display_name"] for item in first["students"]], ["Student Two"])
        self.database.create_membership(self.english["id"], self.other["id"], "student", "test")
        self.assertEqual(audience_preview(self.database, rule)["count"], 0)

    def test_google_preview_is_translated_to_draft_without_publishing(self):
        changes = preview_changes({
            "source_fingerprint": "sheet-v1",
            "overlay_patches": [{
                "block_key": self.block_key, "change_kind": "replaced", "weekday": 0,
                "slot": {"start": "09:00", "end": "09:45"}, "grade_scope": "9",
                "weekly_source_cells": ["B3"], "weekly_raw_text": {"B3": "Русский"},
                "assignments": [{
                    "activity": "Русский", "teacher_ids": [str(self.teacher["id"])],
                    "canonical_group_ids": [str(self.english["id"])],
                    "metadata": {"room": "401", "teachers": ["Teacher One"]},
                }],
            }],
        })
        self.assertEqual(len(changes), 1)
        self.assertFalse(changes[0]["manual"])
        self.assertEqual(changes[0]["lesson"]["audience"]["kind"], "groups")
        self.assertIn("semantic_fingerprint", changes[0]["lesson"]["source_identity"])
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM canonical_effective_weeks").fetchone()[0], 0)

    def test_template_publish_creates_new_immutable_canonical_version(self):
        saved = save_draft(self.database, "template", "base-v1", expected_revision=0, payload={"changes": [self.change()]}, base_version_id="base-v1")
        result = publish_draft(self.database, "template", "base-v1", expected_revision=saved["revision"])
        self.assertEqual(result["status"], "published")
        self.assertNotEqual(result["published_id"], "base-v1")
        self.assertFalse(get_draft(self.database, "template", "base-v1")["has_changes"])
        with self.database.connection() as connection:
            row = connection.execute("SELECT parent_version_id FROM canonical_schedule_versions WHERE version_id=?", (result["published_id"],)).fetchone()
        self.assertEqual(row["parent_version_id"], "base-v1")


if __name__ == "__main__":
    unittest.main()
