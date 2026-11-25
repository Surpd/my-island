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
