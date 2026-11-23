from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from backend.services.constructor_publication import preview_publication
from backend.services import canonical_schedule as canonical
from backend.services.schedule_experiments import experiment_week_audience_preview, get_experiment, save_experiment
from backend.services.schedule_normalization import normalization_preview
from backend.tests import test_constructor_publication as fixtures

LEGACY_MATH = "Для этого предмета не подтверждена учебная группа; прежняя математическая группа удалена"
IDENTITY = "Не подтверждён преподаватель"
ASSIGNMENT = "Нет подтверждённого назначения преподавателя группе"
CLUB_NOTE = "Для этого предмета не подтверждена учебная группа"
UNKNOWN = "Неоднозначная исходная ячейка"


class OrdinarySaveValidationTests(TestCase):
    setUp = fixtures.ConstructorPublicationTests.setUp
    tearDown = fixtures.ConstructorPublicationTests.tearDown
    lesson = fixtures.ConstructorPublicationTests.lesson
    draft = fixtures.ConstructorPublicationTests.draft
    publish = fixtures.ConstructorPublicationTests.publish

    def scope(self, name, subject, student):
        group = self.database.create_group(f"{name} {uuid4().hex[:6]}", "subject_group", subject=subject, base_class_name="9A")
        self.database.create_membership(group["id"], student["id"], "student", "synthetic")
        return str(group["id"])

    def actor(self):
        return self.database.get_or_create_user(9900091, "admin")["id"]

    def stale_draft(self, *, note=LEGACY_MATH):
        old = self.scope("Synthetic Math 7.2", "Математика", self.student)
        target = self.scope("Synthetic Russian group", "Русский язык", self.other_student)
        lesson = self.lesson(id="stale", subject="Русский язык", groupId=old, groupIds=[old],
                             audience={"kind": "groups", "group_ids": [old]},
                             needsClarification=True, clarificationReason=note)
        frozen = self.lesson(id="frozen", subject="Physics", period=1, room="301",
                             audience={"kind": "students", "student_ids": [str(self.student["id"])]})
        draft = self.draft([lesson, frozen])
        # Match a real imported draft with an earlier valid/frozen roster record.
        saved = save_experiment(self.database, draft["id"], expected_revision=draft["revision"], payload=draft["payload"])
        return saved, target

    def edit(self, item, apply, *, explicit=()):
        payload = deepcopy(item["payload"])
        apply(payload["lessons"][0])
        return save_experiment(self.database, item["id"], expected_revision=item["revision"],
            payload=payload, actor_user_id=self.actor(), editor_audience_changes=list(explicit))

    def test_explicit_subject_replacement_verified_once_retains_control_and_audit(self):
        draft, target = self.stale_draft()
        frozen = deepcopy(draft["payload"]["lessons"][1])
        self.assertEqual(draft["payload"]["lessons"][0]["audience_snapshot"]["student_ids"], [str(self.student["id"])])
        prev = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        self.assertIn("stale_group_subject_mismatch", [row["code"] for row in prev["diagnostics"]])
        edit = lambda row: row.update(audience={"kind": "groups", "group_ids": [target]}, groupId=target, groupIds=[target])
        with patch("backend.services.schedule_experiments.experiment_week_audience_preview",
                   wraps=experiment_week_audience_preview) as resolver:
            saved = self.edit(draft, edit, explicit=["stale"])
            self.assertEqual(resolver.call_count, 1)
        self.assertEqual(saved["editor_validation"], [{"lesson_id": "stale", "status": "audience_verified", "participant_count": 1}])
        reopened = get_experiment(self.database, draft["id"])
        self.assertNotIn("editor_validation", reopened)
        row = reopened["payload"]["lessons"][0]
        self.assertNotIn("needsClarification", row)
        self.assertEqual(row["audience_snapshot"]["student_ids"], [str(self.other_student["id"])])
        self.assertEqual(row["edit_validation_history"][0]["before"], LEGACY_MATH)
        self.assertEqual(row["edit_validation_history"][0]["after"], "")
        self.assertEqual(row["edit_validation_history"][0]["actor"], str(self.actor()))
        self.assertEqual(reopened["payload"]["lessons"][1], frozen)
        publish_check = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=saved["revision"])
        self.assertNotIn("stale_group_subject_mismatch", [issue["code"] for issue in publish_check["diagnostics"]])
        self.assertEqual(publish_check["summary"]["ready_lessons"], 2)
        self.assertTrue(self.publish(saved)["runtime_readback"]["verified"])

    def test_explicit_class_preserves_other_reason_and_invalid_group_keeps_frozen_snapshot(self):
        draft, _ = self.stale_draft(note=LEGACY_MATH + "; " + UNKNOWN)
        invalid = self.scope("Synthetic archived", "Биология", self.other_student)
        self.database.archive_admin_group(invalid, self.actor(), confirm_used=True)
        with self.assertRaisesRegex(ValueError, "Новый состав не подтверждён"):
            self.edit(draft, lambda row: row.update(audience={"kind": "groups", "group_ids": [invalid]}), explicit=["stale"])
        saved = get_experiment(self.database, draft["id"])
        self.assertEqual(saved["revision"], draft["revision"])
        self.assertEqual(saved["payload"], draft["payload"])
        class_id = str(self.group["id"])
        changed = self.edit(saved, lambda row: row.update(audience={"kind": "classes", "class_ids": [class_id]}), explicit=["stale"])
        row = changed["payload"]["lessons"][0]
        self.assertEqual(changed["editor_validation"][0]["status"], "audience_verified")
        self.assertEqual(row["clarificationReason"], UNKNOWN)
        self.assertTrue(row["needsClarification"])

    def test_reaffirm_same_cross_subject_group_publishes_only_its_members(self):
        draft, _ = self.stale_draft()
        row = draft["payload"]["lessons"][0]
        saved_roster = deepcopy(row["audience_snapshot"])
        note_before = normalization_preview(draft)
        self.assertEqual(note_before["retained_warning_count"], 1)
        self.assertEqual(preview_publication(self.database, draft["id"], scope_kind="template",
            expected_revision=draft["revision"])["summary"]["deferred_lessons"], 1)
        edited = self.edit(draft, lambda row: None, explicit=["stale"])
        kept = edited["payload"]["lessons"][0]
        self.assertEqual(kept["subject"], "Русский язык")
        self.assertEqual(kept["audience"], row["audience"])
        self.assertEqual(kept["audience_snapshot"], saved_roster)
        self.assertEqual(kept["edit_validation_history"][0]["retired"], LEGACY_MATH.split("; "))
        self.assertEqual(normalization_preview(edited)["retained_warning_count"], 0)
        self.publish(edited)
        self.publish(edited, "week", week_start="2026-10-26")
        activities = lambda person: [a["activity"] for a in canonical.project_student(
            self.database, person["id"], "2026-10-26")["items"]]
        self.assertIn("Русский язык", activities(self.student))
        self.assertNotIn("Русский язык", activities(self.other_student))

    def test_explicit_different_cross_subject_scope_and_compound_warning(self):
        draft, _ = self.stale_draft(note=LEGACY_MATH + "; " + UNKNOWN)
        candidate = self.scope("Synthetic biological subgroup", "Биология", self.other_student)
        before = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        self.assertTrue(any(i["code"] == "review_required" and UNKNOWN in i["message"] for i in before["diagnostics"]))
        saved = self.edit(draft, lambda row: row.update(audience={"kind": "groups", "group_ids": [candidate]},
                                                     groupId=candidate, groupIds=[candidate]), explicit=["stale"])
        row = saved["payload"]["lessons"][0]
        self.assertEqual(row["audience"]["group_ids"], [candidate])
        self.assertEqual(row["audience_snapshot"]["student_ids"], [str(self.other_student["id"])])
        self.assertEqual(row["clarificationReason"], UNKNOWN)
        self.assertEqual([r["reason"] for r in normalization_preview(saved)["retained_warnings"]], [UNKNOWN])
        after = preview_publication(self.database, saved["id"], scope_kind="template", expected_revision=saved["revision"])
        codes = [r["code"] for r in after["diagnostics"]]
        self.assertNotIn("stale_group_subject_mismatch", codes)
        self.assertIn("review_required", codes)

    def test_reaffirming_old_scope_cannot_replace_frozen_members_after_drift(self):
        draft, _ = self.stale_draft()
        row = draft["payload"]["lessons"][0]
        self.database.create_membership(row["audience"]["group_ids"][0], self.other_student["id"], "student", "synthetic")
        saved = self.edit(draft, lambda row: None, explicit=["stale"])
        stored = saved["payload"]["lessons"][0]
        self.assertEqual(stored["audience_snapshot"], row["audience_snapshot"])
        self.assertNotIn("clarificationReason", stored)
        preview = preview_publication(self.database, saved["id"], scope_kind="template", expected_revision=saved["revision"])
        self.assertIn("audience_unresolved", [entry["code"] for entry in preview["diagnostics"]])
        self.assertEqual(preview["summary"]["deferred_lessons"], 1)

    def test_missing_archived_and_empty_ids_never_resolve_by_a_matching_name(self):
        for invalid_kind in ("missing", "archived", "empty"):
            with self.subTest(invalid_kind=invalid_kind):
                valid = self.scope("Active display namesake", "Русский язык", self.other_student)
                group_id = valid
                if invalid_kind == "missing":
                    # An imported display string is not a stable group ID.
                    group_id = "matching group display label"
                    with self.database.connection() as c:
                        self.database.execute(c, "UPDATE groups SET display_name=? WHERE id=?", (group_id, valid))
                elif invalid_kind == "archived":
                    self.database.archive_admin_group(valid, self.actor(), confirm_used=True)
                    namesake = self.scope("New active group", "Русский язык", self.student)
                    group_id = valid
                    with self.database.connection() as c:
                        self.database.execute(c, "UPDATE groups SET display_name='Archived namesake' WHERE id IN (?,?)", (valid, namesake))
                else:
                    with self.database.connection() as c:
                        self.database.execute(c, "UPDATE memberships SET active=FALSE WHERE group_id=?", (valid,))
                lesson = self.lesson(id="stale", subject="Русский язык", groupId=group_id, groupIds=[group_id],
                    audience={"kind":"groups","group_ids":[group_id]}, needsClarification=True, clarificationReason=LEGACY_MATH)
                draft = self.draft([lesson])
                with self.assertRaisesRegex(ValueError, "Новый состав не подтверждён"):
                    self.edit(draft, lambda row: None, explicit=["stale"])
                self.assertEqual(get_experiment(self.database, draft["id"])["revision"], draft["revision"])
                preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
                self.assertEqual(preview["summary"]["ready_lessons"], 0)
                self.assertIn("audience_unresolved", [entry["code"] for entry in preview["diagnostics"]])

    def test_cross_subject_group_reuse_still_reports_actual_student_overlap(self):
        group = self.scope("Reusable math split", "Математика", self.student)
        audience = {"kind": "groups", "group_ids": [group]}
        draft = self.draft([
            self.lesson(id="russian", subject="Русский язык", audience=audience),
            self.lesson(id="art", subject="Пластика", audience=audience,
                        teacherId=str(self.other_teacher["id"]), room="102"),
        ])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        self.assertFalse(any(row["code"] == "stale_group_subject_mismatch" for row in preview["diagnostics"]))
        conflicts = [row for row in preview["diagnostics"] if row["code"] == "student_overlap"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["affected_student_ids"], [str(self.student["id"])])

    def test_no_explicit_confirmation_does_not_change_legacy_or_frozen_members(self):
        draft, target = self.stale_draft()
        changed = self.edit(draft, lambda row: row.update(audience={"kind": "groups", "group_ids": [target]}))
        row = changed["payload"]["lessons"][0]
        self.assertEqual(row["clarificationReason"], LEGACY_MATH)
        self.assertEqual(row["audience_snapshot"]["student_ids"], [str(self.student["id"])])

    def test_club_false_hint_not_revived_and_other_flags_and_untyped_rows_survive(self):
        row = self.lesson(id="club", subject="Английский язык", kind="lesson", activityType="club",
                          needsClarification=True, clarificationReason=CLUB_NOTE + "; " + UNKNOWN)
        untyped = self.lesson(id="untyped", period=1, subject="Английский язык", kind="lesson",
                              needsClarification=True, clarificationReason=CLUB_NOTE)
        draft = self.draft([row, untyped])
        preview = normalization_preview(draft)
        self.assertEqual({row["reason"] for row in preview["retained_warnings"]}, {CLUB_NOTE, UNKNOWN})
        self.assertEqual(preview["retained_warning_count"], 2)
        changed = self.edit(draft, lambda lesson: lesson.update(room="204"))
        first, second = changed["payload"]["lessons"]
        self.assertEqual(first["clarificationReason"], UNKNOWN)
        self.assertEqual(first["edit_validation_history"][0]["evidence"]["activity_type"], "club")
        self.assertEqual(second["clarificationReason"], CLUB_NOTE)
        diagnostics = preview_publication(self.database, draft["id"], scope_kind="template",
                                          expected_revision=changed["revision"])["diagnostics"]
        self.assertFalse(any(row["lesson_id"] == "club" and row["code"] in {"audience_unresolved", "elective_block_unlinked"} for row in diagnostics))

    def test_only_verified_teacher_evidence_clears_exact_clauses_on_touched_lesson(self):
        class_id, teacher = str(self.group["id"]), str(self.teacher["id"])
        lesson = self.lesson(subject="Русский язык", needsClarification=True,
                             clarificationReason=f"{IDENTITY}; {ASSIGNMENT}; {UNKNOWN}")
        draft = self.draft([lesson])
        with self.database.connection() as connection:
            self.database.execute(connection, """INSERT INTO teacher_assignments(teacher_identity_id,group_id,subject,source,active)
                VALUES (?,?,'Русский язык','admin_override',TRUE)""", (teacher, class_id))
        saved = self.edit(draft, lambda row: row.update(room="205"))
        row = saved["payload"]["lessons"][0]
        self.assertEqual(row["clarificationReason"], UNKNOWN)
        self.assertEqual(row["edit_validation_history"][0]["evidence"]["teacher_directory"], "active_subject_assignment")
        draft = self.draft([lesson])
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE teacher_assignments SET valid_from='2026-10-20' WHERE group_id=?", (class_id,))
        saved = self.edit(draft, lambda row: row.update(room="205"))
        self.assertEqual(saved["payload"]["lessons"][0]["clarificationReason"], f"{ASSIGNMENT}; {UNKNOWN}")

    def test_scoped_save_only_seeds_the_edited_and_overlapping_dependent_rosters(self):
        class_id = str(self.group["id"])
        old_group = self.scope("Old Math subset", "Математика", self.student)
        new_group = self.scope("Russian subset", "Русский язык", self.other_student)
        stale = self.lesson(id="stale", subject="Русский язык", groupId=old_group, groupIds=[old_group],
                           audience={"kind": "groups", "group_ids": [old_group]}, needsClarification=True,
                           clarificationReason=LEGACY_MATH)
        dependent = self.lesson(id="remainder", subject="Геометрия", needsClarification=True, clarificationReason=UNKNOWN,
                                audience={"kind": "available_slot", "class_ids": [class_id]})
        fixed = self.lesson(id="fixed", subject="Практика", audience={"kind": "students", "student_ids": []},
                            needsClarification=True, clarificationReason=UNKNOWN)
        unrelated = self.lesson(id="unrelated", subject="Физика", period=1, room="301",
                                audience={"kind": "classes", "class_ids": [class_id]},
                                needsClarification=True, clarificationReason=UNKNOWN)
        draft = self.draft([stale, dependent, fixed, unrelated])
        edits = lambda row: row.update(audience={"kind": "groups", "group_ids": [new_group]}, groupId=new_group, groupIds=[new_group])
        # A pre-existing confirmed scope may release a different pupil than
        # the new rule claims. Keep that case covered without pre-populating
        # snapshots for the other three lessons.
        payload = deepcopy(draft["payload"])
        payload["lessons"][0]["audience_snapshot"] = {
            "student_ids": [str(self.student["id"])], "fingerprint": "old-scope", "resolved_at": "2026-10-19T08:00:00+00:00",
        }
        from backend.services.schedule_experiments import _write_experiment_revision
        draft = _write_experiment_revision(self.database, draft["id"], draft["revision"], payload)
        saved = self.edit(draft, edits, explicit=["stale"])
        reopened = get_experiment(self.database, saved["id"])
        before = {row["id"]: row for row in draft["payload"]["lessons"]}
        after = {row["id"]: row for row in reopened["payload"]["lessons"]}
        self.assertEqual(after["stale"]["audience_snapshot"]["student_ids"], [str(self.other_student["id"])])
        self.assertNotIn("needsClarification", after["stale"])
        self.assertEqual(after["remainder"].pop("audience_snapshot")["student_ids"], [str(self.student["id"])])
        self.assertEqual(after["remainder"], before["remainder"])
        self.assertEqual(after["unrelated"], before["unrelated"])
        self.assertEqual(after["fixed"], before["fixed"])
