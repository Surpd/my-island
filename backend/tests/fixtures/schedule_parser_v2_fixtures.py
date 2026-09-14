"""Representative source shapes for the diagnostic-only Parser V2 phase."""

REGRESSION_SCENARIOS = {
    "class_split_literature_class_hour": [
        ("L3", 12, "Литература", "9-А"), ("M3", 13, "Классный час", "9-Д"),
    ],
    "class_split_class_hour_literature": [
        ("L4", 12, "Классный час", "9-А"), ("M4", 13, "Литература", "9-Д"),
    ],
    "math_c_remainder_no_lesson": [
        ("L5", 12, "Математика C", "9-Д"), ("M5", 13, "Обед", "9-Д"),
    ],
    "math_a_b_c_mixed_activities": [
        ("L6", 12, "Математика A", "9-Д"), ("M6", 13, "Обед", "9-Д"), ("N6", 14, "История искусства", "9-Д"),
    ],
    "english_6_remainder_russian": [
        ("L7", 12, "English 6", "9-Д"), ("M7", 13, "Русский", "9-Д"),
    ],
    "english_7_8_remainder_russian": [
        ("L8", 12, "English 7", "9-Д"), ("M8", 13, "English 8", "9-Д"), ("N8", 14, "Русский", "9-Д"),
    ],
    "math_b_chemistry_oge_remainder": [
        ("L9", 12, "Математика B", "9-Д"), ("M9", 13, "Химия ОГЭ", "9-Д"), ("N9", 14, "свободны", "9-Д"),
    ],
    "math_c_multiple_oge": [
        ("L10", 12, "Математика C", "9-Д"), ("M10", 13, "Физика OGE", "9-Д"),
        ("N10", 14, "Химия ОГЭ", "9-Д"), ("O10", 15, "Перерыв", "9-Д"),
    ],
    "pure_oge_electives": [
        ("L11", 12, "Физика OGE", "9-Д"), ("M11", 13, "География OGE", "9-Д"),
        ("N11", 14, "Литература ОГЭ", "9-Д"), ("O11", 15, "нет урока", "9-Д"),
    ],
    "no_lesson_source_variants": [
        ("L12", 12, "Обед", "9-Д"), ("M12", 13, "свободны", "9-Д"), ("N12", 14, "Перерыв", "9-Д"),
    ],
    "simple_activities": [
        ("L13", 12, "Творчество", "9-Д"), ("M13", 13, "Курс по выбору", "9-Д"),
        ("N13", 14, "Цифровой трек", "9-Д"), ("O13", 15, "Тренинг", "9-Д"),
    ],
    "sdep_grade_10_11_friday": [
        ("P14", 16, "SDEP весь день", "10"), ("Q14", 17, "SDEP весь день", "11"),
    ],
}


# Exact Grade 9 source shapes observed in the 2026/27 template and the
# 14–18 September tab. Teacher/room suffixes are retained because they are
# part of the parser input, not semantic test instructions.
REAL_GRADE9_ROWS = {
    "monday_english_7_8_residual": [
        ("L9", 11, "Русский ЕВ\nкаб.6", "9-А"),
        ("M9", 12, "Англ 7 Ангелина\nкаб.9", "9-Д"),
        ("N9", 13, "Англ 8 ОГЭ Игорь\nкаб.18", "9-Д"),
    ],
    "monday_english_6_residual": [
        ("L10", 11, "Англ 6 Ангелина\nкаб.9", "9-А"),
        ("M10", 12, "Русский ЕВ\nкаб.6", "9-Д"),
    ],
    "tuesday_math_group_set": [
        ("L18", 11, "История искусства (группы А, В)\nАнна\nкаб.8", "9-А"),
        ("N18", 13, "Матем С ДФ\nкаб.Нов", "9-Д"),
    ],
    "tuesday_math_a_lunch_b_art_c": [
        ("L19", 11, "Матем А ДФ\nкаб.Нов", "9-А"),
        ("M19", 12, "Обед группы В", "9-Д"),
        ("N19", 13, "История искусства\nАнна\nкаб.8", "9-Д"),
    ],
    "wednesday_math_and_oge": [
        ("L33", 11, "химия ОГЭ АнК онлайн\nкаб.16", "9-А"),
        ("M33", 12, "Матем В ДФ\nкаб.6", "9-Д"),
        ("N33", 13, "🥨", "9-Д"),
    ],
    "friday_oge_training_residual": [
        ("L55", 11, "География ОГЭ Антон\nкаб.17", "9-А"),
        ("M55", 12, "тренинг", "9-Д"),
        ("N55", 13, "тренинг", "9-Д"),
    ],
}
