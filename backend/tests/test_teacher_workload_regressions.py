from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from backend.services import teacher_load_analytics as analytics
from backend.services.canonical_schedule import (
    import_canonical_artifact, materialize_effective_week, resolve_published_week,
)
from backend.tests import test_canonical_schedule as fixtures


class TeacherAnalyticsTests(TestCase):
    setUp = fixtures.CanonicalScheduleRuntimeTests.setUp
    tearDown = fixtures.CanonicalScheduleRuntimeTests.tearDown
    artifact = fixtures.CanonicalScheduleRuntimeTests.artifact

    def add(self, artifact, key, day, start, end, *, metadata=None, teachers=None):
        block = deepcopy(artifact["blocks"][self.block_key])
        block.update(weekday=day, slot={"start": start, "end": end})
        entry = block["assignments"][0]
        entry.update(activity=key, source_cells=[], metadata=metadata or {})
        if teachers is not None:
            entry["teacher_ids"] = teachers
        artifact["blocks"][key] = block

    def read(self, day, finish=None, *, complete=True, **filters):
        def resolve(database, week):
            edition = resolve_published_week(database, week)
            if complete and edition.effective:
                edition.metadata["coverage"] = "complete"
            return edition

        with patch.object(analytics, "resolve_published_week", side_effect=resolve):
            return analytics.teacher_load_analytics(self.database, day, finish, **filters)

    def test_one_day_and_overlapping_lessons_retain_real_gap(self):
        artifact = self.artifact()
        self.add(artifact, "first", 0, "09:00", "10:00")
        self.add(artifact, "second", 0, "09:30", "10:30")
        self.add(artifact, "third", 0, "11:00", "11:30")
        self.add(artifact, "Tuesday", 1, "14:00", "15:30")
        del artifact["blocks"][self.block_key]
        del artifact["blocks"]["2026-09-07|1|10:00|9A"]
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-09-07")

        summary = self.read("2026-09-07")["teachers"][0]
        self.assertEqual(summary["scheduled_minutes"], 150)
        self.assertEqual(summary["occupied_minutes"], 120)
        self.assertEqual(summary["gap_minutes"], 30)
        self.assertEqual(summary["conflict_minutes"], 30)
        self.assertEqual({x["date"] for x in summary["items"]}, {"2026-09-07"})
        day = summary["day_summaries"][0]
        self.assertEqual(day["conflicts"], [{"start_time": "09:30", "end_time": "10:00"}])
        self.assertEqual(self.read("2026-09-08")["teachers"][0]["scheduled_minutes"], 90)

    def test_unknown_coverage_and_duration_never_become_authoritative_zero(self):
        artifact = self.artifact()
        artifact["blocks"][self.block_key]["slot"]["end"] = None
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-09-07")
        partial = self.read("2026-09-07", complete=False)["teachers"][0]
        self.assertEqual(partial["coverage_status"], "partial")
        self.assertEqual(partial["unknown_duration_items"], 1)
        self.assertIsNone(partial["scheduled_minutes"])
        self.assertIsNone(partial["observed_scheduled_minutes"])
        known_edition = self.read("2026-09-07")["teachers"][0]
        self.assertEqual(known_edition["duration_coverage"], "unavailable")
        self.assertIsNone(known_edition["occupied_minutes"])
        empty = self.read("2026-09-09")["teachers"][0]
        self.assertEqual(empty["occupied_minutes"], 0)

    def test_baseline_pinned_changes_include_swap_move_duration_and_cancellation(self):
        artifact = self.artifact()
        del artifact["blocks"]["2026-09-07|1|10:00|9A"]
        old_id = str(self.teacher["id"])
        other = self.database.create_identity("teacher", "Replacement")
        new_id = str(other["id"])
        original = artifact["blocks"][self.block_key]["assignments"][0]
        original["metadata"] = {"constructor_lesson_id": "swapped"}
        self.add(artifact, "removed", 0, "10:00", "10:45", metadata={"constructor_lesson_id": "cancelled"})
        import_canonical_artifact(self.database, artifact)
        moved = deepcopy(original)
        moved.update(teacher_ids=[new_id, old_id])
        moved["metadata"]["constructor_lesson_id"] = "swapped"
        # One row with two teachers is one school occurrence.
        materialize_effective_week(self.database, self.version_id, "2026-09-07", patches=[
            {"block_key": self.block_key, "change_kind": "cancelled"},
            {"block_key": "removed", "change_kind": "cancelled"},
            {"block_key": "moved-block", "change_kind": "added", "weekday": 1,
             "slot": {"start": "12:00", "end": "13:30"}, "assignments": [moved]},
        ])
        newest = deepcopy(artifact)
        newest.update(version_id="new-active-template", source_snapshot={"id": "new", "fingerprint": "new"})
        newest["blocks"] = {}
        import_canonical_artifact(self.database, newest)
        report = self.read("2026-09-07", "2026-09-08")
        lookup = {row["teacher_id"]: row for row in report["teachers"]}
        comparisons = lookup[old_id]["comparison"]
        self.assertEqual(comparisons["weeks"][0]["base_version_id"], self.version_id)
        self.assertEqual(comparisons["baseline_scheduled_minutes"], 90)
        self.assertEqual(comparisons["scheduled_minutes_delta"], 0)
        self.assertEqual(lookup[new_id]["comparison"]["scheduled_minutes_delta"], 90)
        self.assertEqual(report["school_occurrence_count"], 1)
        self.assertEqual(report["scheduled_person_minutes"], 180)
        changes = {tuple(row["kinds"]) for row in comparisons["changes"]}
        self.assertIn(("moved", "duration_changed", "teachers_changed"), changes)
        self.assertIn(("cancelled",), changes)

    def test_responsible_practice_is_visible_but_does_not_occupy_or_conflict(self):
        artifact = self.artifact()
        teacher = str(self.teacher["id"])
        self.add(artifact, "responsible", 0, "09:00", "10:00", teachers=[], metadata={
            "activity_type": "practice", "activity_format": "independent_practice", "responsible_teacher_ids": [teacher],
        })
        self.add(artifact, "remote", 0, "11:00", "11:30", metadata={
            "activity_type": "consultation", "day_plan_mode": "online", "activity_format": "individual",
        })
        self.add(artifact, "club", 0, "12:30", "13:30", metadata={"activity_type": "club"})
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-09-07")
        result = self.read("2026-09-07")["teachers"][0]
        self.assertEqual(result["responsible_minutes"], 60)
        self.assertEqual(result["optional_minutes"], 60)
        self.assertEqual(result["occupied_minutes"], 135)
        self.assertEqual(result["conflict_minutes"], 0)
        online = self.read("2026-09-07", activity_format="online")["teachers"][0]
        self.assertEqual(online["scheduled_minutes"], 30)

    def test_laboratory_segments_and_actual_replacement_have_separate_deltas(self):
        artifact = self.artifact()
        artifact["blocks"] = {}
        old_id = str(self.teacher["id"])
        replacement = str(self.database.create_identity("teacher", "Replacement")["id"])
        anchored = self.artifact()["blocks"][self.block_key]
        artifact["blocks"][self.block_key] = anchored
        anchored["assignments"][0]["metadata"] = {"constructor_lesson_id": "lesson-swap"}
        for key, start, end in (("lab-a", "10:00", "10:45"), ("lab-b", "11:15", "12:45")):
            self.add(artifact, key, 0, start, end, metadata={"activity_type": "laboratory"})
        import_canonical_artifact(self.database, artifact)
        changed = deepcopy(anchored["assignments"][0])
        changed["teacher_ids"] = [replacement]
        materialize_effective_week(self.database, self.version_id, "2026-09-07", patches=[
            {"block_key": self.block_key, "change_kind": "replaced", "assignments": [changed]},
        ])
        report = self.read("2026-09-07")
        rows = {teacher["teacher_id"]: teacher for teacher in report["teachers"]}
        self.assertEqual(rows[old_id]["comparison"]["scheduled_minutes_delta"], -45)
        self.assertEqual(rows[replacement]["comparison"]["scheduled_minutes_delta"], 45)
        self.assertEqual(rows[old_id]["comparison"]["changes"][0]["kinds"], ["teacher_replaced"])
        self.assertEqual(rows[old_id]["activity_minutes"], {"laboratory": 135})
        self.assertEqual(rows[old_id]["gap_minutes"], 30)
