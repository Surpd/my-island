import hashlib
import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.database import Database
from backend.services.schedule_experiments import (
    create_experiment,
    create_experiment_week,
    delete_experiment,
    duplicate_experiment,
    get_experiment,
    save_experiment,
)
from backend.services.school_template_selection import (
    approve_legacy_template_baseline,
    open_active_school_template, preview_school_template_selection,
    select_school_template, rollback_school_template,
)
from backend.scripts.restore_school_editor_template import digest, restore


class ActiveSchoolTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "school-template.sqlite3")
        self.database.initialize()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def school_payload(*, subject="Предмет", school_source=None):
        class_ids = [str(uuid4()) for _ in range(9)]
        lessons = []
        for index in range(386):
            lunch = index >= 337
            class_id = class_ids[index % len(class_ids)]
            lessons.append({
                "id": f"sheet:synthetic:A{index + 1}",
                "day": index % 5,
                "period": index % 9,
                "start": "11:50" if lunch else "09:00",
                "end": "12:35" if lunch else "09:45",
                "classId": class_id,
                "classIds": [class_id],
                "groupId": class_id,
                "groupIds": [class_id],
                "audience": {"kind": "classes", "class_ids": [class_id]},
                "subject": "Обед" if lunch else subject,
                "teacherId": "",
                "teacherIds": [],
                "room": "",
                "specialMark": "lunch" if lunch else "",
                "needsClarification": False,
                "source": {
                    "sheet_id": "synthetic",
                    "cells": [f"A{index + 1}"],
                    "raw_text": "🥨" if lunch else subject,
                },
            })
        return {
            "days": [0, 1, 2, 3, 4],
            "slots": [{"start": "09:00", "end": "09:45"}],
            "classes": class_ids,
            "lessons": lessons,
            "draft_kind": "school",
            "school_source": school_source or {
                "kind": "current_google_template",
                "role": "source_led_school_template",
                "spreadsheet_id": "synthetic-spreadsheet",
                "sheet_id": "synthetic-sheet",
            },
            "import_review": {
                "status": "source_led_applied",
                "source_cell_count": 386,
                "source_block_count": 386,
            },
            "transfer_result": {
                "transferred": 337,
                "explicit_lunch": 49,
                "needs_clarification": 0,
                "import_errors": 0,
            },
        }

    def create_draft(self, name, payload):
        return create_experiment(self.database, name=name, source="import", payload=payload)

    def activate(self, draft):
        payload_json = json.dumps(draft["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.database.connection() as connection:
            self.database.execute(connection, """INSERT INTO school_template_selection
                (scope,draft_id,source_draft_id,source_revision,source_payload_sha256,activated_at)
                VALUES ('school',?,?,?,?,?)""", (
                draft["id"], draft["id"], draft["revision"],
                hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                "2026-10-19T00:00:00Z",
            ))

    def selection_row(self):
        with self.database.connection() as connection:
            row = self.database.execute(connection,
                "SELECT * FROM school_template_selection WHERE scope='school'").fetchone()
            return dict(row) if row else None

    def test_missing_pointer_fails_without_creating_canonical_copy(self):
        with self.assertRaisesRegex(ValueError, "Основной школьный шаблон не назначен"):
            open_active_school_template(self.database)
        with self.database.connection() as connection:
            count = self.database.execute(connection,
                "SELECT COUNT(*) AS count FROM schedule_experiment_drafts").fetchone()["count"]
        self.assertEqual(count, 0)
        self.assertIsNone(self.selection_row())

    def test_opens_explicit_source_led_draft_even_with_an_old_canonical_primary(self):
        old_primary = self.create_draft(
            "Школа · canonical template",
            self.school_payload(school_source={
                "kind": "canonical_template",
                "role": "primary_school_template",
                "canonical_version_id": "old-canonical-v1",
            }),
        )
        active = self.create_draft(
            "Проверенный исходный шаблон",
            self.school_payload(),
        )
        active = save_experiment(
            self.database,
            active["id"],
            expected_revision=active["revision"],
            payload=active["payload"],
            name="Переименованный активный шаблон",
        )
        self.activate(active)

        opened = open_active_school_template(self.database)

        self.assertNotEqual(active["id"], old_primary["id"])
        self.assertEqual(opened["id"], active["id"])
        self.assertEqual(opened["name"], "Переименованный активный шаблон")
        self.assertTrue(opened["already_exists"])
        self.assertEqual(opened["payload"]["import_review"]["status"], "source_led_applied")
        self.assertEqual(len(opened["payload"]["lessons"]), 386)

    def test_saves_reloads_duplicates_and_weeks_without_changing_selection(self):
        active = self.create_draft("Исходный шаблон", self.school_payload())
        self.activate(active)
        selected_before = self.selection_row()

        duplicate = duplicate_experiment(self.database, active["id"], name="Копия шаблона")
        week = create_experiment_week(
            self.database, active["id"], week_start="2026-10-19",
            expected_revision=active["revision"],
        )
        self.assertNotEqual(week["id"], active["id"])
        self.assertEqual(week["payload"]["source_template"], {
            "id": active["id"], "revision": active["revision"],
        })

        self.assertEqual(open_active_school_template(self.database)["id"], active["id"])
        self.assertEqual(open_active_school_template(self.database)["id"], active["id"])
        self.assertEqual(self.selection_row(), selected_before)

        edited_payload = dict(get_experiment(self.database, active["id"])["payload"])
        edited_payload["lessons"] = list(edited_payload["lessons"])
        edited_payload["lessons"][0] = {**edited_payload["lessons"][0], "subject": "Изменённый предмет"}
        edited = save_experiment(
            self.database, active["id"], expected_revision=active["revision"],
            payload=edited_payload,
        )

        self.assertEqual(open_active_school_template(self.database)["id"], active["id"])
        self.assertEqual(open_active_school_template(self.database)["payload"]["lessons"][0]["subject"],
                         "Изменённый предмет")
        self.assertEqual(get_experiment(self.database, week["id"])["payload"]["lessons"][0]["subject"],
                         "Предмет")
        self.assertEqual(get_experiment(self.database, duplicate["id"])["payload"]["lessons"][0]["subject"],
                         "Предмет")
        self.assertEqual(self.selection_row(), selected_before)
        self.assertEqual(edited["revision"], active["revision"] + 1)

    def test_creating_same_week_from_same_template_revision_is_idempotent(self):
        active = self.create_draft("Основной шаблон", self.school_payload())
        self.activate(active)
        first = create_experiment_week(
            self.database, active["id"], week_start="2026-10-19",
            expected_revision=active["revision"],
        )
        repeated = create_experiment_week(
            self.database, active["id"], week_start="2026-10-19",
            expected_revision=active["revision"],
        )
        self.assertEqual(repeated["id"], first["id"])
        self.assertTrue(repeated["already_exists"])
        self.assertEqual(repeated["payload"]["source_template"], {
            "id": active["id"], "revision": active["revision"],
        })

    def test_concurrent_week_creation_returns_one_draft_and_keeps_manual_edits(self):
        active = self.create_draft("Основной шаблон", self.school_payload())
        self.activate(active)
        def create():
            return create_experiment_week(self.database, active["id"],
                week_start="2026-10-19", expected_revision=active["revision"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(first["id"], second["id"])
        edited_payload = dict(first["payload"])
        edited_payload["lessons"] = list(edited_payload["lessons"])
        edited_payload["lessons"][0] = {**edited_payload["lessons"][0], "subject": "Ручная правка"}
        edited = save_experiment(self.database, first["id"], expected_revision=first["revision"],
                                 payload=edited_payload)
        reopened = create()
        self.assertEqual(reopened["id"], edited["id"])
        self.assertEqual(reopened["payload"]["lessons"][0]["subject"], "Ручная правка")

    def test_new_template_revision_cannot_create_duplicate_calendar_week(self):
        active = self.create_draft("Основной шаблон", self.school_payload())
        self.activate(active)
        first = create_experiment_week(
            self.database, active["id"], week_start="2026-10-19",
            expected_revision=active["revision"],
        )
        revised = save_experiment(
            self.database, active["id"], expected_revision=active["revision"],
            payload=active["payload"],
        )
        with self.assertRaisesRegex(ValueError, "уже сохранена"):
            create_experiment_week(self.database, revised["id"], week_start="2026-10-19",
                expected_revision=revised["revision"])
        self.assertEqual(get_experiment(self.database, first["id"])["payload"]["source_template"]["revision"], 1)

    def test_template_switch_compares_and_rolls_back_a_saved_snapshot(self):
        first = self.create_draft("Основной A", self.school_payload())
        self.activate(first)
        second_payload = self.school_payload(subject="Другой предмет")
        second = self.create_draft("Шаблон B", second_payload)

        preview = preview_school_template_selection(self.database, second["id"])
        self.assertEqual(len(preview["changed"]), 386)
        self.assertEqual(preview["changed"][0]["before"]["teachers"], [])
        switched = select_school_template(
            self.database, second["id"], expected_revision=second["revision"],
            expected_current_id=first["id"], expected_current_revision=first["revision"],
            expected_candidate_hash=preview["candidate_hash"],
        )
        self.assertEqual(open_active_school_template(self.database)["id"], second["id"])
        self.assertEqual(switched["selected"]["revision"], second["revision"])

        restored = rollback_school_template(self.database, history_id=switched["history_id"])
        selected = open_active_school_template(self.database)
        self.assertEqual(selected["payload"], first["payload"])
        self.assertNotEqual(selected["id"], first["id"])
        self.assertEqual(restored["draft"]["payload"], first["payload"])

    def test_legacy_baseline_requires_explicit_approval_and_records_missing_provenance(self):
        payload = self.school_payload()
        payload["lessons"] = payload["lessons"][:2]
        selected = self.create_draft("Legacy primary", payload)
        self.activate(selected)
        changed_payload = deepcopy(payload)
        changed_payload["lessons"][0]["subject"] = "Редакция 46"
        changed = save_experiment(self.database, selected["id"], expected_revision=selected["revision"], payload=changed_payload)
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE school_template_selection SET source_revision=45,source_payload_sha256='missing-revision-45' WHERE scope='school'")

        preview = preview_school_template_selection(self.database, changed["id"])
        self.assertTrue(preview["baseline_recovery_required"])
        self.assertFalse(preview["has_semantic_changes"])
        self.assertEqual(preview["added"], [])
        self.assertEqual(preview["removed"], [])
        self.assertEqual(preview["changed"], [])
        self.assertEqual(len(preview["baseline_recovery"]["contents"]), 2)
        with self.assertRaisesRegex(ValueError, "Снимок выбранной версии не найден"):
            select_school_template(self.database, changed["id"], expected_revision=changed["revision"],
                expected_current_id=changed["id"], expected_current_revision=45,
                expected_candidate_hash=preview["candidate_hash"])

        result = approve_legacy_template_baseline(self.database, changed["id"],
            expected_revision=changed["revision"], expected_payload_hash=preview["baseline_recovery"]["payload_hash"],
            expected_missing_revision=45, expected_missing_payload_hash="missing-revision-45", actor_user_id="admin-1")
        self.assertEqual(result["selected"]["revision"], changed["revision"])
        self.assertFalse(open_active_school_template(self.database)["selection_stale"])
        with self.database.connection() as connection:
            approval = dict(self.database.execute(connection, "SELECT * FROM school_template_baseline_approvals WHERE scope='school'").fetchone())
            history = dict(self.database.execute(connection, "SELECT * FROM school_template_selection_history WHERE id=?", (result["history_id"],)).fetchone())
        self.assertEqual(approval["missing_revision"], 45)
        self.assertEqual(approval["provenance"], "legacy_editor_state_explicitly_approved_without_historical_snapshot")
        self.assertIsNone(history["previous_payload"])
        self.assertEqual(history["next_revision"], changed["revision"])

    def test_legacy_baseline_approval_rejects_a_concurrent_editor_change(self):
        payload = self.school_payload()
        payload["lessons"] = payload["lessons"][:1]
        selected = self.create_draft("Legacy primary", payload)
        self.activate(selected)
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE school_template_selection SET source_revision=45,source_payload_sha256='missing-revision-45' WHERE scope='school'")
        preview = preview_school_template_selection(self.database, selected["id"])
        changed_payload = deepcopy(selected["payload"])
        changed_payload["lessons"][0]["subject"] = "Конкурентная редакция"
        save_experiment(self.database, selected["id"], expected_revision=selected["revision"], payload=changed_payload)
        with self.assertRaisesRegex(ValueError, "шаблон изменился после проверки"):
            approve_legacy_template_baseline(self.database, selected["id"],
                expected_revision=preview["baseline_recovery"]["revision"],
                expected_payload_hash=preview["baseline_recovery"]["payload_hash"],
                expected_missing_revision=45, expected_missing_payload_hash="missing-revision-45", actor_user_id="admin-1")

    def test_template_comparison_reports_overlapping_teacher_and_group(self):
        payload = self.school_payload()
        payload["lessons"] = payload["lessons"][:2]
        payload["lessons"][0].update({"day": 0, "start": "09:00", "end": "10:00", "teacherIds": ["teacher-1"]})
        payload["lessons"][1].update({"day": 0, "start": "09:30", "end": "10:30", "teacherIds": ["teacher-1"]})
        draft = self.create_draft("С конфликтом", payload)
        preview = preview_school_template_selection(self.database, draft["id"])
        self.assertTrue(preview["requires_explicit_problem_acknowledgement"])
        self.assertTrue(any("Конфликт преподавателя" in detail for item in preview["problems"] for detail in item["details"]))

    def test_template_diff_reports_roster_and_day_mode_changes_with_same_lesson_details(self):
        selected_payload = self.school_payload()
        selected_payload["lessons"] = selected_payload["lessons"][:1]
        lesson = selected_payload["lessons"][0]
        lesson["audience_snapshot"] = {"student_ids": ["student-a"], "fingerprint": "old", "resolved_at": "2026-01-01"}
        selected_payload["grade_modes"] = {"9": {"0": "online"}}
        selected = self.create_draft("Выбранный шаблон", selected_payload)
        self.activate(selected)

        candidate_payload = deepcopy(selected_payload)
        candidate_payload["lessons"][0]["audience_snapshot"] = {
            "student_ids": ["student-a", "student-b"], "fingerprint": "new", "resolved_at": "2026-02-01"}
        candidate_payload["lessons"][0]["confirmedParticipantIds"] = ["student-a", "student-b"]
        candidate_payload["day_modes"] = {candidate_payload["lessons"][0]["classId"]: {"0": "online"}}
        candidate_payload["grade_modes"] = {"9": {"0": "sdep"}}
        candidate = self.create_draft("Кандидат с новым составом", candidate_payload)

        preview = preview_school_template_selection(self.database, candidate["id"])
        self.assertEqual(len(preview["changed"]), 1)
        labels = {change["field"] for change in preview["changed"][0]["changes"]}
        self.assertIn("members", labels)
        self.assertIn("audience_snapshot_members", labels)
        self.assertIn("grade_modes", {change["field"] for change in preview["settings_changes"]})
        self.assertIn("day_modes", {change["field"] for change in preview["settings_changes"]})
        self.assertEqual(preview["changed"][0]["before"]["subject"], preview["changed"][0]["after"]["subject"])
        self.assertEqual(preview["changed"][0]["before"]["teachers"], preview["changed"][0]["after"]["teachers"])
        self.assertEqual(preview["changed"][0]["before"]["room"], preview["changed"][0]["after"]["room"])

    def test_service_metadata_and_snapshot_fingerprints_do_not_create_template_change_notice(self):
        payload = self.school_payload()
        payload["lessons"] = payload["lessons"][:1]
        payload["lessons"][0]["audience_snapshot"] = {
            "student_ids": ["student-a"], "fingerprint": "same-roster-old-fingerprint", "resolved_at": "2026-01-01"}
        selected = self.create_draft("Выбранный шаблон", payload)
        selected_preview = preview_school_template_selection(self.database, selected["id"])
        select_school_template(self.database, selected["id"], expected_revision=selected["revision"],
            expected_current_id=None, expected_current_revision=None,
            expected_candidate_hash=selected_preview["candidate_hash"])
        candidate_payload = deepcopy(payload)
        candidate_payload["lessons"][0]["audience_snapshot"]["fingerprint"] = "same-roster-new-fingerprint"
        candidate_payload["lessons"][0]["audience_snapshot"]["resolved_at"] = "2026-08-10"
        candidate_payload["lessons"][0]["source"]["raw_text"] = "Другие исходные ячейки"
        candidate_payload["lessons"][0]["resolution_history"] = [{"service_event": "rechecked"}]
        candidate_payload["import_review"] = {"status": "refreshed", "run_id": "run-2"}
        candidate = save_experiment(self.database, selected["id"], expected_revision=selected["revision"], payload=candidate_payload)

        preview = preview_school_template_selection(self.database, candidate["id"])
        self.assertEqual(preview["changed"], [])
        self.assertEqual(preview["added"], [])
        self.assertEqual(preview["removed"], [])
        self.assertEqual(preview["settings_changes"], [])
        self.assertFalse(preview["has_semantic_changes"])
        self.assertFalse(open_active_school_template(self.database)["selection_stale"])

    def test_editing_selected_template_requires_review_before_weeks_can_use_it(self):
        from backend.services.school_template_selection import select_school_template

        payload = self.school_payload()
        payload["lessons"] = payload["lessons"][:1]
        selected = self.create_draft("Выбранный шаблон", payload)
        preview = preview_school_template_selection(self.database, selected["id"])
        select_school_template(self.database, selected["id"], expected_revision=selected["revision"],
            expected_current_id=None, expected_current_revision=None,
            expected_candidate_hash=preview["candidate_hash"])
        changed = deepcopy(selected["payload"])
        changed["lessons"][0]["confirmedParticipantIds"] = ["new-student"]
        updated = save_experiment(self.database, selected["id"], expected_revision=selected["revision"], payload=changed)
        active = open_active_school_template(self.database)
        self.assertTrue(active["selection_stale"])
        self.assertEqual(active["selected_revision"], selected["revision"])

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.handlers.api import create_router
        admin = self.database.get_or_create_user(975310, "admin")
        with self.database.connection() as connection:
            self.database.execute(connection, "INSERT INTO user_roles(user_id,role,source) VALUES (?,'admin','test') ON CONFLICT(user_id,role) DO NOTHING", (admin["id"],))
        challenge, _ = self.database.create_admin_login_challenge(admin["id"])
        token, _ = self.database.consume_admin_login_challenge(challenge)
        app = FastAPI()
        with patch.dict("os.environ", {"MY_ISLAND_CONSOLIDATED_SCHEDULE": "1"}), \
                patch("backend.services.consolidated_workspace.isolated_consolidation_enabled", return_value=True), \
                patch("backend.services.consolidated_workspace.require_consolidated_admin", return_value=(self.database, str(admin["id"]))):
            app.include_router(create_router(self.database))
        client = TestClient(app)
        admin_headers = {"X-Dev-Auth": f"browser:{token}", "X-School-Admin-Session": token}
        listed = client.get("/api/admin/schedule/experiments/school", headers=admin_headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        active_summary = next(item for item in listed.json()["items"] if item["id"] == updated["id"])
        self.assertTrue(active_summary["selection_stale"])
        blocked = client.post(f"/api/admin/schedule/experiments/school/{updated['id']}/week",
            headers=admin_headers,
            json={"week_start": "2026-10-19", "expected_revision": updated["revision"]})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("изменён после подтверждения", blocked.json()["detail"])

        review = preview_school_template_selection(self.database, updated["id"])
        self.assertTrue(review["current"]["has_unreviewed_changes"])
        self.assertIn("members", {item["field"] for item in review["changed"][0]["changes"]})
        selected_again = select_school_template(self.database, updated["id"], expected_revision=updated["revision"],
            expected_current_id=updated["id"], expected_current_revision=selected["revision"],
            expected_candidate_hash=review["candidate_hash"])
        self.assertEqual(selected_again["selected"]["revision"], updated["revision"])
        active_again = open_active_school_template(self.database)
        self.assertFalse(active_again["selection_stale"])
        week = create_experiment_week(self.database, active_again["id"], week_start="2026-10-19",
                                      expected_revision=active_again["revision"])
        self.assertEqual(week["payload"]["source_template"]["revision"], updated["revision"])

    def test_foreign_key_prevents_deleting_the_active_draft(self):
        active = self.create_draft("Активный шаблон", self.school_payload())
        self.activate(active)

        with self.assertRaises(sqlite3.IntegrityError):
            delete_experiment(self.database, active["id"])

        self.assertEqual(open_active_school_template(self.database)["id"], active["id"])

    @staticmethod
    def restore_source():
        lessons = []
        for index in range(386):
            lunch = index >= 337
            lessons.append({
                "id": f"sheet:638874869:synthetic-{index + 1}",
                "day": index % 5,
                "start": "11:50" if lunch else "09:00",
                "end": "12:35" if lunch else "09:45",
                "subject": "Обед" if lunch else "Предмет",
                "specialMark": "lunch" if lunch else "lesson",
                "source": {
                    "sheet_id": "638874869",
                    "sheet_title": "2026/27 шаблон",
                    "cells": [f"A{index + 1}"],
                    "raw_text": "🥨" if lunch else "Оригинальная подпись",
                    "unknown_source_annotation": {"line_breaks": ["line one", "line two"]},
                },
            })
        return {
            "id": "4dc886ce-db12-4faf-bbc7-e614a2fce673",
            "name": "ШКОЛА · Реальный шаблон 2026/27 · На проверке",
            "revision": 45,
            "payload": {
                "days": [0, 1, 2, 3, 4],
                "slots": [{"start": "09:00", "end": "09:45"}],
                "classes": [],
                "lessons": lessons,
                "draft_kind": "school",
                "school_source": {
                    "kind": "current_google_template",
                    "canonical_version_id": "source-version-token",
                    "spreadsheet_id": "1j5dI2VbT0RoW2A5ZkCpMYVdt7cSY4ZjNxQaMWtu--Fo",
                    "sheet_id": "638874869",
                    "sheet_title": "2026/27 шаблон",
                    "observed_at": "2026-10-14",
                    "unusual_provenance_field": {"preserve": True},
                },
                "import_review": {
                    "status": "source_led_applied",
                    "source_cell_count": 395,
                    "source_block_count": 265,
                },
                "transfer_result": {"transferred": 238, "import_errors": 0},
                "unusual_top_level_metadata": {"sequence": ["first", "second"]},
            },
        }

    def draft_count(self):
        with self.database.connection() as connection:
            return self.database.execute(connection,
                "SELECT COUNT(*) AS count FROM schedule_experiment_drafts").fetchone()["count"]

    def test_restore_dry_run_validates_without_inserting(self):
        source = self.restore_source()

        report = restore(self.database, source, expected_sha256=digest(source["payload"]))

        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["records"], 386)
        self.assertEqual(report["lunch"], 49)
        self.assertEqual(report["other_records"], 337)
        self.assertEqual(self.draft_count(), 0)
        self.assertIsNone(self.selection_row())

    def test_restore_apply_inserts_exact_original_payload_and_pointer_once(self):
        source = self.restore_source()
        expected_payload = json.loads(json.dumps(source["payload"], ensure_ascii=False))
        checksum = digest(source["payload"])

        first = restore(self.database, source, expected_sha256=checksum, apply=True)
        second = restore(self.database, source, expected_sha256=checksum, apply=True)

        self.assertTrue(first["valid"], first["errors"])
        self.assertFalse(first["already_restored"])
        self.assertEqual(first["revision"], 45)
        self.assertNotEqual(first["draft_id"], source["id"])
        self.assertTrue(second["already_restored"])
        self.assertEqual(second["draft_id"], first["draft_id"])
        self.assertEqual(self.draft_count(), 1)
        saved = get_experiment(self.database, first["draft_id"])
        self.assertEqual(saved["revision"], 45)
        self.assertEqual(saved["payload"], expected_payload)
        self.assertEqual(saved["payload"]["school_source"]["unusual_provenance_field"], {"preserve": True})
        self.assertEqual(saved["payload"]["lessons"][0]["source"]["unknown_source_annotation"],
                         {"line_breaks": ["line one", "line two"]})
        self.assertEqual(saved["payload"]["unusual_top_level_metadata"], {"sequence": ["first", "second"]})
        selection = self.selection_row()
        self.assertEqual(selection["draft_id"], first["draft_id"])
        self.assertEqual(selection["source_draft_id"], source["id"])
        self.assertEqual(selection["source_revision"], 45)
        self.assertEqual(selection["source_payload_sha256"], checksum)

    def test_malformed_group_reference_is_invalid_and_not_inserted(self):
        source = self.restore_source()
        source["payload"]["lessons"][0]["groupId"] = "missing-group-id"

        report = restore(self.database, source, expected_sha256=digest(source["payload"]), apply=True)

        self.assertFalse(report["valid"])
        self.assertIn({"missing_groups": ["missing-group-id"]}, report["errors"])
        self.assertEqual(self.draft_count(), 0)
        self.assertIsNone(self.selection_row())

    def test_different_existing_selection_is_not_overwritten(self):
        source = self.restore_source()
        existing_id = "existing-selected-draft"
        with self.database.connection() as connection:
            self.database.execute(connection, """INSERT INTO schedule_experiment_drafts
                (id,name,revision,payload) VALUES (?,?,1,'{}')""", (existing_id, "Existing"))
            self.database.execute(connection, """INSERT INTO school_template_selection
                (scope,draft_id,source_draft_id,source_revision,source_payload_sha256)
                VALUES ('school',?,?,?,?)""", (existing_id, "different-source", 4, "different-checksum"))

        with self.assertRaisesRegex(ValueError, "different active selection"):
            restore(self.database, source, expected_sha256=digest(source["payload"]), apply=True)

        self.assertEqual(self.draft_count(), 1)
        self.assertEqual(self.selection_row()["draft_id"], existing_id)

    def test_checksum_mismatch_raises_without_inserting(self):
        source = self.restore_source()

        with self.assertRaisesRegex(ValueError, "checksum differs"):
            restore(self.database, source, expected_sha256="0" * 64, apply=True)

        self.assertEqual(self.draft_count(), 0)
        self.assertIsNone(self.selection_row())


if __name__ == "__main__":
    unittest.main()
