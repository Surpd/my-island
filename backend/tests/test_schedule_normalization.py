import unittest
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.services.schedule_experiments import create_experiment, get_experiment, save_experiment
from backend.services.schedule_normalization import normalization_preview, normalized_copy_payload, verified_teacher_directory_evidence


class ScheduleNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.lessons = []
        for block, count in (("source:B29:electives", 4), ("source:F32:electives", 4)):
            for index in range(count):
                self.lessons.append({"id": f"{block}-{index}", "blockId": block,
                    "blockName": "Курсы по выбору", "subject": f"Направление {index}",
                    "room": "301", "teacherId": f"teacher-{index}"})
        self.lessons.extend([
            {"id": "digital", "activityType": "elective", "electiveDirection": "digital", "subject": "track"},
            {"id": "ambiguous", "electiveDirection": "digital", "subject": "unknown"},
            {"id": "ordinary", "subject": "Опрос"},
        ])
        self.draft = {"id": "template", "name": "Рабочий шаблон", "revision": 29,
                      "payload": {"draft_kind": "school", "classes": ["class-5"], "lessons": self.lessons}}

    def test_preview_only_marks_stable_repeated_elective_blocks_as_safe(self):
        preview = normalization_preview(self.draft)
        self.assertEqual(preview["lesson_count"], 11)
        self.assertEqual(preview["automatic_count"], 8)
        self.assertEqual(preview["manual_count"], 2)
        self.assertEqual(preview["already_current_count"], 1)
        self.assertEqual(preview["unclassified_count"], 2)
        self.assertIsNone(preview["real_conflict_count"])
        self.assertTrue(all(row["block_id"].endswith(":electives") for row in preview["changes"]))

    def test_copy_changes_only_evidence_backed_types_and_preserves_source(self):
        preview = normalization_preview(self.draft)
        before = [dict(item) for item in self.draft["payload"]["lessons"]]
        copied = normalized_copy_payload(self.draft, preview)
        self.assertEqual(self.draft["payload"]["lessons"], before)
        self.assertEqual(len(copied["lessons"]), len(before))
        changed = {item["id"]: item for item in copied["lessons"]}
        self.assertTrue(all(changed[f"{block}-{index}"]["activityType"] == "elective"
                            for block in ("source:B29:electives", "source:F32:electives") for index in range(4)))
        self.assertNotIn("activityType", changed["ordinary"])
        self.assertNotIn("activityType", changed["ambiguous"])
        self.assertNotIn("blockId", changed["digital"])
        self.assertEqual(copied["school_source"]["normalization_copy"]["changed_count"], 8)

    def test_plan_is_idempotent_after_copy(self):
        preview = normalization_preview(self.draft)
        copied = normalized_copy_payload(self.draft, preview)
        copied_draft = {**self.draft, "id": "copy", "revision": 1, "payload": copied}
        second = normalization_preview(copied_draft)
        self.assertEqual(second["automatic_count"], 0)
        self.assertEqual(second["manual_count"], 2)

    def test_realistic_357_row_preview_preserves_unclassified_rows(self):
        lessons = [{"id": f"ordinary-{i}", "subject": "Синтетический урок"} for i in range(345)]
        lessons.extend({"id": f"legacy-{block}-{i}", "blockId": f"source:{block}:electives",
                        "subject": f"Вариант {i}"} for block in ("B29", "F32") for i in range(4))
        lessons.extend({"id": f"digital-{i}", "activityType": "elective", "electiveDirection": "digital"} for i in range(4))
        preview = normalization_preview({"id": "357-fixture", "revision": 29,
            "payload": {"lessons": lessons}})
        self.assertEqual(preview["lesson_count"], 357)
        self.assertEqual(preview["automatic_count"], 8)
        self.assertEqual(preview["unclassified_count"], 345)
        self.assertEqual(preview["manual_count"], 4)

    def test_teacher_warning_is_cleared_only_with_exact_active_assignment_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "teacher-evidence.db")
            database.initialize()
            teacher = database.create_identity("teacher", "Synthetic Teacher")
            group = database.create_group("7A", "class")
            actor = database.get_or_create_user(991771, "admin")
            database.set_teacher_assignment(teacher["id"], group["id"], "История", True, actor["id"], base_class_name="7")
            row = {"id": "verified", "subject": "История", "teacherId": str(teacher["id"]),
                   "classId": str(group["id"]), "needsClarification": True,
                   "clarificationReason": "Нет подтверждённого назначения преподавателя группе"}
            identity_row = {"id": "identity-only", "subject": "Литература", "teacherId": str(teacher["id"]),
                "needsClarification": True, "clarificationReason": "Не подтверждён преподаватель"}
            unresolved_row = {"id": "unresolved-teacher", "subject": "Физика", "teacherId": "missing-teacher",
                "needsClarification": True, "clarificationReason": "Не подтверждён преподаватель"}
            draft = {"id": "evidence-template", "revision": 1, "payload": {"lessons": [row, identity_row, unresolved_row]}}
            active_teachers, evidence = verified_teacher_directory_evidence(database, draft)
            preview = normalization_preview(draft, verified_active_teachers=active_teachers, verified_teacher_assignments=evidence)
            self.assertEqual(preview["automatic_count"], 2)
            self.assertEqual(preview["retained_warning_count"], 1)
            self.assertEqual(preview["retained_warnings"][0]["count"], 1)
            self.assertEqual({item["field"] for item in preview["changes"]}, {"teacher_assignment_warning", "teacher_identity_warning"})
            copied = normalized_copy_payload(draft, preview)["lessons"]
            self.assertNotIn("needsClarification", copied[0])
            self.assertEqual(copied[0]["normalization_resolution"]["code"], "teacher_assignment_verified")
            self.assertNotIn("needsClarification", copied[1])
            self.assertEqual(copied[1]["normalization_resolution"]["code"], "teacher_identity_verified")
            self.assertTrue(row["needsClarification"])
            rejected = normalization_preview(draft, verified_teacher_assignments=set())
            self.assertEqual(rejected["automatic_count"], 0)


