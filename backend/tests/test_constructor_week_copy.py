"""Trusted weekly copies preserve confirmation without hiding roster changes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact
from backend.services.schedule_experiments import (
    _audience_fingerprint, create_experiment, create_experiment_week,
    experiment_audience_preview, get_experiment, save_experiment,
)


class ConstructorWeekCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "synthetic.sqlite3")
        self.db.initialize()
        self.group = self.db.create_group("9A", "class")
        self.student = self.db.create_identity("student", "Synthetic", "9A")
        self.db.create_membership(self.group["id"], self.student["id"], "student", "synthetic")
        import_canonical_artifact(self.db, {"version_id": "synthetic-base", "blocks": {},
            "source_snapshot": {"id": "synthetic", "fingerprint": "synthetic"}})
        draft = create_experiment(self.db, name="Source", source="canonical", payload={
            "reference_date": "2026-10-19", "days": [0], "classes": [str(self.group["id"])],
            "slots": [{"start": "09:00", "end": "09:45"}],
            "lessons": [{"id": "lesson", "day": 0, "period": 0, "subject": "Math",
                         "classId": str(self.group["id"]),
                         "audience": {"kind": "classes", "class_ids": [str(self.group["id"])]}}]})
        self.source = save_experiment(self.db, draft["id"], expected_revision=1, payload=draft["payload"])

    def tearDown(self):
        self.temp.cleanup()

    def week(self):
        return create_experiment_week(self.db, self.source["id"], week_start="2026-10-26",
                                      expected_revision=self.source["revision"])

    def test_valid_snapshot_rebound_base_inherited_and_source_untouched(self):
        original = copy.deepcopy(self.source)
        week = self.week()
        old_snapshot = self.source["payload"]["lessons"][0]["audience_snapshot"]
        lesson = week["payload"]["lessons"][0]
        self.assertEqual(week["base_version_id"], "synthetic-base")
        self.assertEqual(lesson["audience_snapshot"]["student_ids"], old_snapshot["student_ids"])
        self.assertEqual(lesson["audience_snapshot"]["resolved_at"], old_snapshot["resolved_at"])
        self.assertNotEqual(lesson["audience_snapshot"]["fingerprint"], old_snapshot["fingerprint"])
        self.assertEqual(lesson["audience_snapshot"]["fingerprint"], _audience_fingerprint(lesson, week["payload"]))
        result = experiment_audience_preview(self.db, week["payload"])["lessons"]["lesson"]
        self.assertFalse(result["needs_recalculation"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(get_experiment(self.db, self.source["id"]), original)

    def test_invalid_source_snapshot_not_silently_repaired(self):
        payload = copy.deepcopy(self.source["payload"])
        payload["lessons"][0]["audience_snapshot"]["fingerprint"] = "invalid-old-fingerprint"
        with self.db.connection() as connection:
            connection.execute("UPDATE schedule_experiment_drafts SET payload=? WHERE id=?",
                               (json.dumps(payload), self.source["id"]))
        week = self.week()
        lesson = week["payload"]["lessons"][0]
        self.assertEqual(lesson["audience_snapshot"]["fingerprint"], "invalid-old-fingerprint")
        result = experiment_audience_preview(self.db, week["payload"])["lessons"]["lesson"]
        self.assertTrue(result["needs_recalculation"])

    def test_target_week_membership_change_keeps_frozen_roster_and_requires_review(self):
        with self.db.connection() as connection:
            connection.execute("UPDATE memberships SET valid_until='2026-10-23'")
        week = self.week()
        snapshot_ids = week["payload"]["lessons"][0]["audience_snapshot"]["student_ids"]
        self.assertEqual(snapshot_ids, [str(self.student["id"])])
        result = experiment_audience_preview(self.db, week["payload"])["lessons"]["lesson"]
        self.assertTrue(result["needs_recalculation"])
        self.assertEqual(result["student_ids"], snapshot_ids)
        self.assertNotEqual(result["status"], "ok")

    def test_public_import_payload_cannot_select_canonical_base(self):
        imported = create_experiment(self.db, name="Untrusted import", source="import",
            payload={**self.source["payload"], "base_version_id": "synthetic-base"})
        self.assertIsNone(imported["base_version_id"])


if __name__ == "__main__":
    unittest.main()
