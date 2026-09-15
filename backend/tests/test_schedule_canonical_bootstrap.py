import unittest

from backend.services.schedule_canonical_bootstrap import audit_groups, build_bootstrap_canonical
from backend.services.schedule_parser_v2 import NO_LESSON
from backend.scripts.build_full_current_v2 import normalize_grade7_candidate_block


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
