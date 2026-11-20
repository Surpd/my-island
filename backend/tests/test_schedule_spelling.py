import hashlib
import json
import unittest

from backend.scripts.preview_schedule_spelling import preview
from backend.services.constructor_publication import _semantic
from backend.services.schedule_experiments import lesson_is_online
from backend.services.weekly_schedule_ingestion import assignment_signature
from backend.services.schedule_rooms import room_key, room_label


class ScheduleSpellingTests(unittest.TestCase):
    def test_publication_compares_resources_and_preserves_assignments(self):
        def block(subject, room, group="base", teacher="teacher"):
            return _semantic({"assignments": [{"activity": subject, "teacher_ids": [teacher],
                              "canonical_group_ids": [group], "metadata": {"room": room}}]})
        for room in ("15", "каб.15", "каб. 15", "Кабинет 15"):
            self.assertEqual(room_label(room), "каб. 15")
            self.assertEqual(room_key(room), "15")
            for subject in ("Мат", "Матем", "Математика"):
                self.assertEqual(block(subject, room), block("Математика", "15"))
        for other in (block("Математика ОГЭ", "15"), block("Мат", "15", "advanced"),
                      block("Мат", "15", teacher="other")):
            self.assertNotEqual(block("Мат", "15"), other)
        self.assertNotEqual(block("История", "зал"), block("История искусства", "зал"))
        self.assertEqual(room_label("музей"), "музей")

    def test_weekly_signature_keeps_course_for_same_subject_and_teacher(self):
        def signature(subject, track):
            return assignment_signature({"assignments": [{"activity": subject, "teacher_ids": ["same"],
                "canonical_group_ids": ["same"], "metadata": {"exam_track": track}}]})
        self.assertEqual(signature("Рус", "ОГЭ"), signature("Русский язык", "ОГЭ"))
        self.assertNotEqual(signature("Рус", "ОГЭ"), signature("Русский язык", "ЕГЭ"))

    def test_day_mode_and_legacy_room(self):
        self.assertTrue(lesson_is_online({"format": "offline", "room": "15"}, {"online"}))
        self.assertTrue(lesson_is_online({"format": "offline", "room": "Онлайн"}, set()))
        self.assertFalse(lesson_is_online({"room": "15"}, set()))

    def test_preview_requires_backup_and_revision(self):
        raw = json.dumps({"id": "a", "revision": 3, "payload": {"lessons": [
            {"id": "one", "subject": "Рус", "teacherId": "t", "room": "15"}]}}).encode()
        digest = hashlib.sha256(raw).hexdigest()
        plan = preview(raw, draft_id="a", revision=3, checksum=digest)
        self.assertEqual([e["after"] for e in plan["edits"]], ["каб. 15", "Русский язык"])
        for rev, checksum in [(2, digest), (3, "wrong")]:
            with self.assertRaises(ValueError):
                preview(raw, draft_id="a", revision=rev, checksum=checksum)
