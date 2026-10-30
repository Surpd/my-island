import tempfile
import unittest
from datetime import date
from pathlib import Path
from uuid import uuid4

from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact, canonical_version
from backend.services.schedule_experiments import (
    ExperimentConflict, ExperimentNotFound, create_experiment,
    delete_experiment, duplicate_experiment, get_experiment, list_experiments,
    save_experiment, experiment_audience_preview,
    _lesson_interval,
)
from backend.services.school_experiment_workspace import cached_audience_snapshot, school_directory


class LessonIntervalTests(unittest.TestCase):
    def test_explicit_long_lesson_uses_its_end_time(self):
        slots = [{"start": "09:00", "end": "09:45"}, {"start": "09:55", "end": "10:40"},
                 {"start": "10:55", "end": "11:40"}]
        self.assertEqual(_lesson_interval({"kind": "lesson", "period": 0,
                                           "start": "09:00", "end": "11:40"}, slots), (540, 700))
        self.assertEqual(_lesson_interval({"kind": "lesson", "period": 0,
                                           "start": "09:00", "end": "09:45"}, slots), (540, 585))


class ScheduleExperimentTests(unittest.TestCase):
    def test_list_uses_explicit_template_selection_instead_of_legacy_role_tags(self):
        old = create_experiment(self.database, name="Old role-tagged copy", source="empty", payload={"school_source": {"role": "primary_school_template"}})
        selected = create_experiment(self.database, name="Selected editor template", source="empty", payload={"draft_kind": "school"})
        with self.database.connection() as connection:
            connection.execute("INSERT INTO school_template_selection(scope,draft_id,source_draft_id,source_revision,source_payload_sha256) VALUES ('school',?,?,1,'fixture')", (selected["id"], selected["id"]))
        listing = {item["id"]: item for item in list_experiments(self.database)}
        self.assertTrue(listing[selected["id"]]["is_primary_template"])
        self.assertFalse(listing[old["id"]]["is_primary_template"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "experiments.db")
        self.database.initialize()
        self.class_group = self.database.create_group("9A", "class")
        self.other_class_group = self.database.create_group("9B", "class")
        self.parallel_group = self.database.create_group("9 класс", "class")
        self.subgroup = self.database.create_group("9A English", "instructional")
        self.teacher = self.database.create_identity("teacher", "Teacher")
        self.version_id = "canonical-test-v1"
        import_canonical_artifact(self.database, {
            "schema_version": "test-v1", "version_id": self.version_id,
            "source_snapshot": {"id": "source", "fingerprint": "fingerprint"},
            "blocks": {"monday-1": {
                "weekday": 0, "slot": {"start": "09:00", "end": "09:45"},
                "grade_scope": "9", "status": "resolved", "mode": "SINGLE",
                "assignments": [{"activity": "Английский", "audience": {"type": "canonical_groups", "canonical_group_ids": [str(self.class_group["id"]), str(self.subgroup["id"])]}, "teacher_ids": [str(self.teacher["id"])], "metadata": {"room": "301"}}],
            }, "monday-2": {
                "weekday": 0, "slot": {"start": "09:55", "end": "10:40"},
                "grade_scope": "9", "status": "resolved", "mode": "SINGLE",
                "assignments": [{"activity": "Unmapped group lesson", "audience": {"type": "canonical_groups", "canonical_group_ids": [str(self.subgroup["id"])]}, "teacher_ids": []}],
            }},
        }, status="approved_baseline")

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_experiment_persists_and_revision_cas_is_enforced(self):
        created = create_experiment(self.database, name="Черновик", source="empty")
        self.assertEqual(created["revision"], 1)
        self.assertIsNone(created["base_version_id"])
        self.assertEqual(created["payload"]["days"], [0, 1, 2, 3, 4])
        self.assertEqual(len(created["payload"]["slots"]), 8)
        self.assertEqual(set(created["payload"]["classes"]), {str(self.class_group["id"]), str(self.other_class_group["id"]), str(self.parallel_group["id"])})
        saved = save_experiment(self.database, created["id"], expected_revision=1, name="Изменённый", payload={
            "days": [0], "slots": [{"start": "09:00", "end": "09:45"}], "classes": ["9A"],
            "lessons": [{"weekday": 0, "activity": "Математика", "teacher_ids": []}],
        })
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["name"], "Изменённый")
        self.assertEqual(get_experiment(self.database, created["id"])["payload"]["lessons"][0]["activity"], "Математика")
        self.assertEqual([item["id"] for item in list_experiments(self.database)], [created["id"]])
        with self.assertRaises(ExperimentConflict):
            save_experiment(self.database, created["id"], expected_revision=1, payload={"lessons": []})

    def test_remote_admin_uuid_does_not_break_local_sqlite_drafts(self):
        actor_id = uuid4()
        created = create_experiment(self.database, name="School draft", source="empty", actor_user_id=actor_id)
        saved = save_experiment(self.database, created["id"], expected_revision=1,
                                payload=created["payload"], actor_user_id=actor_id)
        copied = duplicate_experiment(self.database, created["id"], actor_user_id=actor_id)
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(copied["revision"], 1)

    def test_canonical_experiment_snapshots_lessons_without_mutating_baseline(self):
        before = canonical_version(self.database)
        experiment = create_experiment(self.database, name="Копия", source="canonical")
        self.assertEqual(experiment["base_version_id"], self.version_id)
        self.assertIn(str(self.class_group["id"]), experiment["payload"]["classes"])
        lesson = experiment["payload"]["lessons"][0]
        self.assertEqual(lesson["subject"], "Английский")
        self.assertEqual(lesson["day"], 0)
        self.assertEqual(lesson["period"], 0)
        self.assertEqual(lesson["classId"], str(self.class_group["id"]))
        self.assertEqual(lesson["groupId"], str(self.subgroup["id"]))
        self.assertEqual(set(lesson["groupIds"]), {str(self.class_group["id"]), str(self.subgroup["id"])})
        self.assertEqual(lesson["teacherId"], str(self.teacher["id"]))
        self.assertEqual(lesson["room"], "301")
        self.assertTrue(lesson["canonical"]["block_key"])
        grade_scoped = next(item for item in experiment["payload"]["lessons"] if item["subject"] == "Unmapped group lesson")
        self.assertEqual(grade_scoped["classId"], str(self.parallel_group["id"]))
        self.assertEqual(grade_scoped["grade_scope"], "9")
        self.assertEqual(canonical_version(self.database), before)

    def test_missing_experiment_and_invalid_payload_are_reported(self):
        with self.assertRaises(ExperimentNotFound):
            get_experiment(self.database, "missing")
        with self.assertRaises(ValueError):
            create_experiment(self.database, name="bad", source="empty", payload={"days": [7]})

    def test_canonical_copy_preserves_parallel_audiences_groups_remainders_and_bell_slots(self):
        seven_a = self.database.create_group("7A", "class")
        seven_b = self.database.create_group("7B", "class")
        ten_a = self.database.create_group("10A", "class")
        eleven_a = self.database.create_group("11A", "class")
        math_a = self.database.create_group("9A math", "instructional")
        math_b = self.database.create_group("9B math", "instructional")
        english = self.database.create_group("10-11 English", "instructional")
        students = [self.database.create_identity("student", f"Student {index}") for index in range(5)]
        for student, base_class, audience in [
            (students[0], self.class_group, math_a),
            (students[1], self.other_class_group, math_b),
            (students[2], ten_a, english),
            (students[3], eleven_a, english),
            (students[4], seven_a, seven_a),
        ]:
            self.database.create_membership(base_class["id"], student["id"], "student", "test")
            if audience["id"] != base_class["id"]:
                self.database.create_membership(audience["id"], student["id"], "student", "test")
        version_id = "zz-canonical-complex-v1"
        import_canonical_artifact(self.database, {
            "schema_version": "test-v1", "version_id": version_id,
            "source_snapshot": {"id": "complex", "fingerprint": "complex-fingerprint"},
            "blocks": {
                "monday-math": {"weekday": 0, "slot": {"start": "09:55", "end": "10:40"}, "grade_scope": "9", "assignments": [
                    {"activity": "Математика", "audience": {"type": "canonical_groups", "canonical_group_ids": [str(math_a["id"]), str(math_b["id"])]}, "teacher_ids": []},
                ]},
                "monday-english": {"weekday": 0, "slot": {"start": "12:45", "end": "13:30"}, "grade_scope": "10-11", "assignments": [
                    {"activity": "Английский", "audience": {"type": "canonical_groups", "canonical_group_ids": [str(english["id"])]}, "teacher_ids": []},
                ]},
                "tuesday-remainder": {"weekday": 1, "slot": {"start": "09:00", "end": "09:45"}, "grade_scope": "7", "assignments": [
                    {"activity": "Самостоятельная работа", "role": "residual", "audience": {"type": "remaining", "canonical_group_ids": []}, "metadata": {"audience_rule": {"kind": "remaining", "grade": "7", "class_group_id": str(seven_a["id"]), "partition_group_ids": [str(seven_b["id"])]}}, "teacher_ids": []},
                ]},
            },
        }, status="approved_baseline")
        published_before = canonical_version(self.database)
        experiment = create_experiment(self.database, name="Сложная копия", source="canonical")
        lessons = {lesson["subject"]: lesson for lesson in experiment["payload"]["lessons"]}
        math = lessons["Математика"]
        self.assertEqual(set(math["groupIds"]), {str(math_a["id"]), str(math_b["id"])})
        self.assertEqual(set(math["classIds"]), {str(self.class_group["id"]), str(self.other_class_group["id"])})
        self.assertEqual(math["classId"], "")
        self.assertEqual(math["period"], 1)
        english_lesson = lessons["Английский"]
        self.assertEqual(set(english_lesson["classIds"]), {str(ten_a["id"]), str(eleven_a["id"])})
        self.assertEqual(english_lesson["period"], 4)
        remainder = lessons["Самостоятельная работа"]
        self.assertEqual(remainder["audienceMode"], "remainder")
        self.assertEqual(remainder["excludedGroupIds"], [str(seven_b["id"])])
        self.assertEqual(remainder["classIds"], [str(seven_a["id"])])
        self.assertTrue(remainder["canonical"]["assignment"].get("metadata"))
        self.assertEqual(experiment["transfer_result"], {"transferred": 3, "needs_clarification": 0, "import_errors": 0})
        self.assertEqual(canonical_version(self.database), published_before)

    def test_unmapped_ids_and_missing_times_remain_separate_and_draft_actions_are_scoped(self):
        version_id = "zzzz-canonical-import-problems-v1"
        import_canonical_artifact(self.database, {
            "schema_version": "test-v1", "version_id": version_id,
            "source_snapshot": {"id": "problematic", "fingerprint": "problem-fingerprint"},
            "blocks": {
                "unknown-group": {"weekday": 0, "slot": {"start": "09:00", "end": "09:45"}, "grade_scope": "9", "assignments": [
                    {"activity": "Английский", "audience": {"type": "canonical_groups", "canonical_group_ids": ["missing-canonical-id"]}, "teacher_ids": []},
                ]},
                "missing-time": {"weekday": 1, "grade_scope": "9", "assignments": [
                    {"activity": "Математика", "audience": {"type": "canonical_groups", "canonical_group_ids": [str(self.class_group["id"])]}, "teacher_ids": []},
                ]},
            },
        }, status="approved_baseline")
        experiment = create_experiment(self.database, name="Проблемная копия", source="canonical")
        self.assertEqual(experiment["transfer_result"], {"transferred": 0, "needs_clarification": 1, "import_errors": 1})
        lessons = experiment["payload"]["lessons"]
        unknown = next(item for item in lessons if item["subject"] == "Английский")
        self.assertEqual(unknown["groupIds"], ["missing-canonical-id"])
        self.assertTrue(unknown["importError"])
        self.assertTrue(unknown["needsClarification"])
        missing_time = next(item for item in lessons if item["subject"] == "Математика")
        self.assertEqual(missing_time["period"], -1)
        self.assertFalse(missing_time["importError"])
        self.assertTrue(missing_time["needsClarification"])

        renamed = save_experiment(self.database, experiment["id"], expected_revision=1, name="Переименован", payload=experiment["payload"])
        copied = duplicate_experiment(self.database, renamed["id"])
        self.assertEqual(copied["base_version_id"], experiment["base_version_id"])
        self.assertEqual(copied["payload"], experiment["payload"])
        published_before_delete = canonical_version(self.database)
        delete_experiment(self.database, copied["id"])
        with self.assertRaises(ExperimentNotFound):
            get_experiment(self.database, copied["id"])
        self.assertEqual(canonical_version(self.database), published_before_delete)

    def test_experimental_audience_resolves_interval_remainder_and_recalculates(self):
        class_id = str(self.class_group["id"])
        english_id = str(self.subgroup["id"])
        students = [self.database.create_identity("student", f"Pupil {index}") for index in range(30)]
        for index, student in enumerate(students):
            self.database.create_membership(class_id, student["id"], "student", "test")
            if index < 10:
                self.database.create_membership(english_id, student["id"], "student", "test")
        payload = {"reference_date": "2026-10-13", "slots": [{"start": "10:30", "end": "11:30"}, {"start": "11:35", "end": "12:20"}], "lessons": [
            {"id": "english", "day": 0, "period": 0, "audience": {"kind": "groups", "group_ids": [english_id]}},
            {"id": "russian", "day": 0, "period": 0, "audience": {"kind": "available_slot", "class_ids": [class_id]}},
        ]}
        before = canonical_version(self.database)
        result = experiment_audience_preview(self.database, payload)["lessons"]
        self.assertEqual(result["russian"]["count"], 20)
        self.assertEqual(result["russian"]["excluded_count"], 10)
        payload["lessons"][0]["period"] = 1
        self.assertEqual(experiment_audience_preview(self.database, payload)["lessons"]["russian"]["count"], 30)
        payload["lessons"][0].update({"kind": "event", "start": "10:55", "end": "12:00"})
        self.assertEqual(experiment_audience_preview(self.database, payload)["lessons"]["russian"]["count"], 20)
        payload["lessons"].append({"id": "notice", "kind": "event", "informational": True, "day": 0,
                                   "period": 0, "classIds": [class_id], "start": "10:30", "end": "11:30"})
        self.assertEqual(experiment_audience_preview(self.database, payload)["lessons"]["russian"]["count"], 20)
        payload["lessons"].append({"id": "second-remainder", "day": 0, "period": 0,
                                   "audience": {"kind": "available_slot", "class_ids": [class_id]}})
        result = experiment_audience_preview(self.database, payload)["lessons"]
        self.assertEqual(result["russian"]["status"], "ambiguous")
        self.assertEqual(result["second-remainder"]["status"], "ambiguous")
        self.assertEqual(result["russian"]["student_ids"], [])
        self.assertIsNone(result["russian"]["count"])
        self.assertEqual(result["russian"]["source_count"], 30)
        self.assertEqual(result["russian"]["excluded_count"], 10)
        self.assertEqual(result["russian"]["potential_count"], 20)
        self.assertEqual(result["russian"]["blockers"][0]["lesson_id"], "second-remainder")
        self.assertEqual(canonical_version(self.database), before)

    def test_available_slot_grade_scope_uses_every_administrative_class(self):
        classes = {
            "7-А": self.database.create_group("grade7:base:7-А", "class", display_name="7-А"),
            "7-Б": self.database.create_group("grade7:base:7-Б", "class", display_name="7-Б"),
            "9-Ан": self.database.create_group("9-Ан", "class", display_name="9-Ан"),
            "9-Д": self.database.create_group("9-Д", "class", display_name="9-Д"),
        }
        groups = {
            name: self.database.create_group(name, "instructional")
            for name in ("7-1", "9-A")
        }
        pupils = {}
        for grade, class_names, group_name, roster_count, assigned_count in (
            ("7", ("7-А", "7-Б"), "7-1", 24, 11),
            ("9", ("9-Ан", "9-Д"), "9-A", 10, 3),
        ):
            roster = []
            for index in range(roster_count):
                pupil = self.database.create_identity("student", f"{grade} pupil {index}")
                base_class = classes[class_names[index % len(class_names)]]
                self.database.create_membership(base_class["id"], pupil["id"], "student", "test")
                if index < assigned_count:
                    self.database.create_membership(groups[group_name]["id"], pupil["id"], "student", "test")
                roster.append(pupil)
            pupils[grade] = roster

        result = experiment_audience_preview(self.database, {
            "reference_date": "2026-10-13",
            "slots": [{"start": "09:00", "end": "09:45"}],
            "lessons": [
                {"id": "math-7-1", "day": 0, "period": 0,
                 "audience": {"kind": "groups", "group_ids": [str(groups["7-1"]["id"])]}},
                {"id": "other-7", "day": 0, "period": 0,
                 "grade_scope": "7",
                 "audience": {"kind": "available_slot", "class_ids": []}},
                {"id": "math-9-a", "day": 0, "period": 0,
                 "audience": {"kind": "groups", "group_ids": [str(groups["9-A"]["id"])]}},
                {"id": "other-9", "day": 0, "period": 0,
                 "grade_scope": "9",
                 "audience": {"kind": "available_slot", "class_ids": []}},
            ],
        })["lessons"]

        self.assertEqual(result["other-7"]["status"], "ok")
        self.assertEqual(result["other-7"]["source_count"], 24)
        self.assertEqual(result["other-7"]["excluded_count"], 11)
        self.assertEqual(result["other-7"]["count"], 13)
        self.assertEqual(set(result["other-7"]["student_ids"]), {
            str(pupils["7"][index]["id"]) for index in range(11, 24)
        })
        self.assertEqual(result["other-9"]["source_count"], 10)
        self.assertEqual(result["other-9"]["excluded_count"], 3)
        self.assertEqual(result["other-9"]["count"], 7)

        payload = {
            "reference_date": date.today().isoformat(),
            "slots": [{"start": "09:00", "end": "09:45"}],
            "lessons": [
                {"id": "math-7-1", "day": 0, "period": 0,
                 "audience": {"kind": "groups", "group_ids": [str(groups["7-1"]["id"])]}},
                {"id": "other-7", "day": 0, "period": 0, "grade_scope": "7",
                 "audience": {"kind": "available_slot", "class_ids": []}},
                {"id": "math-9-a", "day": 0, "period": 0,
                 "audience": {"kind": "groups", "group_ids": [str(groups["9-A"]["id"])]}},
                {"id": "other-9", "day": 0, "period": 0, "grade_scope": "9",
                 "audience": {"kind": "available_slot", "class_ids": []}},
            ],
        }
        school_directory(self.database)
        snapshot = cached_audience_snapshot(date.today(), self.database)
        self.assertIsNotNone(snapshot)
        direct = experiment_audience_preview(self.database, payload)["lessons"]
        cached = experiment_audience_preview(self.database, payload, snapshot=snapshot)["lessons"]
        self.assertEqual(cached, direct)

    def test_experimental_audience_uses_valid_memberships_and_reports_unknown(self):
        student = self.database.create_identity("student", "Student")
        other_student = self.database.create_identity("student", "Other Student")
        class_id = str(self.class_group["id"])
        group_id = str(self.subgroup["id"])
        self.database.create_membership(class_id, student["id"], "student", "test")
        self.database.create_membership(self.other_class_group["id"], other_student["id"], "student", "test")
        self.database.create_membership(group_id, student["id"], "student", "test")
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE memberships SET valid_until='2026-09-30' WHERE group_id=?", (group_id,))
        payload = {"reference_date": "2026-10-13", "slots": [{"start": "09:00", "end": "09:45"}], "lessons": [
            {"id": "expired", "day": 0, "period": 0, "audience": {"kind": "groups", "group_ids": [group_id]}},
            {"id": "rest", "day": 0, "period": 0, "audience": {"kind": "available_slot", "class_ids": [class_id]}},
            {"id": "chosen", "day": 1, "period": 0, "audience": {"kind": "students", "student_ids": [str(student["id"])]}},
            {"id": "parallel", "day": 1, "period": 0, "audience": {"kind": "classes", "grade": "9", "class_ids": []}},
            {"id": "missing-id", "day": 1, "period": 0, "audience": {"kind": "groups", "group_ids": ["missing"]}},
        ]}
        result = experiment_audience_preview(self.database, payload)["lessons"]
        self.assertEqual(result["expired"]["status"], "incomplete")
        self.assertEqual(result["rest"]["status"], "ambiguous")
        self.assertIsNone(result["rest"]["count"])
        self.assertEqual(result["rest"]["blockers"][0]["lesson_id"], "expired")
        self.assertEqual(result["chosen"]["student_ids"], [str(student["id"])])
        self.assertEqual(result["chosen"]["class_ids"], [class_id])
        self.assertEqual(set(result["parallel"]["student_ids"]), {str(student["id"]), str(other_student["id"])})
        self.assertEqual(result["missing-id"]["status"], "error")

    def test_remainder_excludes_only_its_own_lesson_and_detects_all_overlapping_columns(self):
        class_id = str(self.class_group["id"])
        student = self.database.create_identity("student", "Pupil")
        self.database.create_membership(class_id, student["id"], "student", "test")
        payload = {"slots": [{"start": "09:00", "end": "09:45"}], "lessons": [
            {"id": "same-id", "day": 0, "period": 0,
             "audience": {"kind": "available_slot", "class_ids": [class_id]}},
            {"id": "same-id", "day": 0, "period": 0,
             "audience": {"kind": "available_slot", "class_ids": [class_id]}},
        ]}
        results = experiment_audience_preview(self.database, payload)["lessons"]
        self.assertEqual(results["same-id"]["status"], "ambiguous")

    def test_membership_override_applies_to_remainder_on_reference_date(self):
        class_id = str(self.class_group["id"])
        student = self.database.create_identity("student", "Excluded pupil")
        self.database.create_membership(class_id, student["id"], "student", "test")
        with self.database.connection() as connection:
            self.database.execute(connection, """INSERT INTO membership_overrides
                (identity_id,group_id,member_role,action,reason,active,valid_from,valid_until)
                VALUES (?,?,'student','exclude','test',TRUE,'2026-10-01',NULL)""",
                (student["id"], class_id))
        payload = {"reference_date": "2026-10-13", "slots": [{"start": "09:00", "end": "09:45"}],
                   "lessons": [{"id": "rest", "day": 0, "period": 0,
                                "audience": {"kind": "available_slot", "class_ids": [class_id]}}]}
        result = experiment_audience_preview(self.database, payload)["lessons"]["rest"]
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["count"])
        self.assertIsNone(result["potential_count"])
        self.assertEqual(result["student_ids"], [])

    def test_membership_override_include_is_effective_and_exclude_takes_precedence(self):
        class_id = str(self.class_group["id"])
        included = self.database.create_identity("student", "Included pupil")
        both = self.database.create_identity("student", "Excluded despite include")
        with self.database.connection() as connection:
            for identity, action in [(included, "include"), (both, "include"), (both, "exclude")]:
                self.database.execute(connection, """INSERT INTO membership_overrides
                    (identity_id,group_id,member_role,action,reason,active,valid_from,valid_until)
                    VALUES (?,?,'student',?,'test',TRUE,'2026-10-01',NULL)""",
                    (identity["id"], class_id, action))
        result = experiment_audience_preview(self.database, {
            "reference_date": "2026-10-13", "lessons": [
                {"id": "class", "audience": {"kind": "classes", "class_ids": [class_id]}}
            ]})["lessons"]["class"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["student_ids"], [str(included["id"])])

    def test_saved_roster_survives_stale_edits_and_requires_explicit_recalculation(self):
        class_id = str(self.class_group["id"])
        group_id = str(self.subgroup["id"])
        first = self.database.create_identity("student", "First pupil")
        second = self.database.create_identity("student", "Second pupil")
        self.database.create_membership(class_id, first["id"], "student", "test")
        self.database.create_membership(group_id, first["id"], "student", "test")
        draft = create_experiment(self.database, name="Frozen roster", source="empty")
        payload = {**draft["payload"], "reference_date": "2026-10-13", "slots": [
            {"start": "09:00", "end": "09:45"}, {"start": "09:55", "end": "10:40"}],
            "lessons": [
                {"id": "remainder", "day": 0, "period": 0,
                 "audience": {"kind": "available_slot", "class_ids": [class_id]}},
                {"id": "english", "day": 0, "period": 0,
                 "audience": {"kind": "groups", "group_ids": [group_id]}},
            ]}
        saved = save_experiment(self.database, draft["id"], expected_revision=1, payload=payload)
        locked = {lesson["id"]: lesson["audience_snapshot"]["student_ids"] for lesson in saved["payload"]["lessons"]}
        self.assertEqual(locked["remainder"], [])
        self.assertEqual(locked["english"], [str(first["id"])])

        self.database.create_membership(class_id, second["id"], "student", "test")
        self.database.create_membership(group_id, second["id"], "student", "test")
        stale_payload = {**saved["payload"], "lessons": [dict(lesson) for lesson in saved["payload"]["lessons"]]}
        for lesson in stale_payload["lessons"]:
            lesson.pop("audience_snapshot", None)
        stale_payload["lessons"][1]["period"] = 1
        next_saved = save_experiment(self.database, draft["id"], expected_revision=2, payload=stale_payload)
        snapshots = {lesson["id"]: lesson["audience_snapshot"]["student_ids"] for lesson in next_saved["payload"]["lessons"]}
        self.assertEqual(snapshots["english"], [str(first["id"])])
        preview = experiment_audience_preview(self.database, next_saved["payload"])["lessons"]
        self.assertEqual(preview["english"]["student_ids"], [str(first["id"])])
        self.assertEqual(preview["english"]["proposed_student_ids"], [str(first["id"]), str(second["id"])])
        self.assertTrue(preview["english"]["needs_recalculation"])
        self.assertEqual(preview["remainder"]["student_ids"], [])
        self.assertEqual(preview["remainder"]["proposed_student_ids"], [str(first["id"]), str(second["id"])])
        self.assertTrue(preview["remainder"]["needs_recalculation"])

        confirmed = {**next_saved["payload"], "lessons": [dict(lesson) for lesson in next_saved["payload"]["lessons"]]}
        confirmed["lessons"][1]["audience_snapshot"] = None
        recalculated = save_experiment(self.database, draft["id"], expected_revision=3, payload=confirmed)
        recalculated_roster = next(lesson for lesson in recalculated["payload"]["lessons"] if lesson["id"] == "english")["audience_snapshot"]["student_ids"]
        self.assertEqual(recalculated_roster, [str(first["id"]), str(second["id"])])

        time_edit = {**recalculated["payload"], "lessons": [dict(lesson) for lesson in recalculated["payload"]["lessons"]]}
        time_edit["lessons"][1]["period"] = 0
        time_saved = save_experiment(self.database, draft["id"], expected_revision=4, payload=time_edit)
        time_preview = experiment_audience_preview(self.database, time_saved["payload"])["lessons"]["english"]
        self.assertEqual(time_preview["student_ids"], [str(first["id"]), str(second["id"])])
        self.assertEqual(time_preview["proposed_student_ids"], [str(first["id"]), str(second["id"])])
        self.assertFalse(time_preview["needs_recalculation"])
        self.assertFalse(time_preview["fingerprint_changed"])

    def test_undated_template_does_not_require_confirmation_when_roster_is_unchanged(self):
        class_id = str(self.class_group["id"])
        student = self.database.create_identity("student", "Undated pupil")
        self.database.create_membership(class_id, student["id"], "student", "test")
        draft = create_experiment(self.database, name="Undated primary template", source="empty")
        saved = save_experiment(self.database, draft["id"], expected_revision=1,
            payload={**draft["payload"], "lessons": [{"id": "lesson", "day": 0, "period": 0,
                "audience": {"kind": "classes", "class_ids": [class_id]}}]})
        preview = experiment_audience_preview(self.database, saved["payload"])["lessons"]["lesson"]
        self.assertEqual(preview["student_ids"], [str(student["id"])])
        self.assertFalse(preview["needs_recalculation"])

        next_student = self.database.create_identity("student", "Added pupil")
        self.database.create_membership(class_id, next_student["id"], "student", "test")
        changed = experiment_audience_preview(self.database, saved["payload"])["lessons"]["lesson"]
        self.assertTrue(changed["needs_recalculation"])
        self.assertEqual(set(changed["proposed_student_ids"]), {str(student["id"]), str(next_student["id"])})

    def test_school_snapshot_resolves_from_source_database_but_saves_to_local_store(self):
        source_dir = tempfile.TemporaryDirectory()
        source = Database(Path(source_dir.name) / "school-source.db")
        source.initialize()
        try:
            source_class = source.create_group("9-Д", "class")
            source_student = source.create_identity("student", "School pupil")
            source.create_membership(source_class["id"], source_student["id"], "student", "test")
            draft = create_experiment(self.database, name="Local school draft", source="empty")
            saved = save_experiment(self.database, draft["id"], expected_revision=1,
                payload={**draft["payload"], "lessons": [{"id": "source-class", "audience": {
                    "kind": "classes", "class_ids": [str(source_class["id"])]}}]},
                audience_database=source)
            snapshot = saved["payload"]["lessons"][0]["audience_snapshot"]["student_ids"]
            self.assertEqual(snapshot, [str(source_student["id"])])
            self.assertEqual(get_experiment(self.database, draft["id"])["payload"]["lessons"][0]["audience_snapshot"]["student_ids"], snapshot)
            self.assertEqual(experiment_audience_preview(self.database, saved["payload"])["lessons"]["source-class"]["status"], "incomplete")
        finally:
            source_dir.cleanup()

    def test_unresolved_display_class_scope_only_blocks_matching_parallel(self):
        class_id = str(self.class_group["id"])
        student = self.database.create_identity("student", "Pupil")
        self.database.create_membership(class_id, student["id"], "student", "test")
        slots = [{"start": "09:00", "end": "09:45"}]
        remainder = {"id": "free", "day": 0, "period": 0,
                     "audience": {"kind": "available_slot", "grade": "9", "class_ids": [class_id]}}
        unrelated_unknown = {"id": "unknown-7", "day": 0, "period": 0,
                             "classIds": ["grade7:base:7-А", "grade7:base:7-Б"]}
        result = experiment_audience_preview(self.database,
            {"slots": slots, "lessons": [remainder, unrelated_unknown]})["lessons"]["free"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["student_ids"], [str(student["id"])])
        related_unknown = {"id": "unknown-9", "day": 0, "period": 0,
                           "audience": {"kind": "unresolved", "class_ids": [class_id]}}
        result = experiment_audience_preview(self.database,
            {"slots": slots, "lessons": [remainder, related_unknown]})["lessons"]["free"]
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["count"])
        self.assertEqual(result["potential_count"], 1)
        self.assertEqual(result["blockers"][0]["lesson_id"], "unknown-9")
        self.assertEqual(result["student_ids"], [])

    def test_m10_grade9_remainder_ignores_grade6_lesson_and_grade7_unknown(self):
        grade6 = self.database.create_group("6-А", "class")
        english6 = self.database.create_group("English 6", "instructional")
        grade9_student = self.database.create_identity("student", "Grade 9 pupil")
        grade6_student = self.database.create_identity("student", "Grade 6 pupil")
        grade9_id = str(self.class_group["id"])
        self.database.create_membership(grade9_id, grade9_student["id"], "student", "test")
        self.database.create_membership(str(grade6["id"]), grade6_student["id"], "student", "test")
        self.database.create_membership(str(english6["id"]), grade6_student["id"], "student", "test")
        payload = {"reference_date": "2026-10-13", "slots": [{"start": "13:40", "end": "14:25"}],
                   "lessons": [
                       {"id": "L10", "subject": "English", "day": 0, "period": 0,
                        "audience": {"kind": "groups", "group_ids": [str(english6["id"])]},
                        "classIds": ["grade6:base:6-А"]},
                       {"id": "F10", "subject": "Plastic", "day": 0, "period": 0,
                        "audience": {"kind": "groups", "group_ids": ["missing-grade7-group"]},
                        "classIds": ["grade7:base:7-А", "grade7:base:7-Б"]},
                       {"id": "M10", "subject": "Русский", "day": 0, "period": 0,
                        "audience": {"kind": "available_slot", "grade": "9", "class_ids": [grade9_id]}},
                   ]}
        result = experiment_audience_preview(self.database, payload)["lessons"]["M10"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["student_ids"], [str(grade9_student["id"])])
        self.assertEqual(result["blockers"], [])

    def test_experimental_audience_keeps_legacy_ids_and_separate_remainders(self):
        first = self.database.create_identity("student", "First")
        second = self.database.create_identity("student", "Second")
        third = self.database.create_identity("student", "Third")
        first_class = str(self.class_group["id"])
        second_class = str(self.other_class_group["id"])
        group_id = str(self.subgroup["id"])
        for group, student in [(first_class, first), (group_id, first), (second_class, second), (first_class, third)]:
            self.database.create_membership(group, student["id"], "student", "test")
        payload = {"slots": [{"start": "09:00", "end": "09:45"}], "lessons": [
            {"id": "legacy", "day": 0, "period": 0, "groupIds": [first_class, group_id], "classIds": [first_class]},
            {"id": "first-rest", "day": 0, "period": 0, "audience": {"kind": "available_slot", "class_ids": [first_class]}},
            {"id": "second-rest", "day": 0, "period": 0, "audience": {"kind": "available_slot", "class_ids": [second_class]}},
            {"id": "legacy-static", "day": 1, "period": 0, "audienceMode": "remainder", "classIds": [first_class],
             "canonical": {"assignment": {"metadata": {"audience_rule": {"kind": "remaining", "class_group_id": first_class,
                 "partition_group_ids": [group_id]}}}}},
        ]}
        result = experiment_audience_preview(self.database, payload)["lessons"]
        self.assertEqual(result["legacy"]["student_ids"], [str(first["id"])])
        self.assertEqual(result["first-rest"]["student_ids"], [str(third["id"])])
        self.assertEqual(result["second-rest"]["student_ids"], [str(second["id"])])
        self.assertEqual(result["second-rest"]["status"], "ok")
        self.assertEqual(result["legacy-static"]["student_ids"], [str(third["id"])])

    def test_informational_event_does_not_remove_students_from_available_slot(self):
        student = self.database.create_identity("student", "Notice pupil")
        class_id = str(self.class_group["id"])
        self.database.create_membership(class_id, student["id"], "student", "test")
        payload = {"slots": [{"start": "09:00", "end": "09:45"}], "lessons": [
            {"id": "notice", "kind": "event", "informational": True, "day": 0, "period": 0,
             "classIds": [class_id], "start": "09:00", "end": "09:45"},
            {"id": "rest", "day": 0, "period": 0,
             "audience": {"kind": "available_slot", "class_ids": [class_id]}},
        ]}
        result = experiment_audience_preview(self.database, payload)["lessons"]
        self.assertEqual(result["notice"]["student_ids"], [])
        self.assertEqual(result["rest"]["student_ids"], [str(student["id"])])

    def test_special_schedule_marks_do_not_consume_available_slot_audience(self):
        student = self.database.create_identity("student", "Lunch pupil")
        class_id = str(self.class_group["id"])
        self.database.create_membership(class_id, student["id"], "student", "test")
        payload = {"slots": [{"start": "12:00", "end": "12:45"}], "lessons": [
            {"id": "lunch", "specialMark": "lunch", "day": 0, "period": 0,
             "audience": {"kind": "classes", "class_ids": [class_id]}},
            {"id": "free", "specialMark": "no_lesson", "day": 0, "period": 0,
             "audience": {"kind": "classes", "class_ids": [class_id]}},
            {"id": "rest", "day": 0, "period": 0,
             "audience": {"kind": "available_slot", "class_ids": [class_id]}},
        ]}
        result = experiment_audience_preview(self.database, payload)["lessons"]
        # A parallel remainder is a separate, unresolved fallback audience;
        # lunch cannot capture that whole cohort at the same time.
        self.assertEqual(result["lunch"]["student_ids"], [])
        self.assertEqual(result["free"]["student_ids"], [])
        self.assertEqual(result["rest"]["student_ids"], [str(student["id"])])

    def test_lunch_is_a_class_scoped_fallback_behind_overlapping_lessons(self):
        class_id = str(self.class_group["id"])
        math = self.database.create_group("9A math", "instructional", base_class_name="9A")
        chemistry = self.database.create_group("9A chemistry", "instructional", base_class_name="9A")
        math_student = self.database.create_identity("student", "Math pupil")
        free_student = self.database.create_identity("student", "Free pupil")
        self.database.create_membership(class_id, math_student["id"], "student", "test")
        self.database.create_membership(class_id, free_student["id"], "student", "test")
        self.database.create_membership(str(math["id"]), math_student["id"], "student", "test")
        payload = {"slots": [{"start": "11:50", "end": "12:35"}], "lessons": [
            {"id": "math", "day": 0, "period": 0,
             "audience": {"kind": "groups", "group_ids": [str(math["id"])]}, "classIds": [class_id]},
            {"id": "lunch", "specialMark": "lunch", "day": 0, "period": 0,
             "audience": {"kind": "classes", "class_ids": [class_id]}},
        ]}
        result = experiment_audience_preview(self.database, payload)["lessons"]
        self.assertEqual(result["math"]["student_ids"], [str(math_student["id"])])
        self.assertEqual(result["lunch"]["status"], "ok")
        self.assertEqual(result["lunch"]["source_count"], 2)
        self.assertEqual(result["lunch"]["student_ids"], [str(free_student["id"])])

    def test_historical_canonical_lunch_without_staffing_uses_its_saved_audience(self):
        class_id = str(self.class_group["id"])
        pupil = self.database.create_identity("student", "Historical lunch pupil")
        self.database.create_membership(class_id, pupil["id"], "student", "test")
        result = experiment_audience_preview(self.database, {
            "slots": [{"start": "12:00", "end": "12:45"}],
            "lessons": [{"id": "historical-lunch", "subject": "Обед", "day": 0, "period": 0,
                         "teacherIds": [], "canonicalRole": "residual",
                         "canonical": {"assignment": {"metadata": {
                             "special_mark": "lunch",
                             "audience_rule": {"kind": "whole_grade", "class_group_id": class_id},
                         }}}}],
        })["lessons"]["historical-lunch"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["student_ids"], [str(pupil["id"])])

    def test_grade_expansion_uses_human_class_metadata_and_ignores_grade_catalog_group(self):
        class_one = self.database.create_group("grade9:base:opaque-a", "class", display_name="9-Ан", base_class_name="9-Ан")
        class_two = self.database.create_group("grade9:base:opaque-d", "class", display_name="9-Д", base_class_name="9-Д")
        other_grade = self.database.create_group("grade10:base:opaque", "class", display_name="10-А", base_class_name="10-А")
        grade_catalog = self.parallel_group
        students = []
        for index, group in enumerate((class_one, class_two, other_grade, grade_catalog)):
            student = self.database.create_identity("student", f"Metadata pupil {index}")
            self.database.create_membership(str(group["id"]), student["id"], "student", "test")
            students.append(student)
        result = experiment_audience_preview(self.database, {
            "slots": [{"start": "12:00", "end": "12:45"}],
            "lessons": [{"id": "grade9", "day": 0, "period": 0,
                         "audience": {"kind": "available_slot", "grade": "9"}}],
        })["lessons"]["grade9"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(set(result["student_ids"]), {str(students[0]["id"]), str(students[1]["id"])})

    def test_column_widths_and_reference_date_round_trip_only_in_experiment(self):
        baseline = canonical_version(self.database)
        draft = create_experiment(self.database, name="Ширина", source="empty")
        self.assertEqual(draft["payload"]["lessons"], [])
        updated = save_experiment(self.database, draft["id"], expected_revision=1,
                                  payload={**draft["payload"], "reference_date": "2026-10-13",
                                           "column_width_mode": "manual", "column_widths": {"grade:9": 240}})
        self.assertEqual(updated["payload"]["column_widths"], {"grade:9": 240})
        self.assertEqual(updated["payload"]["column_width_mode"], "manual")
        self.assertEqual(updated["payload"]["reference_date"], "2026-10-13")
        self.assertEqual(canonical_version(self.database), baseline)

    def test_draft_catalog_separates_explicit_demo_and_unclassified_without_losing_content(self):
        user = create_experiment(self.database, name="Моя неделя", source="empty", payload={"draft_kind": "user"})
        demo = create_experiment(self.database, name="ДЕМО · Полная тестовая неделя", source="empty",
                                 payload={"draft_kind": "demo", "lessons": [{"id": "event", "kind": "event"}]})
        old = create_experiment(self.database, name="Новый шаблон", source="empty")
        items = {item["id"]: item for item in list_experiments(self.database)}
        self.assertEqual(items[user["id"]]["draft_kind"], "user")
        self.assertEqual(items[demo["id"]]["draft_kind"], "demo")
        self.assertEqual(items[demo["id"]]["event_count"], 1)
        self.assertEqual(items[demo["id"]]["lesson_count"], 1)
        self.assertTrue(items[demo["id"]]["created_at"])
        self.assertEqual(items[old["id"]]["draft_kind"], "unclassified")
        copy = duplicate_experiment(self.database, demo["id"])
        self.assertEqual(get_experiment(self.database, copy["id"])["payload"]["draft_kind"], "user")


if __name__ == "__main__":
    unittest.main()
