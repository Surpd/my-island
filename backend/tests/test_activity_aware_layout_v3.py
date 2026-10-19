import unittest

from backend.services.schedule_sheet_export import build_sheet_plan, freeze_week, sheets_requests
from backend.services.schedule_sheet_layout_v3 import activity_semantics


class ActivityAwareLayoutV3Tests(unittest.TestCase):
    def test_explicit_type_wins_and_catalog_metadata_is_carried(self):
        semantics = activity_semantics({
            "subject": "Программирование", "activityType": "elective",
            "activityCatalogId": "catalog-1", "activityNameSnapshot": "Цифровой трек",
            "eligibleGrades": [5, 6], "confirmedParticipantIds": ["p1"],
            "electiveDirection": "digital",
        })
        self.assertEqual(semantics["section"], "schedule")
        self.assertEqual(semantics["display_name"], "Цифровой трек")
        self.assertEqual(semantics["activity_catalog_id"], "catalog-1")
        self.assertEqual(semantics["eligible_grades"], "5,6")
        self.assertEqual(semantics["elective_direction"], "digital")
        self.assertEqual(activity_semantics({"subject": "Программирование", "activityType": "lesson"})["section"], "schedule")

    def test_empty_class_ids_club_uses_eligible_grades_without_assignment(self):
        lesson = {"id": "club-1", "day": 0, "start": "15:00", "end": "15:45",
                  "classIds": [], "groupIds": [], "teacherIds": [], "subject": "Программирование",
                  "activityType": "club", "activityCatalogId": "catalog-1",
                  "activityNameSnapshot": "Программирование", "eligibleGrades": [5, 6, 7],
                  "confirmedParticipantIds": ["private-person"]}
        source = {"source": {"draft_id": "template", "revision": 1},
                  "groups": [{"id": f"c{grade}", "name": str(grade), "group_type": "class"}
                             for grade in (5, 6, 7)],
                  "teachers": [],
                  "payload": {"days": [0], "slots": [], "classes": ["c5", "c6", "c7"],
                              "lessons": [lesson]}}
        plan = build_sheet_plan(freeze_week(source, "2026-10-19"), title="week", sheet_id=1,
                                mapping_sheet_id=2, layout="final-compact")
        resolved = plan["presentation_context"]["resolved_audiences"]["club-1"]
        mapping = plan["mapping"][0]
        card = next(cell for cell in plan["cells"] if cell["note"].startswith("MY_ISLAND:"))
        self.assertEqual(resolved["grade_numbers"], [5, 6, 7])
        self.assertEqual(resolved["class_ids"], [])
        self.assertEqual(mapping["source_class_ids"], [])
        self.assertEqual(mapping["activity_catalog_id"], "catalog-1")
        self.assertNotIn("confirmed_participant_ids", mapping)
        self.assertNotIn("private-person", str(sheets_requests(plan)))
        self.assertEqual(card["text"].split(" · ", 1)[1], "Программирование")
        self.assertIn("Кружки", [cell["text"] for cell in plan["cells"]])

    def test_known_legacy_catalog_and_french_marker_are_separated(self):
        self.assertEqual(activity_semantics({"subject": "⚪ ВОКАЛ"})["section"], "clubs")
        self.assertEqual(activity_semantics({"subject": "⚪ Французский"})["section"], "activity_review")

    def test_final_compact_keeps_full_elective_and_nonstandard_titles(self):
        source = {"source": {"draft_id": "template", "revision": 3},
                  "groups": [{"id": "c5", "name": "5", "group_type": "class"},
                             {"id": "c6", "name": "6", "group_type": "class"}],
                  "teachers": [{"id": "t", "display_name": "Юлия"}],
                  "payload": {"classes": ["c5", "c6"], "days": [0, 1, 2, 3, 4, 5],
                              "slots": [{"start": "09:00", "end": "09:45"}], "lessons": []}}
        titles = ["Программирование", "Компьютерная графика", "ШУМ · медиамастерская · группа1",
                  "Лаборатория · Прошлое · Настоящее", "История искусства", "Графический дизайн"]
        expected_titles = ["Программирование", "Компьютерная графика", "ШУМ · Медиамастерская · группа1",
                           "Лаборатория · Прошлое · Настоящее", "История искусства", "Графический дизайн"]
        source["payload"]["lessons"] = [
            {"id": f"e{i}", "classIds": ["c5"], "day": i, "start": "09:00", "end": "09:45",
             "subject": title, "teacherIds": ["t"], "groupIds": [], "room": "17",
             "activityType": "elective", "activityCatalogId": f"catalog-{i}",
             "activityNameSnapshot": title, "electiveDirection": "digital" if i == 1 else "general",
             "eligibleGrades": [5], "confirmedParticipantIds": ["private-person"]}
            for i, title in enumerate(titles)
        ]
        plan = build_sheet_plan(freeze_week(source, "2026-10-19"), title="final", sheet_id=401,
                                mapping_sheet_id=402, layout="final-compact")
        cards = {item["lesson_id"]: next(cell for cell in plan["cells"]
                   if cell["note"].startswith("MY_ISLAND:") and
                   f'"lesson_id":"{item["lesson_id"]}"' in cell["note"])
                 for item in plan["mapping"]}
        for i, title in enumerate(expected_titles):
            self.assertIn(title, cards[f"e{i}"]["text"])
        self.assertIn("Цифровой трек", cards["e1"]["text"])
        self.assertTrue(all(item["section"] == "schedule" for item in plan["mapping"]))
        self.assertTrue(all("confirmed_participant_ids" not in item for item in plan["mapping"]))
        self.assertTrue(sheets_requests(plan))

    def test_classless_elective_and_event_get_visual_scope_without_class_assignment(self):
        source = {"source": {"draft_id": "template", "revision": 3},
                  "groups": [{"id": "c5", "name": "5", "group_type": "class"},
                             {"id": "c6", "name": "6", "group_type": "class"}],
                  "teachers": [],
                  "payload": {"classes": ["c5", "c6"], "days": [0], "slots": [],
                              "lessons": [
                                  {"id": "elective", "classIds": [], "day": 0, "start": "09:00", "end": "09:45",
                                   "subject": "Компьютерная графика", "teacherIds": [], "groupIds": [],
                                   "activityType": "elective", "eligibleGrades": [5]},
                                  {"id": "event", "classIds": [], "day": 0, "start": "10:00", "end": "10:45",
                                   "subject": "Праздник", "teacherIds": [], "groupIds": [], "activityType": "event"}]}}
        plan = build_sheet_plan(freeze_week(source, "2026-10-19"), title="week", sheet_id=3,
                                mapping_sheet_id=4, layout="final-compact")
        mappings = {item["lesson_id"]: item for item in plan["mapping"]}
        audiences = plan["presentation_context"]["resolved_audiences"]
        self.assertEqual(mappings["elective"]["grade_numbers"], [5])
        self.assertEqual(mappings["event"]["grade_numbers"], [5, 6])
        self.assertEqual(mappings["event"]["source_class_ids"], [])
        self.assertEqual(audiences["event"]["class_ids"], [])


if __name__ == "__main__":
    unittest.main()
