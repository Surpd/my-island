from __future__ import annotations

import unittest

from backend.services.schedule_semantic_fallback import merge_review_only_proposals, propose_changed_lessons
from backend.services.school_data import SemanticResponse


class FakeProvider:
    def __init__(self, resolutions):
        self.resolutions = resolutions
        self.calls = []

    def interpret(self, request):
        self.calls.append(request)
        return SemanticResponse("fake", "test", {"resolutions": self.resolutions})


class ScheduleSemanticFallbackTests(unittest.TestCase):
    def setUp(self):
        self.corpus = {
            "students": [{"id": "s5", "class_name": "5A"}, {"id": "s6", "class_name": "6A"}],
            "teachers": [{"id": "t1", "display_name": "Ангелина"}],
            "groups": [
                {"id": "g1", "name": "English Group 1", "subject": "Английский", "group_type": "instructional"},
                {"id": "g2", "name": "Math A", "subject": "Математика", "group_type": "instructional"},
            ],
            "memberships": [
                {"group_id": "g1", "identity_id": "s5"},
                {"group_id": "g1", "identity_id": "s6"},
                {"group_id": "g2", "identity_id": "s5"},
            ],
        }
        self.lesson = {"source_cell": "B3", "raw_text": "Англ группа 1 Ангелина",
                       "subject": "Англ", "audience": "5", "resolved_identity_ids": []}
        self.patch = {"resolution_state": "UNRESOLVED", "weekly_source_cells": ["B3"],
                      "resolution_details": ["explicit instructional audience has no unique canonical group"],
                      "before": {"assignments": [{"teacher_ids": ["t1"]}]}}

    def test_changed_text_can_propose_only_existing_class_relevant_ids(self):
        provider = FakeProvider([{"source_cell": "B3", "teacher_id": "t1", "group_id": "g1"}])
        lessons, accepted, status = propose_changed_lessons([self.lesson], [self.patch], self.corpus, provider=provider)
        self.assertEqual(accepted, {"B3"})
        self.assertEqual(lessons[0]["resolved_group_ids"], ["g1"])
        self.assertEqual(lessons[0]["resolved_identity_ids"], ["t1"])
        self.assertEqual(status["status"], "proposed")
        request = provider.calls[0]
        self.assertEqual(request.source_type, "schedule_weekly")
        self.assertEqual({item["id"] for item in request.structural_payload["candidates"][0]["group_candidates"]}, {"g1"})
        self.assertNotIn("s5", str(request.structural_payload))

    def test_hallucinated_or_wrong_subject_group_is_rejected(self):
        for group_id in ("g2", "invented"):
            with self.subTest(group_id=group_id):
                provider = FakeProvider([{"source_cell": "B3", "teacher_id": "", "group_id": group_id}])
                lessons, accepted, status = propose_changed_lessons([self.lesson], [self.patch], self.corpus, provider=provider)
                self.assertFalse(accepted)
                self.assertNotIn("resolved_group_ids", lessons[0])
                self.assertEqual(status["status"], "unresolved")

    def test_unchanged_and_roster_failures_never_call_model(self):
        for patch in (
            {**self.patch, "resolution_state": "AUTO_RESOLVED"},
            {**self.patch, "resolution_details": ["student is missing from the canonical base-class membership"]},
        ):
            with self.subTest(patch=patch):
                provider = FakeProvider([])
                _, accepted, status = propose_changed_lessons([self.lesson], [patch], self.corpus, provider=provider)
                self.assertFalse(accepted)
                self.assertEqual(status["status"], "not_needed")
                self.assertFalse(provider.calls)

    def test_model_failure_keeps_deterministic_result(self):
        class BrokenProvider:
            def interpret(self, request):
                raise TimeoutError()

        lessons, accepted, status = propose_changed_lessons([self.lesson], [self.patch], self.corpus, provider=BrokenProvider())
        self.assertFalse(accepted)
        self.assertEqual(lessons, [self.lesson])
        self.assertEqual(status["status"], "unavailable")

    def test_validated_result_still_requires_review_and_roster_gap_cannot_replace(self):
        key = (0, "09:00", "09:45", "5")
        original = {"items": [{"key": key, "classification": "REPLACED", "patch": dict(self.patch)}],
                    "patches": [dict(self.patch)]}
        candidate = {"key": key, "weekly_cells": ["B3"],
                     "weekly_block": {"status": "resolved"},
                     "patch": {"assignments": [{"activity": "Английский", "canonical_group_ids": ["g1"]}],
                               "resolution_state": "AUTO_RESOLVED"}}
        count = merge_review_only_proposals(original, {"items": [candidate]}, {"B3"})
        self.assertEqual(count, 1)
        self.assertEqual(original["patches"][0]["resolution_state"], "NEEDS_CONFIRMATION")
        self.assertTrue(original["patches"][0]["semantic_fallback"]["review_required"])

        original = {"items": [{"key": key, "classification": "REPLACED", "patch": dict(self.patch)}],
                    "patches": [dict(self.patch)]}
        candidate["weekly_block"] = {"status": "unresolved", "unresolved": [{"reason": "student is missing"}]}
        self.assertEqual(merge_review_only_proposals(original, {"items": [candidate]}, {"B3"}), 0)
        self.assertNotIn("semantic_fallback", original["patches"][0])


if __name__ == "__main__":
    unittest.main()
