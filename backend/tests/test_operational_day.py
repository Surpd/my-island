from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.database import Database
from backend.handlers.api import create_router
from backend.services import canonical_schedule as canonical
from backend.services.operational_day import operational_day, next_week_drafts, school_day
from backend.services.schedule_experiments import create_experiment


MONDAY = date(2026, 12, 28)


def block(start, end, teacher="t", room="101", mode="normal"):
    return {"weekday": 0, "slot": {"start": start, "end": end}, "grade_scope": "9",
            "status": "resolved", "mode": "SINGLE",
            "assignments": [{"activity": "Synthetic", "teacher_ids": [teacher], "role": "primary",
                             "metadata": {"room": room, "day_plan_mode": mode}}]}


class OperationalDayTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name) / "test.sqlite3")
        self.db.initialize()

    def materialize(self):
        blocks = {"first": block("09:00", "09:45"), "second": block("09:15", "10:00", room="каб. 101"),
                  "adjacent": block("10:00", "10:45"), "cancel": block("09:00", "09:45"),
                  "remote": block("09:00", "09:45", "remote", mode="online"),
                  "sdep": block("09:00", "09:45", mode="sdep")}
        canonical.import_canonical_artifact(self.db, {"version_id": "test-base", "source_snapshot": {"id": "s", "fingerprint": "f"},
                                           "blocks": blocks}, status="approved_baseline")
        return canonical.materialize_effective_week(self.db, "test-base", MONDAY.isoformat(), [
            {"block_key": "cancel", "change_kind": "cancelled"},
            {"block_key": "adjacent", "change_kind": "replaced", "assignments": block("10:00", "10:45", teacher="new")["assignments"]},
        ])

    def test_day_uses_actual_intervals_pinned_record_and_known_coverage(self):
        missing = operational_day(self.db, MONDAY, "Europe/Moscow")
        self.assertIsNone(missing["scheduled_count"])
        self.assertEqual(missing["alerts"][0]["code"], "no_published_week")
        self.materialize()
        day = operational_day(self.db, MONDAY, "Europe/Moscow")
        self.assertEqual(day["scheduled_count"], 5)
        self.assertEqual(day["rooms"], {"occupied": ["101"], "catalog_coverage": "unknown"})
        self.assertEqual(sorted(r["code"] for r in day["alerts"]), ["room_overlap", "teacher_overlap"])
        replaced = next(r for r in day["items"] if r["change"] == "teacher_replaced")
        self.assertEqual(replaced["previous_teacher_ids"], ["t"])
        for row in day["items"]:
            if row["delivery_mode"] in {"sdep", "online"}:
                self.assertIsNone(row["room"])
        self.assertEqual(day["week_days"][-1], "2027-01-03")
        self.assertEqual(operational_day(self.db, date(2027, 1, 3), "Europe/Moscow")["scheduled_count"], 0)
        self.assertEqual(school_day(datetime(2027, 1, 3, 21, tzinfo=timezone.utc))[0], date(2027, 1, 4))

    def test_joint_groups_and_teachers_remain_visible_after_move(self):
        class_a = self.db.create_group("Fixture 9A", "class", base_class_name="9A")
        class_b = self.db.create_group("Fixture 9B", "class", base_class_name="9B")
        teacher_a = self.db.create_identity("teacher", "Fixture Teacher A")
        teacher_b = self.db.create_identity("teacher", "Fixture Teacher B")
        blocks = {}
        for key, group, teacher in (("a", class_a, teacher_a), ("b", class_b, teacher_b)):
            item = block("09:00", "09:45", str(teacher["id"]))
            item["assignments"][0]["canonical_group_ids"] = [str(group["id"])]
            item["assignments"][0]["metadata"].update(constructor_lesson_id=key, joint_activity_id="shared",
                                                        audience_coverage="explicit", activity_type="lab")
            blocks[key] = item
        canonical.import_canonical_artifact(self.db, {"version_id": "joint-base", "source_snapshot": {"id": "s", "fingerprint": "f"},
                                           "blocks": blocks}, status="approved_baseline")
        canonical.materialize_effective_week(self.db, "joint-base", MONDAY.isoformat(), [
            {"block_key": key, "change_kind": "replaced", "weekday": 1,
             "slot": {"start": "11:00", "end": "11:45"}, "assignments": item["assignments"]}
            for key, item in blocks.items()
        ])

        report = operational_day(self.db, date(2026, 12, 29), "Europe/Moscow")
        self.assertEqual(len(report["items"]), 1)
        item = report["items"][0]
        self.assertEqual(item["change"], "moved")
        self.assertEqual(item["previous_interval"], {"day": MONDAY.isoformat(), "start": "09:00", "end": "09:45"})
        self.assertEqual(item["joint_activity_id"], "shared")
        self.assertEqual({g["name"] for g in item["groups"]}, {"Fixture 9A", "Fixture 9B"})
        self.assertEqual({t["name"] for t in item["teachers"]}, {"Fixture Teacher A", "Fixture Teacher B"})
        self.assertEqual(item["lesson_ids"], ["a", "b"])

    def test_cancelled_joint_segment_does_not_hide_remaining_scheduled_segment(self):
        blocks = {key: block("09:00", "09:45") for key in ("a", "b")}
        for item in blocks.values():
            item["assignments"][0]["metadata"].update(joint_activity_id="synthetic-joint", audience_coverage="explicit")
        canonical.import_canonical_artifact(self.db, {"version_id": "joint-cancel", "source_snapshot": {"id": "s", "fingerprint": "f"},
                                           "blocks": blocks}, status="approved_baseline")
        canonical.materialize_effective_week(self.db, "joint-cancel", MONDAY.isoformat(), [
            {"block_key": "a", "change_kind": "cancelled"},
        ])
        report = operational_day(self.db, MONDAY, "Europe/Moscow")
        self.assertEqual(report["scheduled_count"], 1)
        self.assertEqual({item["change"]: item["block_keys"] for item in report["items"]},
                         {"cancelled": ["a"], "unchanged": ["b"]})

    def test_admin_link_is_scoped_to_edition_and_role_and_handles_source_failure(self):
        self.materialize()
        admin = self.db.get_or_create_user(901001, "admin")
        challenge, _ = self.db.create_admin_login_challenge(admin["id"])
        token, _ = self.db.consume_admin_login_challenge(challenge)
        headers = {"X-Dev-Auth": f"browser:{token}"}
        app = FastAPI()
        app.include_router(create_router(self.db))
        client = TestClient(app)
        path = "/api/admin/operations/day?day=2026-12-28"
        with mock.patch.dict("os.environ", {"SCHEDULE_BACKEND": "canonical"}):
            self.assertEqual(client.get(path).status_code, 401)
            response = client.get(path, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            item = response.json()["alerts"][0]
            self.assertEqual(client.get(item["href"], headers=headers).json()["record"], item)
            canonical.materialize_effective_week(self.db, "test-base", MONDAY.isoformat(), overlay_fingerprint="correction")
            self.assertEqual(client.get(item["href"], headers=headers).status_code, 409)
            with mock.patch("backend.handlers.api.operational_day", side_effect=sqlite3.OperationalError()):
                self.assertEqual(client.get(path, headers=headers).status_code, 503)

    def test_future_card_excludes_other_workspaces_and_never_changes_day(self):
        self.materialize()
        for kind in ("school", "demo"):
            draft = create_experiment(self.db, name="Synthetic", source="import",
                                      payload={"week_start": "2027-01-04", "days": [0], "slots": [],
                                               "lessons": [], "draft_kind": kind})
            if kind == "school":
                school_id = draft["id"]
        preview = next_week_drafts(self.db, self.db, MONDAY)
        self.assertEqual([r["id"] for r in preview["items"]], [school_id])
        self.assertIn("blockers", preview["items"][0])
        with mock.patch("backend.services.operational_day.preview_publication", side_effect=sqlite3.OperationalError):
            with self.assertRaises(sqlite3.OperationalError):
                next_week_drafts(self.db, self.db, MONDAY)
            self.assertEqual(operational_day(self.db, MONDAY, "Europe/Moscow")["scheduled_count"], 5)
