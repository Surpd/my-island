from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact, materialize_effective_week
from backend.services.schedule_experiments import _canonical_time, _clean_payload, experiment_audience_preview
from backend.services.school_experiment_workspace import (
    ReadOnlySchoolDatabase, _collapse_repeated_group_lessons, _primary_subject_teacher_defaults, cached_audience_snapshot,
    _safe_lessons, _separate_legacy_no_lesson, school_class_label, school_directory,
    school_teacher_schedule,
)


class SchoolExperimentWorkspaceTests(unittest.TestCase):
    def test_source_guard_rejects_another_project(self):
        with self.assertRaises(ValueError):
            ReadOnlySchoolDatabase("postgresql://postgres.otherproject:secret@aws-1-eu-west-1.pooler.supabase.com:6543/postgres")

    def test_postgres_time_does_not_duplicate_bell_slot(self):
        self.assertEqual(_canonical_time("09:00:00"), "09:00")
        self.assertEqual(_canonical_time("09:00"), "09:00")

    def test_cross_class_duplicate_preserves_source_and_audience(self):
        lessons = [
            {"id": "a", "day": 0, "start": "09:55", "end": "10:40", "classIds": ["c5"],
             "groupIds": ["c5", "english"], "teacherIds": ["teacher"],
             "canonical": {"block_key": "first", "assignment_index": 0, "assignment": {"source_cells": ["B5"]}}},
            {"id": "b", "day": 0, "start": "09:55", "end": "10:40", "classIds": ["c6"],
             "groupIds": ["c6", "english"], "teacherIds": ["teacher"],
             "canonical": {"block_key": "second", "assignment_index": 0, "assignment": {"source_cells": ["D5"]}}},
        ]
        payload = {"classes": ["c5", "c6"], "lessons": lessons}
        self.assertEqual(_collapse_repeated_group_lessons(payload), 1)
        self.assertEqual(len(payload["lessons"]), 1)
        self.assertEqual(payload["lessons"][0]["classIds"], ["c5", "c6"])
        self.assertEqual(payload["lessons"][0]["source_duplicates"][0]["source_cells"], ["D5"])

    def test_school_draft_retains_review_without_student_roster(self):
        payload = {"days": [0], "slots": [{"start": "09:00", "end": "09:45"}],
                   "classes": ["class"], "draft_kind": "school", "lessons": [
                       {"id": "one", "canonical": {"block_key": "b", "assignment_index": 0,
                           "assignment": {"source_cells": ["B4"], "metadata": {"audience_rule": {
                               "kind": "groups", "include_student_ids": ["private-student-id"]}}},
                           "block": {"source_provenance": {"raw_text": "private-student-name"}}}}],
                   "school_source": {"sheet_id": "638874869"},
                   "import_review": {"changed_cell_count": 2},
                   "transfer_result": {"transferred": 1}}
        cleaned = _clean_payload(_safe_lessons(payload))
        self.assertEqual(cleaned["draft_kind"], "school")
        self.assertEqual(cleaned["import_review"]["changed_cell_count"], 2)
        self.assertNotIn("private-student-id", str(cleaned))
        self.assertNotIn("private-student-name", str(cleaned))
        self.assertTrue(cleaned["lessons"][0]["needsClarification"])

    def test_legacy_no_lesson_is_retained_for_review_outside_grid(self):
        payload = {"lessons": [
            {"id": "old", "subject": "NO_LESSON", "day": 0, "start": "09:00", "end": "09:45",
             "classIds": ["class"], "canonical": {"block_key": "block", "assignment_index": 1,
             "assignment": {"source_cells": ["B4"]}}},
            {"id": "real", "subject": "Математика"},
            {"id": "explicit", "subject": "Нет урока", "specialMark": "no_lesson"},
        ]}
        review = _separate_legacy_no_lesson(payload)
        self.assertEqual([lesson["id"] for lesson in payload["lessons"]], ["real", "explicit"])
        self.assertEqual(review[0]["source_cells"], ["B4"])
        self.assertEqual(review[0]["canonical_block_key"], "block")

    def test_confirmed_ninth_class_alias_is_scoped_to_class_labels(self):
        self.assertEqual(school_class_label("9-Ан"), "9-а")
        self.assertEqual(school_class_label("9-Д"), "9-д")
        self.assertEqual(school_class_label("Math A"), "math a")

    def test_group_default_teacher_requires_unique_primary_subject_assignment(self):
        groups = [
            {"id": "math7", "subject": "Математика"},
            {"id": "ambiguous", "subject": "Математика"},
            {"id": "no-subject", "subject": None},
        ]
        assignments = [
            {"group_id": "math7", "subject": "Математика", "teacher_identity_id": "math-teacher"},
            {"group_id": "math7", "subject": "Английский", "teacher_identity_id": "english-teacher"},
            {"group_id": "ambiguous", "subject": "Математика", "teacher_identity_id": "teacher-a"},
            {"group_id": "ambiguous", "subject": "Математика", "teacher_identity_id": "teacher-b"},
            {"group_id": "no-subject", "subject": "Математика", "teacher_identity_id": "teacher-a"},
        ]

        self.assertEqual(_primary_subject_teacher_defaults(groups, assignments), {"math7": "math-teacher"})

    def test_school_directory_uses_effective_memberships(self):
        with TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "directory.sqlite3")
            database.initialize()
            with database.connection() as connection:
                student_id = database.execute(connection, "INSERT INTO identities(kind,display_name,status) VALUES ('student','Synthetic Student','active') RETURNING id").fetchone()["id"]
                group_id = database.execute(connection, "INSERT INTO groups(name,group_type,canonical) VALUES ('Synthetic Group','instructional',1) RETURNING id").fetchone()["id"]
                database.execute(connection, "INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (group_id, student_id))
            before = school_directory(database)
            self.assertIn(str(group_id), before["students"][0]["groups"])
            with database.connection() as connection:
                database.execute(connection, "INSERT INTO membership_overrides(identity_id,group_id,member_role,action,active) VALUES (?,?,'student','exclude',1)", (student_id, group_id))
            after = school_directory(database)
            self.assertNotIn(str(group_id), after["students"][0]["groups"])

            class_id = database.create_group("9-А", "class")["id"]
            included_student = database.create_identity("student", "Included by override")
            with database.connection() as connection:
                database.execute(connection, "INSERT INTO membership_overrides(identity_id,group_id,member_role,action,active) VALUES (?,?,'student','include',1)", (included_student["id"], class_id))
            school_directory(database)
            snapshot = cached_audience_snapshot(date.today(), database)
            self.assertIsNotNone(snapshot)
            preview = experiment_audience_preview(database, {
                "reference_date": date.today().isoformat(),
                "slots": [{"start": "09:00", "end": "09:45"}],
                "lessons": [{"id": "remainder", "day": 0, "period": 0, "grade_scope": "9",
                             "audience": {"kind": "available_slot", "class_ids": []}}],
            }, snapshot=snapshot)["lessons"]["remainder"]
            self.assertEqual(preview["count"], 1)
            self.assertEqual(preview["student_ids"], [str(included_student["id"])])

    def test_teacher_schedule_lists_dated_weeks_from_other_live_versions(self):
        with TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "teacher-schedule.sqlite3")
            database.initialize()
            teacher = database.create_identity("teacher", "Synthetic Teacher")
            teacher_id = str(teacher["id"])
            artifact = {
                "schema_version": "canonical-schedule-bootstrap-v1",
                "version_id": "teacher-schedule-version-1",
                "status": "resolved",
                "source_snapshot": {"id": "snapshot-1", "fingerprint": "fingerprint-1"},
                "blocks": {"monday": {
                    "weekday": 0, "slot": {"start": "09:00", "end": "09:45"},
                    "grade_scope": "9", "status": "resolved", "mode": "SINGLE",
                    "assignments": [{"activity": "Математика", "teacher_ids": [teacher_id],
                                     "audience": {"type": "canonical_groups", "canonical_group_ids": []}}],
                }},
            }
            import_canonical_artifact(database, artifact, status="approved_baseline",
                                      approved_at="2026-09-01T00:00:00+00:00")
            materialize_effective_week(database, artifact["version_id"], "2026-09-07")

            newer = {**artifact, "version_id": "teacher-schedule-version-2",
                     "source_snapshot": {"id": "snapshot-2", "fingerprint": "fingerprint-2"}}
            import_canonical_artifact(database, newer, status="approved_with_exceptions",
                                      approved_at="2026-10-01T00:00:00+00:00")

            schedule = school_teacher_schedule(database, teacher_id)
            dated_week = school_teacher_schedule(database, teacher_id, "2026-09-07")

            self.assertEqual(schedule["source"], "canonical_template")
            self.assertEqual(schedule["available_weeks"], ["2026-09-07"])
            self.assertEqual(dated_week["source"], "published_week")
            self.assertEqual(len(dated_week["items"]), 1)
            self.assertEqual(dated_week["items"][0]["activity"], "Математика")


if __name__ == "__main__":
    unittest.main()
