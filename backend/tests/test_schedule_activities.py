import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.database import Database
from backend.services.schedule_activities import (
    ActivityConflict, list_activities, normalize_activity_name, save_activity,
)
from backend.services.schedule_experiments import (
    _lesson_audience, create_experiment, create_experiment_week, experiment_audience_preview,
    get_experiment, save_experiment,
)


class ScheduleActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "activities.db")
        self.db.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_catalog_aliases_and_japanese_chess_are_distinct(self):
        items = list_activities(self.db)
        self.assertEqual(len(items), 10)
        self.assertEqual(normalize_activity_name("Game Design"), normalize_activity_name("Геймдизайн"))
        chess = next(item for item in items if item["id"] == "chess")
        self.assertIn("японские шахматы", chess["aliases"])
        self.assertIn("Анимация", {item["name"] for item in items})
        self.assertIn("Вокал", {item["name"] for item in items})
        self.assertIn("Французский язык", {item["name"] for item in items})

    def test_catalog_create_update_archive_and_optimistic_conflict(self):
        created = save_activity(self.db, {"name": "Клуб астрономии", "type": "club", "aliases": ["астрономия"]})
        edited = save_activity(self.db, {"name": "Астрономия", "type": "club", "aliases": [], "archive": True},
                               activity_id=created["id"], expected_revision=created["revision"])
        self.assertTrue(edited["archived"])
        self.assertEqual(edited["revision"], 2)
        with self.assertRaises(ActivityConflict):
            save_activity(self.db, {"name": "Устаревшее", "type": "club"},
                          activity_id=created["id"], expected_revision=1)
        self.assertNotIn(created["id"], {item["id"] for item in list_activities(self.db)})

    def test_ui_status_change_wins_over_previous_archived_projection(self):
        item = save_activity(self.db, {'name': 'Тестовый курс', 'type': 'elective'})
        archived = save_activity(self.db, {**item, 'status': 'archived'}, activity_id=item['id'], expected_revision=1)
        self.assertTrue(archived['archived'])
        renamed = save_activity(self.db, {'name': 'Переименованный курс', 'type': 'elective'}, activity_id=item['id'], expected_revision=2)
        self.assertTrue(renamed['archived'])

    def test_optional_activity_metadata_survives_week_copy_and_snapshot_name(self):
        catalog = next(item for item in list_activities(self.db, include_archived=True) if item["id"] == "animation")
        payload = {"days": [0], "slots": [{"start": "09:00", "end": "09:45"}], "classes": [], "lessons": [{
            "id": "club-1", "day": 0, "period": 0, "subject": "Анимация",
            "activityType": "club", "activityCatalogId": catalog["id"], "activityNameSnapshot": "Анимация",
            "eligibleGrades": [7, 8], "confirmedParticipantIds": ["student-a"],
        }]}
        draft = create_experiment(self.db, name="Кружки", source="import", payload=payload)
        # Renames in the catalog do not rewrite a saved historical display snapshot.
        save_activity(self.db, {"name": "Анимация 2", "type": "club"},
                      activity_id=catalog["id"], expected_revision=catalog["revision"])
        week = create_experiment_week(self.db, draft["id"], week_start="2026-10-19", expected_revision=1)
        copied = get_experiment(self.db, week["id"])["payload"]
        self.assertEqual(copied["lessons"][0]["activityNameSnapshot"], "Анимация")
        self.assertEqual(copied["lessons"][0]["confirmedParticipantIds"], ["student-a"])
        self.assertEqual(copied["source_template"], {"id": draft["id"], "revision": 1})
        audience = _lesson_audience(copied["lessons"][0])
        self.assertEqual(audience["student_ids"], [])

    def test_club_eligibility_does_not_become_students(self):
        audience = _lesson_audience({"activityType": "club", "eligibleGrades": [7]})
        self.assertEqual(audience["kind"], "students")
        self.assertEqual(audience["student_ids"], [])

    def test_confirmed_legacy_clubs_are_persisted_without_losing_originals(self):
        payload = {'days': [0], 'classes': [], 'slots': [], 'lessons': [
            {'id': 'legacy', 'subject': '⚪АЙКИДО', 'classIds': ['old-class']},
            {'id': 'digital', 'subject': 'Программирование', 'activityType': 'elective',
             'activityNameSnapshot': 'Программирование', 'electiveDirection': 'digital'}]}
        draft = create_experiment(self.db, name='Импорт', source='import', payload=payload)
        club, course = draft['payload']['lessons']
        self.assertEqual((club['id'], club['activityType'], club['subject']), ('legacy', 'club', 'Айкидо'))
        self.assertEqual(club['legacySubject'], '⚪АЙКИДО')
        self.assertEqual(club['displayClassIds'], ['old-class'])
        self.assertEqual(_lesson_audience(club)['student_ids'], [])
        self.assertEqual(course['activityType'], 'elective')
        self.assertNotIn('activityCatalogId', course)

    def test_school_local_catalog_uses_only_its_draft_store(self):
        from backend.services.school_experiment_workspace import school_drafts
        with patch('backend.services.school_experiment_workspace.SCHOOL_DB', Path(self.tmp.name) / 'school-local.sqlite3'):
            store = school_drafts()
            self.assertEqual(len(list_activities(store)), 10)
            item = save_activity(store, {'name': 'Тестовая студия', 'type': 'club'})
            self.assertTrue(item['id'])
            with store.connection() as c:
                self.assertIsNotNone(c.execute("SELECT name FROM sqlite_master WHERE name='schedule_sheet_exports'").fetchone())

    def test_club_class_targets_and_historical_roster_do_not_occupy_all_others(self):
        class_group = self.db.create_group("7A", "class")
        students = [self.db.create_identity("student", f"Pupil {index}") for index in range(2)]
        for student in students:
            self.db.create_membership(class_group["id"], student["id"], "student", "test")
        class_id = str(class_group["id"])
        payload = {"reference_date": "2026-10-19", "slots": [{"start": "09:00", "end": "09:45"}], "lessons": [
            {"id": "club", "day": 0, "period": 0, "activityType": "club", "eligibleGrades": [7],
             "classId": class_id, "classIds": [class_id], "audience": {"kind": "classes", "class_ids": [class_id]},
             "audience_snapshot": {"student_ids": [str(s["id"]) for s in students]}},
            {"id": "residual", "day": 0, "period": 0,
             "audience": {"kind": "available_slot", "class_ids": [class_id]}},
        ]}
        result = experiment_audience_preview(self.db, payload)["lessons"]
        self.assertEqual(result["club"]["student_ids"], [])
        self.assertEqual(set(result["residual"]["student_ids"]), {str(s["id"]) for s in students})


if __name__ == "__main__":
    unittest.main()
