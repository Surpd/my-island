import unittest

from backend.services.schedule_rooms import display_room, room_key


class ScheduleRoomTests(unittest.TestCase):
    def test_numbered_cabinet_aliases_share_display_and_comparison_key(self):
        aliases = ("каб.16", "каб 16", "Кабинет 16", "16")
        self.assertEqual({display_room(value) for value in aliases}, {"16"})
        self.assertEqual({room_key(value) for value in aliases}, {"16"})
        self.assertEqual(display_room("каб 16-А"), "16-А")
        self.assertEqual(room_key("каб 16-А"), room_key("16-а"))

    def test_textual_rooms_normalize_case_and_spacing_without_merging_names(self):
        self.assertEqual(display_room("  Спорт   зал  "), "Спорт зал")
        self.assertEqual(room_key("  СПОРТ   ЗАЛ "), room_key("спорт зал"))
        self.assertNotEqual(room_key("спорт зал"), room_key("спортзал"))
        self.assertNotEqual(room_key("лаборатория 1"), room_key("лаборатория 2"))
        self.assertNotEqual(room_key("ауд. 16"), room_key("16"))
        self.assertNotEqual(room_key("ё"), room_key("е"))


if __name__ == "__main__":
    unittest.main()