class ScheduleNormalizationApiTests(unittest.TestCase):
    def test_normalized_copy_preserves_trusted_history_audiences_and_all_unplanned_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "normalization.db")
            database.initialize()
            actor = database.get_or_create_user(778811, "admin")
            with database.connection() as connection:
                database.execute(connection, "INSERT INTO user_roles(user_id,role,source) VALUES (?,'admin','test')", (actor["id"],))
            challenge, _ = database.create_admin_login_challenge(actor["id"])
            token, _ = database.consume_admin_login_challenge(challenge)
            token = token[0] if isinstance(token, tuple) else token
            teacher = database.create_identity("teacher", "Evidence-backed Teacher")
            group = database.create_group("7A", "class")
            database.set_teacher_assignment(teacher["id"], group["id"], "История", True, actor["id"], base_class_name="7")
            old_decision = [{"id": "decision-1", "actor_id": "admin-42", "confirmed_at": "2026-10-01T10:00:00Z",
                             "reason": "Ранее подтверждено", "scope": "template"}]
            snapshot = {"student_ids": ["student-1", "student-2"], "fingerprint": "audience-fingerprint",
                        "resolved_at": "2026-10-02T10:00:00Z"}
            lessons = []
            for i in range(4):
                lesson = {"id": f"b29-{i}", "blockId": "source:B29:electives", "subject": f"Choice {i}",
                          "teacherId": f"teacher-{i}", "teacherIds": [f"teacher-{i}"],
                          "classId": str(group["id"]), "classIds": [str(group["id"])],
                          "groupIds": [str(group["id"])], "room": f"B-{i}",
                          "audience_snapshot": deepcopy(snapshot), "resolution_history": deepcopy(old_decision),
                          "saved_resolution": {"status": "accepted", "by": "admin-42"}}
                if i:
                    lesson["activityNameSnapshot"] = f"Choice {i}"
                lessons.append(lesson)
            lessons.extend([
                {"id": "teacher-assignment-warning", "subject": "История", "teacherId": str(teacher["id"]),
                 "teacherIds": [str(teacher["id"])], "classId": str(group["id"]), "classIds": [str(group["id"])],
                 "groupIds": [str(group["id"])], "room": "H-1", "needsClarification": True,
                 "clarificationReason": "Нет подтверждённого назначения преподавателя группе",
                 "audience_snapshot": deepcopy(snapshot), "resolution_history": deepcopy(old_decision)},
                {"id": "teacher-identity-warning", "subject": "Литература", "teacherId": str(teacher["id"]),
                 "teacherIds": [str(teacher["id"])], "classId": str(group["id"]), "classIds": [str(group["id"])],
                 "groupIds": [str(group["id"])], "room": "L-1", "needsClarification": True,
                 "clarificationReason": "Не подтверждён преподаватель", "resolution_history": deepcopy(old_decision)},
                {"id": "digital", "electiveDirection": "digital", "activityType": "elective", "subject": "digital",
                 "teacherId": "digital-teacher", "room": "D-1", "audience_snapshot": deepcopy(snapshot),
                 "resolution_history": deepcopy(old_decision)},
            ])
            payload = {"draft_kind": "school", "classes": [str(group["id"])], "lessons": lessons,
                       "school_source": {"source_ref": "verified-template"}}
            # Trusted source fixture represents an already reviewed school template.
            source = create_experiment(database, name="Synthetic source", source="import", payload=payload,
                                       _trusted_resolution_history=True)
            week_payload = {"draft_kind": "school", "week_start": "2026-11-09", "classes": [str(group["id"])],
                            "lessons": [{"id": "week-override", "subject": "История", "teacherId": "week-teacher",
                                         "teacherIds": ["week-teacher"], "room": "W-1"}]}
            week = create_experiment(database, name="Synthetic week", source="import", payload=week_payload,
                                     _trusted_resolution_history=True)
            original_week = deepcopy(get_experiment(database, week["id"])["payload"])
            app = FastAPI(); app.include_router(create_router(database)); client = TestClient(app)
            headers = {"X-Dev-Auth": f"browser:{token}", "X-School-Admin-Session": token}
            environment = {"MY_ISLAND_TEST_PROFILE": "consolidation", "MY_ISLAND_CONSOLIDATED_SCHEDULE": "1",
                           "MY_ISLAND_LOCAL_TEST_MODE": "true", "DATABASE_URL": ""}
            test_settings = SimpleNamespace(local_test_mode=True, app_env="development", database_url=None)
            with patch.dict("os.environ", environment, clear=False), \
                    patch("backend.services.consolidated_workspace.get_settings", return_value=test_settings), \
                    patch("backend.handlers.api.get_settings", return_value=test_settings):
                preview_response = client.post(f"/api/admin/schedule/experiments/school/{source['id']}/normalization-preview", headers=headers)
                self.assertEqual(preview_response.status_code, 200, preview_response.text)
                preview = preview_response.json()
                self.assertEqual(preview["automatic_count"], 6)
                self.assertEqual(preview["activity_type_change_count"], 4)
                self.assertEqual(preview["teacher_warning_clear_count"], 2)
                self.assertEqual(preview["manual_count"], 1)
                body = {"expected_revision": preview["source_revision"], "expected_source_hash": preview["source_hash"], "expected_plan_hash": preview["plan_hash"]}
                original = deepcopy(get_experiment(database, source["id"])["payload"])
                changed = dict(source["payload"]); changed["reference_date"] = "2026-11-19"
                save_experiment(database, source["id"], expected_revision=1, payload=changed)
                stale = client.post(f"/api/admin/schedule/experiments/school/{source['id']}/normalization-copy", headers=headers, json=body)
                self.assertEqual(stale.status_code, 409, stale.text)
                current = get_experiment(database, source["id"])
                active_teachers, verified_assignments = verified_teacher_directory_evidence(database, current)
                current_preview = normalization_preview(current, verified_active_teachers=active_teachers, verified_teacher_assignments=verified_assignments)
                valid_body = {"expected_revision": current_preview["source_revision"], "expected_source_hash": current_preview["source_hash"], "expected_plan_hash": current_preview["plan_hash"]}
                def publication_state():
                    snapshot = {}
                    with database.connection() as connection:
                        for table in ("school_template_selection", "schedule_publication_heads", "schedule_constructor_publications"):
                            exists = database.execute(connection, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                            snapshot[table] = ([tuple(row) for row in database.execute(connection, f"SELECT * FROM {table} ORDER BY 1").fetchall()]
                                               if exists else None)
                    return snapshot
                publication_before = publication_state()
                applied = client.post(f"/api/admin/schedule/experiments/school/{source['id']}/normalization-copy", headers=headers, json=valid_body)
                self.assertEqual(applied.status_code, 200, applied.text)
                self.assertEqual(publication_state(), publication_before)
                copied = get_experiment(database, applied.json()["id"])
                result = applied.json()
                self.assertEqual(result["changed_count"], 6)
                self.assertEqual(result["changed_type_count"], 4)
                self.assertEqual(result["cleared_teacher_warning_count"], 2)
                source_lessons = {row["id"]: row for row in original["lessons"]}
                copied_lessons = {row["id"]: row for row in copied["payload"]["lessons"]}
                self.assertEqual(set(copied_lessons), set(source_lessons))
                plan = {row["lesson_id"]: row for row in current_preview["changes"]}
                for lesson_id, before in source_lessons.items():
                    expected = deepcopy(before)
                    change = plan.get(lesson_id)
                    if change and change["field"] == "activity_type":
                        expected["activityType"] = "elective"
                        if "activityNameSnapshot" in change.get("companion_fields", []):
                            expected["activityNameSnapshot"] = str(expected.get("subject") or "").strip()
                    elif change and change["field"] in {"teacher_assignment_warning", "teacher_identity_warning"}:
                        expected.pop("needsClarification", None)
                        expected.pop("clarificationReason", None)
                        expected["normalization_resolution"] = {
                            "code": "teacher_assignment_verified" if change["field"] == "teacher_assignment_warning" else "teacher_identity_verified",
                            "source": "teacher_assignments" if change["field"] == "teacher_assignment_warning" else "identity_directory"}
                    self.assertEqual(copied_lessons[lesson_id], expected, f"unexpected fields changed for {lesson_id}")
                self.assertEqual(copied["payload"]["school_source"]["source_ref"], "verified-template")
                self.assertEqual(copied["payload"]["school_source"]["normalization_copy"]["change_plan"], [
                    {"lesson_id": row["lesson_id"], "fields": ([row["field"], *row.get("companion_fields", [])]
                     if row["field"] == "activity_type" else ["needsClarification", "clarificationReason", "normalization_resolution"])}
                    for row in current_preview["changes"]])
                self.assertEqual(get_experiment(database, source["id"])["payload"]["lessons"], original["lessons"])
                self.assertEqual(get_experiment(database, week["id"])["payload"], original_week)
                self.assertFalse(applied.json()["is_primary_template"])
                self.assertFalse(applied.json()["published"])
                # The normalizer's private trusted copy is not an import API capability.
                imported = client.post("/api/admin/schedule/experiments/school", headers=headers, json={
                    "name": "Untrusted import", "source": "import", "payload": {"lessons": [
                        {"id": "forged-history", "subject": "Not trusted", "resolution_history": old_decision}]}})
                self.assertEqual(imported.status_code, 200, imported.text)
                self.assertNotIn("resolution_history", imported.json()["payload"]["lessons"][0])


if __name__ == "__main__":
    unittest.main()
