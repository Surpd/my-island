import unittest

from backend.scripts.build_current_week_overlay import semantic_norm


class CurrentWeekOverlaySemanticTests(unittest.TestCase):
    def test_room_change_is_not_a_semantic_change(self):
        self.assertEqual(
            semantic_norm("Англ 5 Игорь\nкаб.18"),
            semantic_norm("Англ 5 Игорь\nкаб.8"),
        )
        self.assertEqual(
            semantic_norm("Англ 2/1 Игорь\nзал"),
            semantic_norm("Англ 2/1 Игорь\n16"),
        )

    def test_subject_teacher_and_delivery_changes_remain_semantic(self):
        self.assertNotEqual(
            semantic_norm("Литература ЕВ\nкаб.15"),
            semantic_norm("🥨"),
        )
        self.assertNotEqual(
            semantic_norm("Химия ОГЭ АнК\nкаб.10"),
            semantic_norm("Химия ОГЭ АнК онлайн\nкаб.10"),
        )

    def test_room_marker_is_removed_without_erasing_lesson_text(self):
        self.assertEqual(
            semantic_norm("кл час 9-А\nкаб.8"),
            semantic_norm("кл час 9-А\nкаб.9"),
        )
        self.assertNotEqual(
            semantic_norm("кл час 9-А\nкаб.8"),
            semantic_norm("кл час 9-Д\nкаб.8"),
        )


if __name__ == "__main__":
    unittest.main()
