import copy
import unittest

from backend.services.student_membership_reconciliation import (
    DryRunPlan,
    ExpectedRelation,
    ProductionSnapshot,
    build_expected_memberships,
    evaluate_invariants,
    reconcile_memberships,
    render_plan_json,
    _group_candidates,
    _resolve_person,
)
from backend.services.student_membership_manual_resolutions import group_assignment_for_source


def identity(identity_id, name, class_name, status="active"):
    return {"id": identity_id, "display_name": name, "class_name": class_name, "status": status, "origin": "test", "source_ref": "test"}


def group(group_id, name, group_type="subject_group", subject=None, base_class_name=None, subject_subgroup=None, exam_track=None):
    return {"id": group_id, "name": name, "display_name": name, "group_type": group_type, "subject": subject, "base_class_name": base_class_name, "subject_subgroup": subject_subgroup, "exam_track": exam_track, "canonical": True, "provenance_source": "test", "provenance_ref": "test"}


def relation(student, target, student_name, group_name, ref="test!A1", authority="test", rule="test rule"):
    return ExpectedRelation(student, target, student_name, group_name, (ref,), authority, rule)


class StudentMembershipReconciliationTests(unittest.TestCase):
    def test_grade9_math_and_english_exactly_one(self):
        students = [identity("s1", "А", "9-А"), identity("s2", "Б", "9-Д")]
        groups = [
            group("ma", "grade9-math-A", subject="Математика", base_class_name="9", subject_subgroup="A"),
            group("mb", "grade9-math-B", subject="Математика", base_class_name="9", subject_subgroup="B"),
            group("e6", "english:6", subject="Английский язык", subject_subgroup="6"),
            group("e7", "english:7", subject="Английский язык", subject_subgroup="7"),
            group("c9a", "9-А", "class", base_class_name="9-А"),
            group("c9d", "9-Д", "class", base_class_name="9-Д"),
        ]
        expected = [
            relation("s1", "ma", "А", "grade9-math-A"), relation("s1", "e6", "А", "english:6"),
            relation("s2", "mb", "Б", "grade9-math-B"), relation("s2", "e7", "Б", "english:7"),
        ]
        checks = evaluate_invariants(expected, ProductionSnapshot(students, groups, [], [], []))
        self.assertEqual({item["name"]: item["status"] for item in checks}["grade9_math_exactly_one"], "PASS")
        self.assertEqual({item["name"]: item["status"] for item in checks}["grade9_english_exactly_one"], "PASS")

    def test_grade11_xor_and_literature_derived_roster(self):
        students = [identity("s1", "Литературный", "11"), identity("s2", "Егэ", "11")]
        groups = [
            group("c11", "11", "class", base_class_name="11"),
            group("lb", "instructional:литература:11:база", subject="Литература", base_class_name="11", subject_subgroup="База"),
            group("le", "instructional:литература:11:егэ", subject="Литература", base_class_name="11", subject_subgroup="ЕГЭ", exam_track="ЕГЭ"),
            group("mb", "math:11:base", subject="Математика", base_class_name="11", subject_subgroup="Base"),
            group("me", "math:11:advanced", subject="Математика", base_class_name="11", subject_subgroup="Advanced"),
            group("sb", "instructional:обществознание:11:база", subject="Обществознание", base_class_name="11", subject_subgroup="База"),
            group("se", "instructional:обществознание:11:угл", subject="Обществознание", base_class_name="11", subject_subgroup="Угл"),
        ]
        rows = {"Списки по классам 26/27": [[""] * 18 for _ in range(4)], "списки групп 26-27": [[""] * 22 for _ in range(98)], "ОГЭ/ЕГЭ": [], "Структура школы — правила": []}
        rows["Списки по классам 26/27"][0][16] = "11 класс"
        rows["Списки по классам 26/27"][1][16] = "Литературный"
        rows["Списки по классам 26/27"][2][16] = "Егэ"
        source = rows["списки групп 26-27"]
        source[47][17] = "Литература"
        source[48][17] = "11 класс"
        source[49][17] = "ЕГЭ ЕВ"
        source[50][17] = "Егэ"
        snapshot = ProductionSnapshot(students, groups, [], [], [])
        expected, issues, _ = build_expected_memberships(rows, snapshot)
        self.assertIn(("s1", "lb"), {(item.identity_id, item.group_id) for item in expected})
        self.assertIn(("s2", "le"), {(item.identity_id, item.group_id) for item in expected})
        checks = evaluate_invariants(expected, snapshot)
        self.assertEqual({item["name"]: item["status"] for item in checks}["grade11_literature_xor"], "PASS")

    def test_selection_does_not_create_membership_and_computed_audiences_are_skipped(self):
        snapshot = ProductionSnapshot([identity("s1", "Выбор", "9-А")], [], [], [], [])
        before = copy.deepcopy(snapshot.__dict__)
        plan = reconcile_memberships([], snapshot, [], {"Списки по классам 26/27": [], "списки групп 26-27": [], "ОГЭ/ЕГЭ": [], "Структура школы — правила": []})
        payload = render_plan_json(plan)
        self.assertEqual(payload["production_writes_performed"], 0)
        self.assertEqual(payload["write_guard"]["student_memberships_inserted"], 0)
        self.assertEqual(len(plan.computed_audiences_skipped), 3)
        self.assertEqual(snapshot.__dict__, before)

    def test_source_disappearance_and_unknowns_are_not_unsafe(self):
        students = [identity("s1", "Ученик", "9-А")]
        groups = [group("g", "grade9-math-A", subject="Математика", base_class_name="9", subject_subgroup="A")]
        current = [{"id": "m1", "identity_id": "s1", "display_name": "Ученик", "identity_status": "active", "group_id": "g", "group_name": "grade9-math-A", "group_type": "subject_group", "source": "admin_override", "source_ref": "manual", "subject": "Математика", "base_class_name": "9", "subject_subgroup": "A", "exam_track": None}]
        plan = reconcile_memberships([], ProductionSnapshot(students, groups, current, [], []), [], {"Списки по классам 26/27": [], "списки групп 26-27": [], "ОГЭ/ЕГЭ": [], "Структура школы — правила": []})
        self.assertEqual([item["action"] for item in plan.assignments], ["PROTECTED"])

    def test_unknown_identity_and_group_remain_unresolved(self):
        snapshot = ProductionSnapshot([identity("s1", "Known", "9-А")], [], [], [], [])
        resolved, issue = _resolve_person("Unknown", "9", "sheet!A1", snapshot)
        self.assertIsNone(resolved)
        self.assertEqual(issue["reason"], "no deterministic candidate")
        source_group = type("SourceGroup", (), {"name": "subject:unknown:9:Группа", "group_type": "subject_group", "subject": "Unknown", "base_class_name": "9", "subject_subgroup": "Группа", "exam_track": None})()
        self.assertEqual(_group_candidates(source_group, []), [])

    def test_manual_grade7_assignment_accepts_multiple_provenance_refs(self):
        resolutions = {
            "confirmed_group_assignments": [{
                "source_name": "Шишков Павел",
                "target_group_name": "7-2",
                "source_ref": "списки групп 26-27!H16",
                "source_refs": ["списки групп 26-27!H16", "Списки по классам 26/27!F25"],
            }]
        }
        assignment = group_assignment_for_source(resolutions, "Шишков Павел", "Списки по классам 26/27!F25")
        self.assertEqual(assignment["target_group_name"], "7-2")

    def test_known_grade9_partition_exception_is_not_a_human_question(self):
        students = [identity("s1", "Холодова Татьяна", "9-А")]
        groups = [
            group("c", "9-А", "class", base_class_name="9-А"),
            group("e", "english:6", subject="Английский язык", subject_subgroup="6"),
        ]
        manual = {"known_exceptions": [{"display_name": "Холодова Татьяна", "exception": "grade9_math_english_partition"}]}
        plan = reconcile_memberships([], ProductionSnapshot(students, groups, [], [], []), [], {"Списки по классам 26/27": [], "списки групп 26-27": [], "ОГЭ/ЕГЭ": [], "Структура школы — правила": []}, manual)
        self.assertEqual(plan.human_confirmation_required, [])
        statuses = {item["name"]: item["status"] for item in plan.invariants}
        self.assertEqual(statuses["grade9_math_exactly_one"], "PASS_WITH_KNOWN_EXCEPTION")
        self.assertEqual(statuses["grade9_english_exactly_one"], "PASS_WITH_KNOWN_EXCEPTION")


if __name__ == "__main__":
    unittest.main()
