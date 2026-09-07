import unittest

from backend.services.identity_reconciliation import (
    IdentityCandidate,
    Resolution,
    SourceObservation,
    bulk_merge_is_safe,
    normalize_name,
    plan_disappeared_source_records,
    resolve_identity,
    validate_semantic_proposal,
)
from backend.services.school_data import GroqSemanticProvider


class IdentityReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.people = (
            IdentityCandidate("v", "Горлова Виктория", "9-А"),
            IdentityCandidate("p", "Мищенко Пётр", "9-А"),
            IdentityCandidate("t", "Коченкова Таисия", "7"),
            IdentityCandidate("k", "Кудимов Пётр", "7"),
        )

    def test_confirmed_short_name_variants(self):
        for source, expected, grade in (("Горлова Вика", "v", "9"), ("Мищенко Петя", "p", "9-А"), ("Коченкова Тая", "t", "7"), ("Кудимов Петя", "k", "7")):
            result = resolve_identity(source, self.people, class_name=grade)
            self.assertEqual(expected, result.identity_id)
            self.assertEqual("name_variant_context", result.method)

    def test_same_short_name_is_ambiguous(self):
        result = resolve_identity("Петя", (IdentityCandidate("a", "Мищенко Пётр", "9-А"), IdentityCandidate("b", "Кудимов Пётр", "7")))
        self.assertIsNone(result.identity_id)
        self.assertEqual("unresolved", result.method)

    def test_context_conflict_does_not_merge(self):
        result = resolve_identity("Мищенко Петя", self.people, class_name="11")
        self.assertIsNone(result.identity_id)

    def test_normalization_and_observation(self):
        self.assertEqual("мищенко петр", normalize_name("  МИЩЕНКО ПЁТР  "))
        result = resolve_identity("Мищенко Петя", self.people, source_ref="groups!F2", observations=(SourceObservation("groups!F2", "Мищенко Петя", "p", "source_observation"),))
        self.assertEqual("p", result.identity_id)
        self.assertEqual("source_observation", result.method)

    def test_groq_proposal_cannot_bypass_gate(self):
        unresolved = Resolution(None, "unresolved", 0.0, {}, ("p",), "ambiguous")
        self.assertFalse(validate_semantic_proposal({"identity_id": "p", "confidence": .99, "evidence": {"x": 1}}, unresolved))
        deterministic = Resolution("p", "name_variant_context", .95, {"x": 1})
        self.assertTrue(validate_semantic_proposal({"identity_id": "p", "confidence": .9, "evidence": {"context": "7"}}, deterministic))
        self.assertFalse(validate_semantic_proposal({"identity_id": "p", "confidence": .9}, deterministic))

    def test_groq_provider_is_backend_only_and_optional(self):
        provider = GroqSemanticProvider(api_key="", model="")
        self.assertEqual([], provider.available_models())
        with self.assertRaises(RuntimeError):
            provider.interpret(None)  # type: ignore[arg-type]

    def test_disappearance_and_bulk_guard(self):
        self.assertEqual(("old",), plan_disappeared_source_records({"old", "same"}, {"same"}, source_authoritative=True))
        self.assertEqual((), plan_disappeared_source_records({"old"}, set(), source_authoritative=False))
        self.assertTrue(bulk_merge_is_safe(4, 123))
        self.assertFalse(bulk_merge_is_safe(20, 123))


if __name__ == "__main__":
    unittest.main()
