import unittest

from backend.services.school_directory_reconciliation import CanonicalRelation, ReconciliationManifest, plan_relations, source_person_is_eligible, validate_manifest


class SchoolDirectoryReconciliationTests(unittest.TestCase):
    def test_inactive_stale_people_are_not_eligible(self):
        self.assertFalse(source_person_is_eligible("Стрижов Георгий", "active"))
        self.assertFalse(source_person_is_eligible("Любой Ученик", "inactive"))

    def test_manual_authority_survives_source_absence_as_unresolved(self):
        current = [CanonicalRelation("student", "class:9-А", "admin_override", manual_authoritative=True)]
        self.assertEqual(plan_relations(current, []).counts, {"NEEDS_SOURCE": 1})

    def test_unresolved_target_is_not_added(self):
        target = [CanonicalRelation("student", "class:7-1", "official", "sheet!F2")]
        manifest = plan_relations([], target, unresolved_keys={("student", "class:7-1")})
        self.assertEqual(manifest.counts, {"NEEDS_SOURCE": 1})

    def test_duplicate_logical_relation_is_deduplicated(self):
        current = [CanonicalRelation("student", "math:9:C", "admin_override"), CanonicalRelation("student", "math:9:C", "official", "sheet!P11")]
        target = [CanonicalRelation("student", "math:9:C", "official", "sheet!P11")]
        self.assertEqual(plan_relations(current, target).counts, {"DEDUPLICATE": 1, "KEEP": 1})

    def test_mass_removal_guard_quarantines_apply(self):
        with self.assertRaisesRegex(ValueError, "mass-removal anomaly"):
            validate_manifest(ReconciliationManifest(), active_base_counts={}, target_group_keys=(), previous_counts={"memberships": 100}, target_counts={"memberships": 50})


if __name__ == "__main__":
    unittest.main()
