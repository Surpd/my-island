import unittest

from backend.scripts.build_current_week_overlay import by_source_column, semantic_norm


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

    def test_logical_block_comparison_ignores_inserted_source_rows(self):
        template = [{"source_cell": "L16", "source_column": 11, "raw_text": "Русский ИА"}]
        weekly = [{"source_cell": "L17", "source_column": 11, "raw_text": "Русский ИА"}]
        self.assertEqual(
            {column: semantic_norm(item["raw_text"]) for column, item in by_source_column(template).items()},
            {column: semantic_norm(item["raw_text"]) for column, item in by_source_column(weekly).items()},
        )


if __name__ == "__main__":
    unittest.main()
