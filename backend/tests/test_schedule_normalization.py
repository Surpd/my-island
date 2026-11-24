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
from backend.services.schedule_normalization import normalization_preview, normalized_copy_payload, verified_administrative_classes, verified_teacher_directory_evidence


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

    def test_day_mode_retained_warnings_are_projection_only(self):
        room = "Кабинет не указан"
        staff = "Не подтверждён преподаватель"
        common = {"needsClarification": True, "day": 4, "subject": "Synthetic"}
        lessons = [
            {**common, "id": "zd", "classIds": ["class-10"], "clarificationReason": f"{room}; {staff}"},
            {**common, "id": "mixed", "classIds": ["class-10", "unknown-class"], "clarificationReason": room},
            {**common, "id": "remote", "classIds": ["class-9"], "format": "online",
             "clarificationReason": f"{room}; {staff}"},
            {**common, "id": "import", "classIds": ["class-10"], "clarificationReason": room, "importError": True},
        ]
        draft = {"id": "modes", "payload": {"grade_modes": {"10": {"4": "sdep"}}, "lessons": lessons}}
        preview = normalization_preview(draft, verified_classes={"class-9": "9", "class-10": "10"})
        self.assertEqual({item["reason"]: item["count"] for item in preview["retained_warnings"]}, {room: 2, staff: 1})
        self.assertEqual(normalized_copy_payload(draft, preview)["lessons"], lessons)

    def test_retained_warning_preview_includes_every_row_for_normal_review(self):
        reasons = [f"Старая пометка {index}" for index in range(4)]
        lessons = [
            {"id": f"legacy-{index}", "subject": f"Занятие {index}", "needsClarification": True,
             "clarificationReason": reasons[index % len(reasons)]}
            for index in range(51)
        ]
        preview = normalization_preview({"id": "legacy-review", "revision": 1, "payload": {"lessons": lessons}})

        self.assertEqual(preview["retained_warning_count"], 51)
        self.assertEqual(sum(len(group["examples"]) for group in preview["retained_warnings"]), 51)
        self.assertTrue(all(group["count"] == len(group["examples"]) for group in preview["retained_warnings"]))
        self.assertEqual(
            {example["lesson_id"] for group in preview["retained_warnings"] for example in group["examples"]},
            {lesson["id"] for lesson in lessons},
        )

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


    def test_class_scope_retires_exact_lunch_clause_and_not_other_warnings(self):
        from backend.services.schedule_normalization import LUNCH_SCOPE_CLAUSE
        marker = {"specialMark": "lunch", "classIds": ["7a", "7b"], "day": 1, "period": 0, "needsClarification": True,
                  "clarificationReason": LUNCH_SCOPE_CLAUSE}
        rows = [
            {**marker, "id": "exact"},
            {**marker, "id": "combo", "clarificationReason": LUNCH_SCOPE_CLAUSE + "; Нет соответствующего звонка",
             "normalization_resolution": {"code": "prior_evidence"}},
            {**marker, "id": "unknown", "classIds": ["7a", "unknown"]},
            {**marker, "id": "group", "groupId": "math-group"},
            {**marker, "id": "rule", "audience": {"kind": "students", "student_ids": ["synthetic"]}},
            {**marker, "id": "conflict", "classId": "6a"},
            {**marker, "id": "import-error", "importError": True},
            {**marker, "id": "not-lunch", "specialMark": "", "subject": "Математика"},
            {**marker, "id": "no-class", "classIds": []},
            {**marker, "id": "no-time", "period": 1},
            {**marker, "id": "exceptions", "excludedStudentIds": ["synthetic"]},
        ]
        draft = {"revision": 1, "payload": {"lessons": rows, "slots": [{"start": "12:30", "end": "13:00"}]}}
        classes = {"7a": "7", "7b": "7"}
        preview = normalization_preview(draft, verified_classes=classes)
        self.assertEqual(preview["lunch_scope_warning_suppressed_count"], 2)
        self.assertEqual(preview["automatic_count"], 0)
        self.assertEqual(preview["retained_warning_count"], 10)
        self.assertEqual([p["lesson_id"] for p in preview["scope_proposals"]], ["exact", "combo"])
        copied = normalized_copy_payload(draft, preview)["lessons"]
        self.assertEqual(copied, rows)
        self.assertTrue(any(item["reason"] == "Нет соответствующего звонка" for item in preview["retained_warnings"]))
        self.assertEqual(rows[0]["clarificationReason"], LUNCH_SCOPE_CLAUSE)


    def test_elective_legacy_roster_clause_requires_valid_class_block_and_not_a_roster(self):
        from backend.services.schedule_normalization import ELECTIVE_ROSTER_CLAUSES
        reason = "Состав направления курса по выбору не подтверждён"
        self.assertIn(reason, ELECTIVE_ROSTER_CLAUSES)
        shared = {"activityType": "elective", "classIds": ["7a"], "eligibleGrades": [7], "day": 0, "period": 0,
                  "needsClarification": True, "clarificationReason": reason}
        rows = [
            {**shared, "id": "linked", "blockId": "valid"},
            {**shared, "id": "linked-again", "blockId": "valid", "classIds": ["7b"],
             "clarificationReason": reason + "; Не подтверждён преподаватель"},
            {**shared, "id": "missing-block", "electiveDirection": "digital"},
            {**shared, "id": "mixed", "blockId": "mixed"},
            {**shared, "id": "ordinary", "blockId": "mixed", "activityType": "lesson"},
            {**shared, "id": "cross-slot", "blockId": "cross-slot"},
            {**shared, "id": "other-slot", "blockId": "cross-slot", "period": 1},
            {**shared, "id": "wrong-grade", "blockId": "wrong-grade", "eligibleGrades": [8]},
            {**shared, "id": "bad-day", "blockId": "bad-day", "day": None},
        ]
        draft = {"revision": 1, "payload": {"lessons": rows, "slots": [
            {"start": "09:00", "end": "09:45"}, {"start": "10:00", "end": "10:45"}]}}
        preview = normalization_preview(draft, verified_classes={"7a": "7", "7b": "7"})
        self.assertEqual(preview["elective_scope_warning_clear_count"], 2)
        problems = {item["lesson_id"]: item["reason"] for item in preview["manual"]}
        self.assertIn("не связано", problems["missing-block"])
        self.assertIn("другого типа", problems["mixed"])
        self.assertIn("разных временных", problems["cross-slot"])
        self.assertIn("допустимым параллелям", problems["wrong-grade"])
        copied = normalized_copy_payload(draft, preview)["lessons"]
        self.assertNotIn("needsClarification", copied[0])
        self.assertNotIn("confirmedParticipantIds", copied[0])
        self.assertEqual(copied[1]["clarificationReason"], "Не подтверждён преподаватель")
        self.assertEqual(copied[2:], rows[2:])

    def test_math_and_vibe_only_proposes_availability_and_changes_no_payload(self):
        elective = {"id": "target", "subject": "Математика + вайб", "activityType": "elective",
                    "day": 0, "period": 0, "blockId": "parallel-56", "classIds": ["5a"],
                    "eligibleGrades": [5], "teacherId": "original", "confirmedParticipantIds": ["test-student"]}
        peer = {**elective, "id": "peer", "subject": "Other option"}
        other_name = {**elective, "id": "unrelated", "subject": "Математика + вайб 2"}
        unknown = {**elective, "id": "unknown", "blockId": ""}
        outside = {**elective, "id": "outside", "blockId": "other-block", "eligibleGrades": [5, 7]}
        rows = [elective, peer, other_name, unknown, outside]
        draft = {"revision": 1, "payload": {"lessons": rows, "slots": [{"start": "10:30", "end": "11:15"}]}}
        preview = normalization_preview(draft, verified_classes={"5a": "5", "5b": "5", "6a": "6", "7a": "7"})
        proposals = {p["lesson_id"]: p for p in preview["scope_proposals"]}
        self.assertEqual(set(proposals), {"target", "unknown", "outside"})
        self.assertEqual(proposals["target"]["eligible_grades"], [5, 6])
        self.assertEqual(proposals["target"]["problem"], "")
        self.assertIn("общий блок", proposals["unknown"]["problem"])
        self.assertIn("выходит за рамки", proposals["outside"]["problem"])
        self.assertEqual(preview["automatic_count"], 0)
        self.assertEqual(normalized_copy_payload(draft, preview)["lessons"], rows)

    def test_class_evidence_excludes_archived_and_instructional_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "class-evidence.db")
            database.initialize()
            active = database.create_group("7A", "class")
            archived = database.create_group("9B", "class")
            database.create_group("8C", "elective")
            with database.connection() as connection:
                database.execute(connection, "UPDATE groups SET archived_at=? WHERE id=?", ("2026-11-20", archived["id"]))
            self.assertEqual(verified_administrative_classes(database), {str(active["id"]): "7"})


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
                with database.connection() as connection:
                    database.execute(connection, """INSERT INTO school_template_selection
                        (scope, draft_id, source_draft_id, source_revision, source_payload_sha256)
                        VALUES ('school', ?, ?, ?, ?)""",
                        (source["id"], source["id"], preview["source_revision"], preview["source_hash"]))
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
                path = f"/api/admin/schedule/experiments/school/{source['id']}/normalization-apply"
                selected = {"lesson_id": "teacher-assignment-warning", "field": "teacher_assignment_warning"}
                mixed = {"lesson_id": "digital", "field": "elective_scope_warning"}
                before = get_experiment(database, source["id"])
                denied = client.post(path, json={**valid_body, "selected": [selected]})
                self.assertIn(denied.status_code, {401, 403})
                unsafe = client.post(path, headers=headers, json={**valid_body, "selected": [selected, mixed]})
                self.assertEqual(unsafe.status_code, 422, unsafe.text)
                self.assertEqual(get_experiment(database, source["id"]), before)
                stale = client.post(path, headers=headers, json={**valid_body, "expected_plan_hash": "replaced", "selected": [selected]})
                self.assertEqual(stale.status_code, 409, stale.text)
                self.assertEqual(get_experiment(database, source["id"]), before)
                safe = client.post(path, headers=headers, json={**valid_body, "selected": [selected]})
                self.assertEqual(safe.status_code, 200, safe.text)
                persisted = get_experiment(database, source["id"])
                self.assertEqual(persisted["revision"], before["revision"] + 1)
                expected = deepcopy(before["payload"])
                teacher_row = next(row for row in expected["lessons"] if row["id"] == selected["lesson_id"])
                teacher_row.pop("clarificationReason")
                teacher_row.pop("needsClarification")
                self.assertEqual(persisted["payload"], expected)
                self.assertEqual(publication_state(), publication_before)
                repeated = client.post(path, headers=headers, json={**valid_body, "selected": [selected]})
                self.assertEqual(repeated.status_code, 409, repeated.text)
                self.assertEqual(get_experiment(database, source["id"]), persisted)



    def test_apply_notes_round_trip_refuses_unknown_and_preserves_all_other_fields(self):
        from backend.services.schedule_normalization import apply_selected_schedule_notes
        from backend.services.schedule_experiments import ExperimentConflict
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "notes.db")
            database.initialize()
            group = database.create_group("7A", "class")
            with database.connection() as connection:
                database.execute(connection, "UPDATE groups SET canonical=1,base_class_name='7A' WHERE id=?", (group["id"],))
            teacher = database.create_identity("teacher", "Synthetic")
            mark = "Состав направления курса по выбору не подтверждён"
            rows = [
                {"id": "elective", "subject": "Course", "activityType": "elective", "blockId": "known-block",
                 "day": 2, "period": 0, "classIds": [str(group["id"])], "eligibleGrades": [7],
                 "confirmedParticipantIds": ["fixture"], "needsClarification": True,
                 "clarificationReason": mark + "; Нет соответствующего звонка"},
                {"id": "teacher", "subject": "History", "teacherId": str(teacher["id"]),
                 "needsClarification": True, "clarificationReason": "Не подтверждён преподаватель"},
                {"id": "unknown", "subject": "History", "teacherId": "missing",
                 "needsClarification": True, "clarificationReason": "Не подтверждён преподаватель"},
            ]
            draft = create_experiment(database, name="Synthetic working copy", source="import",
                payload={"draft_kind": "school", "classes": [str(group["id"])], "lessons": rows,
                         "days": [2], "slots": [{"start": "14:35", "end": "15:20"}]})
            draft = get_experiment(database, draft["id"])
            active, assignments = verified_teacher_directory_evidence(database, draft)
            plan = normalization_preview(draft, verified_active_teachers=active, verified_teacher_assignments=assignments,
                                         verified_classes=verified_administrative_classes(database))
            guard = {"expected_revision": 1, "expected_source_hash": plan["source_hash"], "expected_plan_hash": plan["plan_hash"]}
            selected = {"lesson_id": "elective", "field": "elective_scope_warning"}
            invalid = {"lesson_id": "unknown", "field": "teacher_identity_warning"}
            with self.assertRaises(ValueError):
                apply_selected_schedule_notes(database, database, draft["id"], selected=[selected, invalid], **guard)
            self.assertEqual(get_experiment(database, draft["id"]), draft)
            result = apply_selected_schedule_notes(database, database, draft["id"], selected=[selected], **guard)
            expected = deepcopy(draft["payload"])
            expected["lessons"][0]["clarificationReason"] = "Нет соответствующего звонка"
            self.assertEqual(result["draft"]["payload"], expected)
            self.assertEqual(result["draft"]["id"], draft["id"])
            self.assertEqual(result["draft"]["revision"], 2)
            self.assertEqual(result["cleared_count"], 1)
            with self.assertRaises(ExperimentConflict):
                apply_selected_schedule_notes(database, database, draft["id"], selected=[selected], **guard)
            current = result["draft"]
            plan = normalization_preview(current, verified_active_teachers=active, verified_teacher_assignments=assignments,
                                         verified_classes=verified_administrative_classes(database))
            with database.connection() as connection:
                database.execute(connection, """INSERT INTO school_template_selection
                    (scope, draft_id, source_draft_id, source_revision, source_payload_sha256) VALUES ('school', ?, ?, ?, ?)""",
                    (current["id"], current["id"], guard["expected_revision"], guard["expected_source_hash"]))
            result = apply_selected_schedule_notes(database, database, draft["id"], expected_revision=current["revision"],
                expected_source_hash=plan["source_hash"], expected_plan_hash=plan["plan_hash"],
                selected=[{"lesson_id": "teacher", "field": "teacher_identity_warning"}])
            expected = deepcopy(current["payload"])
            expected["lessons"][1].pop("clarificationReason")
            expected["lessons"][1].pop("needsClarification")
            self.assertEqual(result["draft"]["payload"], expected)
            self.assertEqual(result["draft"]["revision"], current["revision"] + 1)
            with database.connection() as connection:
                selection = database.execute(connection, """SELECT source_revision,source_payload_sha256
                    FROM school_template_selection WHERE scope='school'""").fetchone()
            self.assertEqual(int(selection["source_revision"]), guard["expected_revision"])
            self.assertEqual(str(selection["source_payload_sha256"]), guard["expected_source_hash"])


if __name__ == "__main__":
    unittest.main()
