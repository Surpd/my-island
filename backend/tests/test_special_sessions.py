from copy import deepcopy
from unittest import TestCase

from backend.services import canonical_schedule as canonical
from backend.services.constructor_publication import preview_publication
from backend.services.schedule_experiments import (
    create_experiment_week,
    get_experiment,
    manage_experiment,
    save_experiment,
)
from backend.services.schedule_laboratories import laboratory_action
from backend.tests import test_constructor_publication as fixtures


class SpecialSessionTests(TestCase):
    setUp = fixtures.ConstructorPublicationTests.setUp
    tearDown = fixtures.ConstructorPublicationTests.tearDown
    lesson = fixtures.ConstructorPublicationTests.lesson
    draft = fixtures.ConstructorPublicationTests.draft
    publish = fixtures.ConstructorPublicationTests.publish
    week = "2026-10-19"

    def preview(self, lessons):
        draft = self.draft(lessons)
        return preview_publication(
            self.database, draft["id"], scope_kind="template", expected_revision=1
        )

    def test_consultation_and_independent_practice_share_online_time(self):
        draft = self.draft(
            [
                self.lesson(
                    subject="Writing",
                    activityType="practice",
                    teacherId="",
                    room="Онлайн",
                ),
                self.lesson(
                    id="help",
                    subject="Feedback",
                    activityType="consultation",
                    format="online",
                    room="101",
                ),
            ]
        )
        preview = preview_publication(
            self.database, draft["id"], scope_kind="template", expected_revision=1
        )
        self.assertFalse(preview["blockers"], preview["blockers"])
        self.publish(draft)
        self.publish(draft, "week", week_start=self.week)
        student = canonical.student_campus_projection(
            self.database, self.student["id"], self.week
        )
        self.assertEqual(
            {r["activity_type"] for r in student["items"] if r["state"] == "ACTIVITY"},
            {"practice", "consultation"},
        )
        self.assertTrue(
            all(
                not r["room"] and r["delivery_mode"] == "online"
                for r in student["items"]
                if r["state"] == "ACTIVITY"
            )
        )
        teacher = canonical.project_teacher(
            self.database, self.teacher["id"], self.week
        )
        self.assertEqual([r["activity"] for r in teacher["items"]], ["Feedback"])

    def test_room_and_consultation_teacher_conflicts(self):
        practice = self.lesson(activityType="practice", teacherId="")
        help = self.lesson(id="help", subject="Help", activityType="consultation")
        codes = {row["code"] for row in self.preview([practice, help])["diagnostics"]}
        self.assertIn("room_overlap", codes)
        self.assertFalse({"student_overlap", "missing_teacher"} & codes)
        codes = {
            row["code"]
            for row in self.preview([help, {**help, "id": "second", "room": "203"}])[
                "diagnostics"
            ]
        }
        self.assertIn("teacher_overlap", codes)
        self.assertIn(
            "missing_consultation_teacher",
            {
                r["code"]
                for r in self.preview([{**help, "teacherId": ""}])["diagnostics"]
            },
        )

    def test_personal_exclusion_and_conflict_titles(self):
        student = str(self.student["id"])
        draft = self.draft(
            [
                self.lesson(activityType="practice", teacherId=""),
                self.lesson(id="foreign", subject="Physics", room="202"),
            ]
        )
        self.publish(draft)
        self.publish(draft, "week", week_start=self.week)
        rows = canonical.student_campus_projection(self.database, student, self.week)[
            "items"
        ]
        self.assertEqual(
            {a["activity"] for r in rows for a in r.get("activities", [])},
            {"Physics", "Math"},
        )
        lesson = draft["payload"]["lessons"][0]
        lesson.update(audience_snapshot=None, attendance_reason="Individual timetable")
        lesson["audience"]["exclude_student_ids"] = [student]
        updated = save_experiment(
            self.database,
            draft["id"],
            expected_revision=draft["revision"],
            payload=draft["payload"],
            actor_user_id=self.database.get_or_create_user(29000001, "admin")["id"],
        )
        self.assertEqual(
            updated["payload"]["lessons"][0]["attendance_history"][0]["excluded"],
            [student],
        )
        self.publish(updated, "week", week_start=self.week)
        rows = canonical.student_campus_projection(self.database, student, self.week)[
            "items"
        ]
        self.assertEqual(
            [row["activity"] for row in rows if row["state"] == "ACTIVITY"], ["Physics"]
        )

    def test_blueprint_edit_and_delete_preserve_week_and_history(self):
        template = self.draft([self.lesson()])
        blueprint = {"id": "lab-one", "subject": "Лаборатория", "direction": "Мир вокруг нас"}
        created = save_experiment(self.database, template["id"], expected_revision=1,
                                  payload={**template["payload"], "laboratory_blueprints": [blueprint]})
        week = create_experiment_week(self.database, created["id"], week_start=self.week, expected_revision=2)
        edited = save_experiment(self.database, template["id"], expected_revision=2, payload={
            **created["payload"], "laboratory_blueprints": [
                {**blueprint, "subject": "Новая тема"}, {**blueprint, "id": "lab-copy"}]})
        deleted = save_experiment(self.database, template["id"], expected_revision=3,
                                  payload={**edited["payload"], "laboratory_blueprints": []})
        receipts = deleted["payload"]["laboratory_blueprint_history"]
        self.assertEqual(receipts[0]["after"], blueprint)
        self.assertEqual(receipts[-2]["before"]["subject"], "Новая тема")
        self.assertIsNone(receipts[-2]["after"])
        self.assertEqual(get_experiment(self.database, week["id"])["payload"], week["payload"])
        self.assertEqual(week["payload"]["laboratory_blueprints"], [blueprint])

    def test_laboratory_preview_partial_week_publish_disable(self):
        original = self.draft(
            [self.lesson(), self.lesson(id="history", subject="History", period=1)]
        )
        blueprint = {
            **self.lesson(
                id="science",
                subject="Science",
                teacherId=str(self.other_teacher["id"]),
                room="",
            ),
            "start": "09:00",
            "end": "10:45",
            "classIds": [str(self.group["id"])],
        }
        original["payload"]["laboratory_blueprints"] = [blueprint]
        template = save_experiment(
            self.database,
            original["id"],
            expected_revision=1,
            payload=original["payload"],
        )
        self.publish(template)
        week = create_experiment_week(
            self.database,
            template["id"],
            expected_revision=template["revision"],
            week_start=self.week,
        )
        authored = deepcopy(week["payload"]["lessons"])
        student = str(self.student["id"])
        meeting = {"audience": {"kind": "students", "student_ids": [student]}}

        def action(d, **kw):
            return laboratory_action(
                self.database,
                self.database,
                d["id"],
                d["revision"],
                "science",
                meeting,
                **kw,
            )

        preview = action(week)
        self.assertEqual(len(preview["changes"]), 2)
        self.assertEqual(
            preview["changes"][0]["continuing"], [str(self.other_student["id"])]
        )
        active = action(week, token=preview["token"])
        with self.assertRaisesRegex(ValueError, "отключите лаборатори"):
            manage_experiment(self.database, active["id"], expected_revision=active["revision"], action="archive")
        with self.assertRaisesRegex(ValueError, "отключите лабораторию"):
            save_experiment(self.database, active["id"], expected_revision=active["revision"],
                            payload={**active["payload"], "laboratory_blueprints": []})
        self.publish(active, "week", week_start=self.week)

        def activities(person):
            return [
                r["activity"]
                for r in canonical.project_student(self.database, person, self.week)[
                    "items"
                ]
                if r["state"] == "ACTIVITY"
            ]

        self.assertEqual(activities(student), ["Science"])
        self.assertEqual(activities(self.other_student["id"]), ["Math", "History"])
        preview = action(active, deactivate=True)
        disabled = action(active, deactivate=True, token=preview["token"])
        self.assertEqual(disabled["payload"]["lessons"], authored)
        archived = manage_experiment(self.database, disabled["id"], expected_revision=disabled["revision"], action="archive")
        with self.assertRaisesRegex(ValueError, "история"):
            manage_experiment(self.database, archived["id"], expected_revision=archived["revision"], action="delete", confirmed=True)
        self.assertEqual(activities(student), ["Science"])
        disabled = manage_experiment(self.database, archived["id"], expected_revision=archived["revision"], action="restore")

        self.publish(disabled, "week", week_start=self.week)
        self.assertEqual(activities(student), ["Math", "History"])

    def test_full_replacement_additional_teacher_restore_copy_and_export(self):
        from backend.services.constructor_publication import (
            preview_restore,
            restore_publication,
        )
        from backend.services.schedule_experiments import (
            create_effective_week_experiment,
        )
        from backend.services.schedule_sheet_export import (
            freeze_week,
            build_sheet_plan,
            sheets_requests,
        )

        original = self.draft(
            [self.lesson(), self.lesson(id="next", subject="Literature", period=1)]
        )
        blueprint = {
            **self.lesson(id="lab", subject="Past and present"),
            "classIds": [str(self.group["id"])],
            "teacherIds": [str(self.other_teacher["id"])],
            "start": "09:00",
            "end": "10:45",
        }
        original["payload"]["laboratory_blueprints"] = [blueprint]
        saved = save_experiment(
            self.database,
            original["id"],
            expected_revision=1,
            payload=original["payload"],
        )
        self.publish(saved)
        week = create_experiment_week(
            self.database,
            saved["id"],
            expected_revision=saved["revision"],
            week_start=self.week,
        )

        def preview(d, **kw):
            return laboratory_action(
                self.database, self.database, d["id"], d["revision"], "lab", {}, **kw
            )

        active = preview(week, token=preview(week)["token"])

        def sheet_cells(d):
            frozen = freeze_week(
                {
                    "source": {"draft_id": str(d["id"]), "revision": d["revision"]},
                    "payload": d["payload"],
                    "groups": [
                        {
                            "id": str(self.group["id"]),
                            "name": self.group["name"],
                            "group_type": "class",
                        }
                    ],
                    "teachers": [
                        {"display_name": t["display_name"], "id": str(t["id"])}
                        for t in (self.teacher, self.other_teacher)
                    ],
                },
                self.week,
            )
            self.assertNotIn("laboratory_meetings", frozen["payload"])
            plan = build_sheet_plan(
                frozen,
                title="Synthetic",
                sheet_id=10,
                mapping_sheet_id=11,
                layout="final-compact",
            )
            self.assertTrue(sheets_requests(plan))
            return " ".join(c["text"] for c in plan["cells"])

        self.assertIn("Past and present", sheet_cells(active))
        published = self.publish(active, "week", week_start=self.week)
        for teacher in (self.teacher, self.other_teacher):
            rows = canonical.project_teacher(self.database, teacher["id"], self.week)[
                "items"
            ]
            self.assertEqual(
                [(r["activity"], str(r["end_time"])[:5]) for r in rows],
                [("Past and present", "10:45")],
            )
        historical = create_effective_week_experiment(
            self.database, published["published_id"]
        )
        self.assertIn("lab", historical["payload"]["laboratory_meetings"])
        another = create_experiment_week(
            self.database,
            active["id"],
            expected_revision=active["revision"],
            week_start="2026-10-26",
        )
        self.assertEqual(
            [r["id"] for r in another["payload"]["lessons"]], ["l1", "next"]
        )
        self.assertFalse(another["payload"].get("laboratory_meetings"))
        disabled = preview(
            active, token=preview(active, deactivate=True)["token"], deactivate=True
        )
        self.assertNotIn("Past and present", sheet_cells(disabled))
        self.publish(disabled, "week", week_start=self.week)
        head = canonical.publication_head(self.database, "week:" + self.week)
        restoration = preview_restore(
            self.database,
            published["publication_id"],
            expected_head_revision=head["revision"],
        )
        restore_publication(
            self.database,
            published["publication_id"],
            expected_head_revision=head["revision"],
            preview_token=restoration["preview_token"],
            confirmed=True,
        )
        rows = canonical.project_student(self.database, self.student["id"], self.week)[
            "items"
        ]
        self.assertEqual(
            [r["activity"] for r in rows if r["state"] == "ACTIVITY"],
            ["Past and present"],
        )

    def test_conflicting_partial_replacement_and_personal_week_override(self):
        original = self.draft()
        blueprint = {
            **self.lesson(id="lab"),
            "classIds": [str(self.group["id"])],
            "audience": {"kind": "students", "student_ids": [str(self.student["id"])]},
        }
        original["payload"]["laboratory_blueprints"] = [blueprint]
        saved = save_experiment(
            self.database,
            original["id"],
            expected_revision=1,
            payload=original["payload"],
        )
        week = create_experiment_week(
            self.database,
            saved["id"],
            expected_revision=saved["revision"],
            week_start=self.week,
        )
        preview = laboratory_action(
            self.database, self.database, week["id"], week["revision"], "lab", {}
        )
        self.assertEqual(len(preview["conflicts"]), 2)
        self.assertNotIn("token", preview)
        row = week["payload"]["lessons"][0]
        row["audience"]["exclude_student_ids"] = [str(self.student["id"])]
        row.update(audience_snapshot=None, attendance_reason="Only this Tuesday")
        updated = save_experiment(
            self.database,
            week["id"],
            expected_revision=week["revision"],
            payload=week["payload"],
        )
        another = create_experiment_week(
            self.database,
            week["id"],
            expected_revision=updated["revision"],
            week_start="2026-10-26",
        )
        self.assertFalse(
            another["payload"]["lessons"][0]["audience"].get("exclude_student_ids")
        )

    def test_present_practice_teacher_and_non_attending_contact(self):
        draft = self.draft(
            [
                self.lesson(
                    activityType="practice",
                    responsible_teacher_ids=[str(self.other_teacher["id"])],
                )
            ]
        )
        self.publish(draft)
        self.publish(draft, "week", week_start=self.week)
        teacher = canonical.project_teacher(
            self.database, self.teacher["id"], self.week
        )["items"][0]
        contact = canonical.project_teacher(
            self.database, self.other_teacher["id"], self.week
        )["items"][0]
        self.assertTrue(teacher["physical_presence"])
        self.assertFalse(contact["physical_presence"])
        student = canonical.student_campus_projection(
            self.database, self.student["id"], self.week
        )["items"][0]
        self.assertEqual(student["teacher_names"], ["Synthetic Teacher"])
        self.assertEqual(student["responsible_teachers"], ["Other Teacher"])

    def test_empty_eligible_group_does_not_hide_teacher_consultation(self):
        empty = self.database.create_group(
            "9A empty", "instructional", base_class_name="9A"
        )
        lesson = self.lesson(
            activityType="consultation",
            audience={"kind": "groups", "group_ids": [str(empty["id"])]},
        )
        draft = self.draft([lesson])
        self.publish(draft)
        self.publish(draft, "week", week_start=self.week)
        self.assertEqual(
            canonical.project_student(self.database, self.student["id"], self.week)[
                "items"
            ],
            [],
        )
        self.assertEqual(
            canonical.project_teacher(self.database, self.teacher["id"], self.week)[
                "items"
            ][0]["activity_type"],
            "consultation",
        )
