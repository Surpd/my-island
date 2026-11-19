from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from backend.database import Database
from backend.services.schedule_experiments import (
    ExperimentConflict, create_experiment, create_experiment_week, get_experiment, save_experiment,
)
from backend.services.schedule_term_comparison import (
    record_term_comparison_decision, term_comparison_lesson_fingerprint,
)


def comparison_of(draft):
    return draft["payload"]["school_source"]["term2_preparation"]["comparison"]


class TermComparisonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Database(Path(self.directory.name) / "term-comparison.db")
        self.database.initialize()
        lesson = {"id": "lesson", "subject": "Предмет", "day": 0, "period": 0, "room": "1"}
        self.payload = {
            "days": [0],
            "slots": [{"start": "09:00", "end": "09:45"}],
            "lessons": [lesson],
            "school_source": {"term2_preparation": {"comparison": {
                "items": [
                    {"key": "B4", "kind": "changed", "lesson_id": "lesson",
                     "after": {"day": 0, "start": "09:00", "end": "09:45"}},
                    {"key": "C4", "kind": "removed", "lesson_id": "lesson"},
                ],
                "baseline_lessons": {"lesson": term_comparison_lesson_fingerprint(lesson)},
            }}},
        }
        self.draft = create_experiment(self.database, name="Synthetic term copy", source="import",
                                       payload=self.payload, _trusted_resolution_history=True)

    def decide(self, revision, action, key="B4", lesson_id=None):
        return record_term_comparison_decision(self.database, self.draft["id"],
            expected_revision=revision, key=key, action=action, reason="Проверено по источнику",
            lesson_id=lesson_id, actor_user_id=None)

    def test_original_evidence_survives_saves_and_acceptance_requires_a_saved_edit(self):
        with self.assertRaisesRegex(ValueError, "Сначала внесите правку"):
            self.decide(1, "accept")
        deferred = self.decide(1, "defer")
        self.assertEqual(deferred["payload"]["lessons"], self.draft["payload"]["lessons"])
        self.assertEqual(comparison_of(deferred)["decisions"][0]["status"], "deferred")
        with self.assertRaises(ExperimentConflict):
            self.decide(1, "reject")

        forged_edit = deepcopy(deferred["payload"])
        forged_edit["lessons"][0]["room"] = "2"
        forged_edit["school_source"]["term2_preparation"]["comparison"] = {"decisions": [{"status": "rejected"}]}
        edited = save_experiment(self.database, self.draft["id"], expected_revision=2, payload=forged_edit)
        for key in ("items", "baseline_lessons", "decisions"):
            self.assertEqual(comparison_of(deferred)[key], comparison_of(edited)[key])
        self.assertEqual(comparison_of(edited)["manual_edits"]["lesson"],
                         term_comparison_lesson_fingerprint(edited["payload"]["lessons"][0]))
        accepted = self.decide(3, "accept")
        self.assertEqual(accepted["payload"]["lessons"], edited["payload"]["lessons"])
        self.assertEqual([row["status"] for row in comparison_of(accepted)["decisions"]], ["deferred", "accepted"])
        self.assertEqual(get_experiment(self.database, self.draft["id"]), accepted)

    def test_removal_and_reject_are_explicit_and_school_weeks_have_no_comparison_workflow(self):
        with self.assertRaisesRegex(ValueError, "Удалите связанное занятие"):
            self.decide(1, "accept", "C4")
        rejected = self.decide(1, "reject")
        self.assertEqual(rejected["payload"]["lessons"], self.draft["payload"]["lessons"])
        removed = save_experiment(self.database, self.draft["id"], expected_revision=2,
                                  payload={**rejected["payload"], "lessons": []})
        accepted = self.decide(removed["revision"], "accept", "C4")
        self.assertEqual(comparison_of(accepted)["decisions"][-1]["lesson_id"], "lesson")
        week = create_experiment_week(self.database, self.draft["id"], week_start="2026-11-23",
                                      expected_revision=accepted["revision"])
        self.assertNotIn("comparison", week["payload"].get("school_source", {}).get("term2_preparation", {}))

    def test_proven_equivalence_is_closed_without_editing_the_lesson(self):
        payload = deepcopy(self.payload)
        row = payload["school_source"]["term2_preparation"]["comparison"]["items"][0]
        row.update(initial_status="accepted", assessment={
            "category": "equivalent", "reason": "Один кабинет и одно назначение",
            "priority": 3, "type": "Написание кабинета", "options": [],
            "roster_evidence": {
                "sources": [{"gid": 728827779, "range": "B3:B8", "label": "Список группы"}],
                "outcome": "closed", "historical": "Шесть участников",
                "current": "Тот же состав", "overrides": "Ручных исключений нет",
                "reliability": "Точное совпадение", "reason": "Состав и назначение подтверждены",
            },
        })
        self.draft = create_experiment(self.database, name="Reviewed copy", source="import",
                                       payload=payload, _trusted_resolution_history=True)
        with self.assertRaisesRegex(ValueError, "уже"):
            self.decide(1, "accept")
        changed = deepcopy(self.draft["payload"])
        client_row = changed["school_source"]["term2_preparation"]["comparison"]["items"][0]
        client_row["initial_status"] = "pending"
        client_row["assessment"].pop("roster_evidence")
        saved = save_experiment(self.database, self.draft["id"], expected_revision=1, payload=changed)
        self.assertEqual(comparison_of(saved)["items"][0], row)
        self.assertEqual(saved["payload"]["lessons"][0]["room"], "1")
        self.assertFalse(comparison_of(saved).get("manual_edits"))

    def test_background_changes_do_not_count_as_an_explicit_edit(self):
        payload = deepcopy(self.draft["payload"])
        payload["lessons"][0]["room"] = "3"
        updated = save_experiment(self.database, self.draft["id"], expected_revision=1, payload=payload,
                                  _trusted_resolution_history=True)
        self.assertNotIn("manual_edits", comparison_of(updated))
        with self.assertRaisesRegex(ValueError, "Сначала внесите правку"):
            self.decide(updated["revision"], "accept")

    def test_untrusted_import_cannot_supply_comparison_decisions(self):
        draft = create_experiment(self.database, name="Unverified template", source="import", payload=self.payload)
        self.assertNotIn("comparison", draft["payload"].get("school_source", {}).get("term2_preparation", {}))
