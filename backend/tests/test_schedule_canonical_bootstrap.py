import unittest

from backend.services.schedule_canonical_bootstrap import audit_groups, build_bootstrap_canonical
from backend.services.schedule_parser_v2 import NO_LESSON
from backend.scripts.build_full_current_v2 import add_explicit_grade7_group_fallback, normalize_bell_times, normalize_grade7_candidate_block


def lesson(cell, text, audience, grade, *, weekday=0, source_day_label="Пн"):
    return {
        "record_key": f"sheet:{cell}",
        "sheet_id": "sheet",
        "tab_title": "2026/27 шаблон",
        "source_cell": cell,
        "source_column": 2,
        "source_row": 3,
        "weekday": weekday,
        "start_time": "09:00",
        "end_time": "09:45",
        "audience": audience,
        "raw_text": text,
        "subject": text,
        "modifiers": {},
        "source_day_label": source_day_label,
        "base_class_name": grade,
    }


class ScheduleCanonicalBootstrapTests(unittest.TestCase):
    def test_punctuated_english_group_number_and_teacher_are_split_semantically(self):
        source = lesson("B3", "Англ. 3 Игорь", "5", "5")
        source["resolved_identity_ids"] = ["igor"]
        corpus = {
            "snapshot": {"id": "snapshot", "fingerprint": "source"},
            "students": [{"id": "student", "class_name": "5"}],
            "groups": [
                {"id": "base5", "name": "5", "base_class_name": "5", "group_type": "class"},
                {"id": "eng3", "name": "English 3", "group_type": "instructional", "subject": "Английский", "subject_subgroup": "3"},
            ],
            "memberships": [{"group_id": "base5", "identity_id": "student"},
                            {"group_id": "eng3", "identity_id": "student"}],
            "teachers": [{"id": "igor", "display_name": "Игорь"}],
            "lessons": [source],
        }
        assignment = next(iter(build_bootstrap_canonical(corpus)["blocks"].values()))["assignments"][0]
        self.assertEqual(assignment["activity"], "Английский")
        self.assertEqual(assignment["audience"]["canonical_group_ids"], ["eng3"])
        self.assertEqual(assignment["teacher_ids"], ["igor"])

    def test_english_pair_marker_routes_union_of_existing_groups_within_class(self):
        corpus = {
            "snapshot": {"id": "snapshot", "fingerprint": "source"},
            "students": [{"id": "5a", "class_name": "5"}, {"id": "5b", "class_name": "5"}, {"id": "6a", "class_name": "6"}],
            "groups": [
                {"id": "base5", "name": "5", "base_class_name": "5", "group_type": "class"},
                {"id": "base6", "name": "6", "base_class_name": "6", "group_type": "class"},
                {"id": "eng1", "name": "English 1", "group_type": "instructional", "subject": "Английский", "subject_subgroup": "1"},
                {"id": "eng2", "name": "English 2", "group_type": "instructional", "subject": "Английский", "subject_subgroup": "2"},
            ],
            "memberships": [
                {"group_id": "base5", "identity_id": "5a"}, {"group_id": "base5", "identity_id": "5b"},
                {"group_id": "base6", "identity_id": "6a"},
                {"group_id": "eng1", "identity_id": "5a"}, {"group_id": "eng1", "identity_id": "6a"},
                {"group_id": "eng2", "identity_id": "5b"},
            ],
            "teachers": [],
            "lessons": [lesson("B3", "Англ 1-2 Ангелина каб.16", "5", "5")],
        }
        block = next(iter(build_bootstrap_canonical(corpus)["blocks"].values()))
        assignment = block["assignments"][0]
        self.assertEqual(assignment["audience"]["canonical_group_ids"], ["eng1", "eng2"])
        self.assertEqual(assignment["student_ids"], ["5a", "5b"])

    def test_cross_class_english_group_and_class_remainder_keep_one_group(self):
        group = "english-1"
        corpus = {
            "snapshot": {"id": "snapshot", "fingerprint": "source"},
            "students": [{"id": "5a", "class_name": "5"}, {"id": "5b", "class_name": "5"},
                         {"id": "6a", "class_name": "6"}, {"id": "6b", "class_name": "6"},
                         {"id": "8a", "class_name": "8"}, {"id": "8b", "class_name": "8"},
                         {"id": "8c", "class_name": "8"}],
            "groups": [{"id": "base5", "name": "5", "base_class_name": "5", "group_type": "class"},
                       {"id": "base6", "name": "6", "base_class_name": "6", "group_type": "class"},
                       {"id": "base8", "name": "8", "base_class_name": "8", "group_type": "class"},
                       {"id": group, "name": "English 1", "group_type": "instructional",
                        "subject": "Английский", "subject_subgroup": "1"},
                       {"id": "english-2", "name": "English 2", "group_type": "instructional",
                        "subject": "Английский", "subject_subgroup": "2"}],
            "memberships": [{"group_id": "base5", "identity_id": item} for item in ("5a", "5b")]
                         + [{"group_id": "base6", "identity_id": item} for item in ("6a", "6b")]
                         + [{"group_id": "base8", "identity_id": item} for item in ("8a", "8b", "8c")]
                         + [{"group_id": group, "identity_id": item} for item in ("5a", "6a", "8a")]
                         + [{"group_id": "english-2", "identity_id": "8b"}],
            "teachers": [],
            "lessons": [lesson("B3", "English Group 1", "5", "5"),
                        lesson("B4", "Нет урока", "5", "5"),
                        lesson("C3", "English Group 1", "6", "6"),
                        lesson("D3", "English Group 1", "8", "8"),
                        lesson("D4", "English Group 2", "8", "8"),
                        lesson("D5", "История", "8", "8")],
        }
        blocks = build_bootstrap_canonical(corpus)["blocks"].values()
        by_grade = {block["grade_scope"]: block for block in blocks}
        five = by_grade["5"]["assignments"]
        six = by_grade["6"]["assignments"]
        eight = by_grade["8"]["assignments"]
        self.assertEqual([item["student_ids"] for item in five[:2]], [["5a"], ["5b"]])
        self.assertEqual(six[0]["student_ids"], ["6a"])
        self.assertEqual({item["audience"]["canonical_group_ids"][0]: item["student_ids"] for item in eight[:2]},
                         {group: ["8a"], "english-2": ["8b"]})
        self.assertEqual(eight[2]["student_ids"], ["8c"])
        self.assertEqual(eight[2]["metadata"]["audience_rule"]["partition_group_ids"],
                         ["english-1", "english-2"])
        english_audit = {item["grade"]: item for item in audit_groups(corpus)["partitions"]
                         if item["name"].endswith("_english")}
        self.assertEqual(set(english_audit["8"]["group_ids"]), {group, "english-2"})
        self.assertEqual(five[0]["audience"]["canonical_group_ids"], [group])
        self.assertEqual(six[0]["audience"]["canonical_group_ids"], [group])
        self.assertEqual(five[1]["metadata"]["audience_rule"],
                         {"kind": "remaining", "grade": "5", "class_group_id": "base5", "partition_group_ids": [group]})

    def test_instructional_group_is_intersected_with_exact_base_class(self):
        group_cell = lesson("B3", "Математика группа 1", "7-А", "7")
        group_cell["resolved_group_ids"] = ["split-1"]
        corpus = {
            "snapshot": {"id": "snapshot", "fingerprint": "source"},
            "students": [{"id": "7a", "class_name": "7-А"}, {"id": "7a2", "class_name": "7-А"},
                         {"id": "7b", "class_name": "7-Б"}],
            "groups": [{"id": "base-a", "name": "7-А", "base_class_name": "7-А", "group_type": "class"},
                       {"id": "base-b", "name": "7-Б", "base_class_name": "7-Б", "group_type": "class"},
                       {"id": "split-1", "name": "Math 1", "group_type": "instructional", "subject": "Математика"}],
            "memberships": [{"group_id": "base-a", "identity_id": "7a"},
                            {"group_id": "base-a", "identity_id": "7a2"},
                            {"group_id": "base-b", "identity_id": "7b"},
                            {"group_id": "split-1", "identity_id": "7a"},
                            {"group_id": "split-1", "identity_id": "7b"}],
            "teachers": [], "lessons": [group_cell, lesson("C3", "Русский", "7-А", "7")],
        }
        block = next(iter(build_bootstrap_canonical(corpus)["blocks"].values()))
        group_assignment = next(item for item in block["assignments"] if item["audience"]["canonical_group_ids"] == ["split-1"])
        self.assertEqual(group_assignment["student_ids"], ["7a"])
        self.assertEqual(group_assignment["metadata"]["audience_rule"]["class_group_id"], "base-a")
        remainder = next(item for item in block["assignments"] if item["activity"] == "Русский")
        self.assertEqual(remainder["student_ids"], ["7a2"])
        self.assertEqual(remainder["metadata"]["audience_rule"], {"kind": "remaining", "grade": "7",
            "class_group_id": "base-a", "partition_group_ids": ["split-1"]})

    def test_bell_times_follow_period_order_and_correct_mistyped_source_label(self):
        values = [[None, "Пн", None, "Пн"]]
        values.extend([[label, None, None, "урок"] for label in (
            "9:00 - 9:45", "9:55-10:40", "10:55 - 11:40", "11:50 - 12:35",
            "12:45-13:30", "13:40 - 14:25", "14:35 -15:20", "15:30 - 16:15",
            "15:25 - 16:10",
        )])
        lessons = [
            {"weekday": 0, "source_row": 1, "source_column": 3, "start_time": "09:00", "end_time": "09:45"},
            {"weekday": 0, "source_row": 9, "source_column": 3, "start_time": "15:25", "end_time": "16:10"},
        ]

        self.assertEqual(normalize_bell_times(lessons, values), 2)
        self.assertEqual((lessons[0]["start_time"], lessons[0]["end_time"], lessons[0]["bell_period"]), ("09:00", "09:45", 1))
        self.assertEqual((lessons[1]["start_time"], lessons[1]["end_time"], lessons[1]["bell_period"]), ("16:25", "17:10", 9))

    def test_grade7_explicit_current_class_and_split_markers_are_editable_group_routes(self):
        base_a = "base-a"
        split_1 = "split-1"
        artifact = {
            "blocks": {"mon-2-7": {
                "weekday": 0, "slot": {"start": "09:55", "end": "10:40"}, "grade_scope": "7",
                "assignments": [], "unresolved": [
                    {"source_cell": "F5", "reason": "explicit instructional audience has no unique canonical group", "raw_text": "Мат 1 Иван"},
                    {"source_cell": "G5", "reason": "explicit instructional audience has no unique canonical group", "raw_text": "Литература 7-А Юлия"},
                ],
            }},
            "unresolved": [{"block_key": "mon-2-7", "weekday": 0, "slot": {"start": "09:55"}, "issues": [], "conflict_student_ids": []}],
            "summary": {"logical_blocks": 1, "unresolved_blocks": 1},
        }
        lessons = [
            {"source_cell": "F5", "raw_text": "Мат 1 Иван", "subject": "Математика", "resolved_identity_ids": ["teacher-1"]},
            {"source_cell": "G5", "raw_text": "Литература 7-А Юлия", "subject": "Литература", "resolved_identity_ids": ["teacher-2"]},
        ]
        groups = [
            {"id": base_a, "name": "grade7:base:7-А"},
            {"id": split_1, "name": "grade7:split:1"},
        ]
        memberships = [
            {"group_id": base_a, "identity_id": "student-a"},
            {"group_id": split_1, "identity_id": "student-split"},
        ]

        routed = add_explicit_grade7_group_fallback(artifact, lessons, groups, memberships)
        block = artifact["blocks"]["mon-2-7"]

        self.assertEqual(routed, 2)
        self.assertEqual(block["unresolved"], [])
        self.assertEqual([item["audience"]["canonical_group_ids"] for item in block["assignments"]], [[split_1], [base_a]])
        self.assertTrue(all(item["metadata"]["review_before_accepting"] for item in block["assignments"]))
        self.assertEqual(artifact["unresolved"], [])

    def test_grade7_inferred_split_complement_uses_source_class_for_ordinary_cell(self):
        candidate = {
            "block_key": "row-1",
            "weekday": "Пн",
            "slot": {"start": "09:55-10:40"},
            "row_routing_dimension": "SHARED_SPLIT_1_2",
            "source_cells": [{"coordinate": "F5", "source_audience": "7-А", "raw_text": "Мат 1"},
                             {"coordinate": "G5", "source_audience": "7-Б", "raw_text": "Русский ИА"}],
            "assignments": [
                {"source_coordinate": "F5", "source_audience": "7-А", "raw_text": "Мат 1",
                 "activity": "Математика", "routing_dimension": "SHARED_SPLIT_1_2",
                 "split_label": "1", "inferred_complement": False,
                 "canonical_group_ids": ["split1"]},
                {"source_coordinate": "G5", "source_audience": "7-Б", "raw_text": "Русский ИА",
                 "activity": "Русский язык", "routing_dimension": "SHARED_SPLIT_1_2",
                 "split_label": "2", "inferred_complement": True,
                 "canonical_group_ids": ["split2"]},
            ],
        }
        groups = [
            {"id": "base-a", "name": "grade7:base:7-А"},
            {"id": "base-b", "name": "grade7:base:7-Б"},
            {"id": "split1", "name": "grade7:split:1"},
            {"id": "split2", "name": "grade7:split:2"},
        ]
        memberships = [
            {"group_id": "base-a", "identity_id": "a"},
            {"group_id": "base-b", "identity_id": "b"},
            {"group_id": "split1", "identity_id": "a"},
            {"group_id": "split2", "identity_id": "b"},
        ]

        block = normalize_grade7_candidate_block(candidate, groups, memberships)

        self.assertEqual(block["assignments"][0]["audience"]["canonical_group_ids"], ["split1"])
        self.assertEqual(block["assignments"][1]["audience"]["canonical_group_ids"], ["base-b"])

    def test_missing_base_membership_is_unresolved_not_no_lesson(self):
        corpus = {
            "snapshot": {"id": "snapshot", "fingerprint": "source"},
            "students": [
                {"id": "s1", "display_name": "One", "class_name": "5"},
                {"id": "s2", "display_name": "Two", "class_name": "5"},
            ],
            "groups": [{"id": "base5", "name": "5", "base_class_name": "5", "group_type": "class"}],
            "memberships": [{"group_id": "base5", "identity_id": "s1"}],
            "teachers": [],
            "lessons": [lesson("B3", "Творчество", "5", "5")],
        }
        artifact = build_bootstrap_canonical(corpus)
        block = next(iter(artifact["blocks"].values()))

        self.assertEqual(artifact["students"]["s1"]["routes"][0]["state"], "ACTIVITY")
        self.assertEqual(artifact["students"]["s2"]["routes"][0]["state"], "UNRESOLVED")
        self.assertNotIn("s2", {student_id for item in block["assignments"] if item["activity"] == NO_LESSON for student_id in item["student_ids"]})
        self.assertEqual(artifact["routing_validation"]["students_without_routes"], [])

    def test_sdep_is_an_explicit_grade_10_day_block(self):
        corpus = {
            "snapshot": {"id": "snapshot", "fingerprint": "source"},
            "students": [{"id": "s10", "display_name": "Ten", "class_name": "10"}],
            "groups": [{"id": "base10", "name": "10", "base_class_name": "10", "group_type": "class"}],
            "memberships": [{"group_id": "base10", "identity_id": "s10"}],
            "teachers": [],
            "lessons": [lesson("P51", "Самостоятельная работа", "10", "10", weekday=4, source_day_label="Пт SDEP")],
        }
        artifact = build_bootstrap_canonical(corpus, explicit_sdep=[(4, "10")])
        block = next(iter(artifact["blocks"].values()))

        self.assertEqual(block["mode"], "SDEP_DAY")
        self.assertEqual(block["slot"], None)
        self.assertEqual(artifact["students"]["s10"]["routes"][0]["activity"], "SDEP")

    def test_exam_label_does_not_override_complete_partition_semantics(self):
        corpus = {
            "students": [
                {"id": "a", "class_name": "11"},
                {"id": "b", "class_name": "11"},
            ],
            "groups": [
                {"id": "base", "name": "11", "base_class_name": "11", "group_type": "class"},
                {"id": "lit-base", "name": "lit:base", "subject": "Литература", "base_class_name": "11", "subject_subgroup": "База"},
                {"id": "lit-ege", "name": "lit:ege", "subject": "Литература", "base_class_name": "11", "subject_subgroup": "ЕГЭ", "exam_track": "ЕГЭ"},
            ],
            "memberships": [
                {"group_id": "base", "identity_id": "a"},
                {"group_id": "base", "identity_id": "b"},
                {"group_id": "lit-base", "identity_id": "a"},
                {"group_id": "lit-ege", "identity_id": "b"},
            ],
        }
        roles = {item["group_id"]: item["role"] for item in audit_groups(corpus)["classification"]}

        self.assertEqual(roles["lit-base"], "instructional_partition")
        self.assertEqual(roles["lit-ege"], "instructional_partition")

    def test_ege_marker_can_use_unique_advanced_roster_partition(self):
        corpus = {
            "students": [{"id": "s1", "class_name": "10"}, {"id": "s2", "class_name": "10"}],
            "groups": [
                {"id": "base", "name": "10", "base_class_name": "10", "group_type": "class"},
                {"id": "social-advanced", "name": "instructional:обществознание:10:угл",
                 "subject": "Обществознание", "base_class_name": "10", "subject_subgroup": "Угл"},
            ],
            "memberships": [{"group_id": "base", "identity_id": "s1"},
                            {"group_id": "base", "identity_id": "s2"},
                            {"group_id": "social-advanced", "identity_id": "s2"}],
            "teachers": [],
            "lessons": [lesson("Q40", "Общество ЕГЭ Леонид", "10", "10")],
        }

        artifact = build_bootstrap_canonical(corpus)
        assignment = next(item for item in next(iter(artifact["blocks"].values()))["assignments"]
                          if item["activity"] != NO_LESSON)

        self.assertEqual(assignment["audience"]["canonical_group_ids"], ["social-advanced"])
        self.assertEqual(assignment["student_ids"], ["s2"])

    def test_version_is_stable_for_same_source_and_directory(self):
        corpus = {
            "snapshot": {"id": "snapshot", "fingerprint": "source"},
            "students": [{"id": "s1", "display_name": "One", "class_name": "5"}],
            "groups": [{"id": "base5", "name": "5", "base_class_name": "5", "group_type": "class"}],
            "memberships": [{"group_id": "base5", "identity_id": "s1"}],
            "teachers": [],
            "lessons": [lesson("B3", "Тренинг", "5", "5")],
        }

        self.assertEqual(build_bootstrap_canonical(corpus)["version_id"], build_bootstrap_canonical(corpus)["version_id"])


if __name__ == "__main__":
    unittest.main()
