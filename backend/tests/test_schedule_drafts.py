import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact, materialize_effective_week, project_student, read_canonical_template, schedule_admin_observability
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

    def test_second_week_publish_keeps_the_first_published_patch(self):
        first = save_draft(self.database, "week", "2026-09-21", expected_revision=0, payload={"changes": [self.change()]}, base_version_id="base-v1")
        first_result = publish_draft(self.database, "week", "2026-09-21", expected_revision=first["revision"])
        second_change = self.change(activity="Физика")
        second_change["block_key"] = "manual-second-block"
        second_change["assignment_index"] = 0
        second_change["lesson"]["start_time"] = "09:50"
        second_change["lesson"]["end_time"] = "10:40"
        second = save_draft(self.database, "week", "2026-09-21", expected_revision=0, payload={"changes": [second_change]}, base_version_id="base-v1")
        second_result = publish_draft(self.database, "week", "2026-09-21", expected_revision=second["revision"])
        self.assertNotEqual(first_result["published_id"], second_result["published_id"])
        with self.database.connection() as connection:
            rows = connection.execute("SELECT block_key FROM canonical_effective_blocks WHERE effective_week_id=? AND change_kind!='unchanged'", (second_result["published_id"],)).fetchall()
        self.assertEqual({row["block_key"] for row in rows}, {self.block_key, "manual-second-block"})

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
        with self.database.connection() as connection:
            connection.execute("UPDATE canonical_schedule_versions SET status='authoritative' WHERE version_id='base-v1'")
        materialize_effective_week(self.database, "base-v1", "2026-09-21")
        saved = save_draft(self.database, "template", "base-v1", expected_revision=0, payload={"changes": [self.change()]}, base_version_id="base-v1")
        result = publish_draft(self.database, "template", "base-v1", expected_revision=saved["revision"])
        self.assertEqual(result["status"], "published")
        self.assertNotEqual(result["published_id"], "base-v1")
        self.assertFalse(get_draft(self.database, "template", "base-v1")["has_changes"])
        with self.database.connection() as connection:
            row = connection.execute("SELECT parent_version_id FROM canonical_schedule_versions WHERE version_id=?", (result["published_id"],)).fetchone()
        self.assertEqual(row["parent_version_id"], "base-v1")
        readback = schedule_admin_observability(self.database, "2026-09-21", include_student_projection=False)
        self.assertEqual(readback["canonical"]["version_id"], result["published_id"])
        self.assertEqual(readback["canonical"]["status"], "authoritative")
        self.assertEqual(readback["blocks"][0]["baseline_assignments"][0]["activity"], "Русский")
        self.assertEqual(readback["blocks"][0]["effective_assignments"][0]["activity"], "Английский")

    def test_template_publish_keeps_time_of_untouched_blocks(self):
        extra = self.change(activity="Физика")
        extra["block_key"] = "manual-later-block"
        extra["assignment_index"] = 0
        extra["lesson"]["start_time"] = "09:50"
        extra["lesson"]["end_time"] = "10:40"
        saved = save_draft(self.database, "template", "base-v1", expected_revision=0, payload={"changes": [extra]}, base_version_id="base-v1")
        result = publish_draft(self.database, "template", "base-v1", expected_revision=saved["revision"])
        blocks = read_canonical_template(self.database, result["published_id"])["blocks"]
        self.assertEqual(blocks[self.block_key]["start_time"], "09:00")
        self.assertEqual(blocks[self.block_key]["end_time"], "09:45")
        self.assertEqual(blocks["manual-later-block"]["start_time"], "09:50")

    def test_template_publish_ignores_orphan_delete_for_manual_block(self):
        orphan_delete = {
            "operation": "delete", "block_key": "manual-orphan-block",
            "assignment_index": 0, "manual": True,
        }
        saved = save_draft(self.database, "template", "base-v1", expected_revision=0,
            payload={"changes": [self.change(), orphan_delete]}, base_version_id="base-v1")
        result = publish_draft(self.database, "template", "base-v1", expected_revision=saved["revision"])
        self.assertEqual(result["status"], "published")
        blocks = read_canonical_template(self.database, result["published_id"])["blocks"]
        self.assertEqual(blocks[self.block_key]["assignments"][0]["activity"], "Русский")
        self.assertNotIn("manual-orphan-block", blocks)

    def test_next_template_publish_repairs_missing_time_from_immutable_parent(self):
        original = read_canonical_template(self.database, "base-v1")["blocks"][self.block_key]
        import_canonical_artifact(self.database, {
            "schema_version": "test-v1", "version_id": "broken-v1", "parent_version_id": "base-v1",
            "source_snapshot": {"id": "broken-source", "fingerprint": "broken-source"},
            "blocks": {self.block_key: {"weekday": 0, "slot": {"start": None, "end": None}, "grade_scope": "9", "assignments": original["assignments"]}},
        }, status="approved_with_exceptions")
        self.assertIsNone(read_canonical_template(self.database, "broken-v1")["blocks"][self.block_key]["start_time"])
        extra = self.change(activity="Физика")
        extra["block_key"] = "manual-later-block"
        extra["assignment_index"] = 0
        extra["lesson"]["start_time"] = "09:50"
        extra["lesson"]["end_time"] = "10:40"
        saved = save_draft(self.database, "template", "broken-v1", expected_revision=0, payload={"changes": [extra]}, base_version_id="broken-v1")
        result = publish_draft(self.database, "template", "broken-v1", expected_revision=saved["revision"])
        repaired = read_canonical_template(self.database, result["published_id"])["blocks"][self.block_key]
        self.assertEqual((repaired["start_time"], repaired["end_time"]), ("09:00", "09:45"))

    def test_editing_one_parallel_assignment_preserves_the_other(self):
        original = read_canonical_template(self.database, "base-v1")["blocks"][self.block_key]
        parallel = {**original, "slot": {"start": original["start_time"], "end": original["end_time"]}, "assignments": [
            original["assignments"][0],
            {"activity": "Математика", "audience_kind": "canonical_groups", "canonical_group_ids": [str(self.base["id"])], "teacher_ids": [str(self.teacher["id"])], "source_cells": ["C3"]},
        ]}
        import_canonical_artifact(self.database, {
            "schema_version": "test-v1", "version_id": "parallel-v1",
            "source_snapshot": {"id": "source-2", "fingerprint": "source-fp-2"},
            "blocks": {self.block_key: parallel},
        }, status="approved_baseline")
        edited = {**self.change(activity="Физика"), "assignment_index": 0}
        saved = save_draft(self.database, "template", "parallel-v1", expected_revision=0, payload={"changes": [edited]}, base_version_id="parallel-v1")
        result = publish_draft(self.database, "template", "parallel-v1", expected_revision=saved["revision"])
        assignments = read_canonical_template(self.database, result["published_id"])["blocks"][self.block_key]["assignments"]
        self.assertEqual([item["activity"] for item in assignments], ["Физика", "Математика"])

    def test_no_lesson_can_be_saved_as_an_explicit_activity(self):
        change = self.change(activity="NO_LESSON")
        change["assignment_index"] = 0
        change["lesson"]["teacher_ids"] = []
        change["lesson"]["note"] = "Обед"
        saved = save_draft(self.database, "template", "base-v1", expected_revision=0, payload={"changes": [change]}, base_version_id="base-v1")
        result = publish_draft(self.database, "template", "base-v1", expected_revision=saved["revision"])
        assignment = read_canonical_template(self.database, result["published_id"])["blocks"][self.block_key]["assignments"][0]
        self.assertEqual(assignment["activity"], "NO_LESSON")
        self.assertEqual(assignment["metadata"]["note"], "Обед")
        materialize_effective_week(self.database, result["published_id"], "2026-09-21")
        projected = project_student(self.database, str(self.other["id"]), "2026-09-21")
        self.assertEqual(projected["items"][0]["note"], "Обед")

    def test_all_others_means_students_not_busy_in_the_same_slot(self):
        busy_rule = {"kind": "groups", "grade": "9", "group_ids": [str(self.english["id"])]}
        free_rule = {"kind": "available_slot", "grade": "9"}
        preview = audience_preview(self.database, free_rule, [busy_rule])
        self.assertEqual([item["display_name"] for item in preview["students"]], ["Student Two"])
        existing = read_canonical_template(self.database, "base-v1")["blocks"][self.block_key]
        import_canonical_artifact(self.database, {
            "schema_version": "test-v1", "version_id": "parallel-free-v1",
            "source_snapshot": {"id": "source-free", "fingerprint": "source-free"},
            "blocks": {self.block_key: {**existing, "assignments": [
                {"activity": "Английский", "audience_kind": "canonical_groups", "canonical_group_ids": [str(self.english["id"])], "teacher_ids": [str(self.teacher["id"])]},
                {"activity": "Русский", "role": "residual", "audience_kind": "remaining", "canonical_group_ids": [], "metadata": {"audience_rule": free_rule}},
            ]}},
        }, status="approved_baseline")
        materialize_effective_week(self.database, "parallel-free-v1", "2026-09-21")
        self.assertEqual(project_student(self.database, str(self.student["id"]), "2026-09-21")["items"][0]["activity"], "Английский")
        self.assertEqual(project_student(self.database, str(self.other["id"]), "2026-09-21")["items"][0]["activity"], "Русский")


if __name__ == "__main__":
    unittest.main()
