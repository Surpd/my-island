import copy
import tempfile
import unittest
import os
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import date
from unittest.mock import patch

from backend.database import Database
from backend.services import canonical_schedule as canonical
from backend.services.constructor_publication import (
    initialize_publication_schema, preview_publication, publish_publication,
    PublicationConflict, PublicationBlocked, list_publications, restore_publication, preview_restore,
)
from backend.services.schedule_experiments import create_experiment, create_experiment_week, get_experiment, save_experiment
from backend.services.canonical_weekly_refresh import refresh_current_week
from backend.services.school_template_selection import open_active_school_template, preview_school_template_selection, select_school_template
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.handlers.api import create_router


class ConstructorPublicationTests(unittest.TestCase):
    def test_template_publication_archive_preserves_selection_baseline_for_later_review(self):
        payload = self.draft().get("payload")
        payload["draft_kind"] = "school"
        template = create_experiment(self.database, name="Published school template", source="import", payload=payload)
        self.publish(template, "template")
        # Publishing can make a draft primary directly, with no earlier
        # selection-history row; its immutable publication source is the
        # fallback snapshot for reviewing later edits.
        with self.database.connection() as connection:
            connection.execute("DELETE FROM school_template_selection_history")
        changed = copy.deepcopy(get_experiment(self.database, template["id"])["payload"])
        changed["lessons"][0]["confirmedParticipantIds"] = ["synthetic-student-2"]
        updated = save_experiment(self.database, template["id"], expected_revision=template["revision"], payload=changed)
        active = open_active_school_template(self.database)
        self.assertTrue(active["selection_stale"])
        preview = preview_school_template_selection(self.database, updated["id"])
        self.assertTrue(preview["current"]["snapshot_available"])
        self.assertIn("confirmed_members", {item["field"] for item in preview["changed"][0]["changes"]})

    def test_selected_template_week_is_hidden_until_canonical_publication_and_preview_is_personalized(self):
        """Campus and admin identity previews both read only the published canonical week."""
        template_payload = self.draft().get("payload")
        template_payload["draft_kind"] = "school"
        template = create_experiment(self.database, name="Primary synthetic template", source="import", payload=template_payload)
        comparison = preview_school_template_selection(self.database, template["id"])
        selected = select_school_template(self.database, template["id"], expected_revision=template["revision"],
            expected_current_id=None, expected_current_revision=None,
            expected_candidate_hash=comparison["candidate_hash"])
        self.assertEqual(selected["selected"]["id"], template["id"])
        self.publish(template, "template")
        active = open_active_school_template(self.database)
        week = create_experiment_week(self.database, active["id"], week_start="2026-10-19",
                                      expected_revision=active["revision"])
        self.assertEqual(week["payload"]["source_template"], {"id": active["id"], "revision": active["revision"]})
        for scope, wrong_date in (("template", None), ("week", "2026-10-26")):
            with self.assertRaisesRegex(ValueError, "только на её исходные даты"):
                preview_publication(self.database, week["id"], scope_kind=scope, week_start=wrong_date,
                                    expected_revision=week["revision"])
        changed = copy.deepcopy(week["payload"])
        changed["lessons"][0]["subject"] = "Synthetic changed Math"
        changed["lessons"][0]["audience"]["exclude_student_ids"] = [str(self.other_student["id"])]
        changed["lessons"].append(self.lesson(id="conflict", period=0, subject="Synthetic overlap"))
        week = save_experiment(self.database, week["id"], expected_revision=week["revision"], payload=changed,
                               _preserve_unconfirmed_rosters=True)
        check = preview_publication(self.database, week["id"], scope_kind="week", week_start="2026-10-19",
                                    expected_revision=week["revision"])
        self.assertTrue(check["can_publish"], check["blockers"])
        self.assertTrue(check["diagnostics"], "overlapping synthetic lessons must be surfaced before publication")
        clean = copy.deepcopy(week["payload"])
        clean["lessons"] = [lesson for lesson in clean["lessons"] if lesson.get("id") != "conflict"]
        week = save_experiment(self.database, week["id"], expected_revision=week["revision"], payload=clean,
                               _preserve_unconfirmed_rosters=True)
        check = preview_publication(self.database, week["id"], scope_kind="week", week_start="2026-10-19",
                                    expected_revision=week["revision"])
        self.assertTrue(check["can_publish"], check["blockers"])

        # Prepare actual admin identity previews, then prove their Campus routes
        # return no data from the constructor draft before publication.
        admin = self.database.get_or_create_user(990002, "admin")
        with self.database.connection() as connection:
            self.database.execute(connection, "INSERT INTO user_roles(user_id,role,source) VALUES (?,'admin','test') ON CONFLICT(user_id,role) DO NOTHING", (admin["id"],))
        challenge, _ = self.database.create_admin_login_challenge(admin["id"])
        token, _ = self.database.consume_admin_login_challenge(challenge)
        app = FastAPI()
        app.include_router(create_router(self.database))
        client = TestClient(app)
        admin_headers = {"X-Dev-Auth": f"browser:{token}"}
        previews = {}
        for role, identity in (("student", self.student), ("teacher", self.teacher)):
            response = client.post("/api/admin/previews", headers=admin_headers,
                json={"target_identity_id": str(identity["id"]), "role": role})
            self.assertEqual(response.status_code, 200, response.text)
            previews[role] = response.json()["id"]
        student_headers = {**admin_headers, "X-Preview-Id": previews["student"]}
        teacher_headers = {**admin_headers, "X-Preview-Id": previews["teacher"]}
        with patch.dict(os.environ, {"SCHEDULE_BACKEND": "canonical"}):
            for path, headers in (("/api/student/schedule?start_day=2026-10-19&end_day=2026-10-25", student_headers),
                                  ("/api/teacher/schedule?start_day=2026-10-19&end_day=2026-10-25", teacher_headers)):
                response = client.get(path, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn("Synthetic changed Math", response.text)
                self.assertNotIn("Synthetic overlap", response.text)

        # Publish through the existing canonical publication service, then the
        # real Campus routes and admin identity-preview routes expose only each
        # target's own canonical projection.
        published = publish_publication(self.database, week["id"], scope_kind="week", week_start="2026-10-19",
            expected_revision=week["revision"], preview_token=check["preview_token"], confirmed=True)
        self.assertTrue(published["runtime_readback"]["verified"])
        with patch.dict(os.environ, {"SCHEDULE_BACKEND": "canonical"}):
            student_response = client.get("/api/student/schedule?start_day=2026-10-19&end_day=2026-10-25", headers=student_headers)
            teacher_response = client.get("/api/teacher/schedule?start_day=2026-10-19&end_day=2026-10-25", headers=teacher_headers)
        self.assertEqual(student_response.status_code, 200, student_response.text)
        self.assertEqual(teacher_response.status_code, 200, teacher_response.text)
        student_items, teacher_items = student_response.json()["items"], teacher_response.json()["items"]
        self.assertTrue(any(item.get("activity") == "Synthetic changed Math" for item in student_items), student_response.json())
        self.assertTrue(any(item.get("activity") == "Synthetic changed Math" for item in teacher_items), teacher_response.json())
        self.assertNotIn("trace", student_response.text)
        self.assertNotIn("exclude_student_ids", student_response.text)
        with patch.dict(os.environ, {"SCHEDULE_BACKEND": "canonical"}):
            today = client.get("/api/student/today?day=2026-10-19", headers=student_headers)
        self.assertEqual(today.status_code, 200, today.text)
        self.assertNotIn("exclude_student_ids", today.text)
        self.assertFalse(any(item.get("activity") == "Synthetic overlap" for item in student_items))
        self.assertFalse(any(item.get("activity") == "Synthetic overlap" for item in teacher_items))
        outsider = self.database.create_identity("student", "Synthetic unrelated student", "10B")
        other_preview = client.post("/api/admin/previews", headers=admin_headers,
            json={"target_identity_id": str(outsider["id"]), "role": "student"}).json()["id"]
        with patch.dict(os.environ, {"SCHEDULE_BACKEND": "canonical"}):
            other_response = client.get("/api/student/schedule?start_day=2026-10-19&end_day=2026-10-25",
                headers={**admin_headers, "X-Preview-Id": other_preview})
        self.assertEqual(other_response.status_code, 200, other_response.text)
        self.assertFalse(any(item.get("activity") == "Synthetic changed Math" for item in other_response.json()["items"]))
        self.assertEqual(client.get("/api/teacher/schedule?start_day=2026-10-19", headers=student_headers).status_code, 403)
        self.assertIn(client.get("/api/student/schedule?start_day=2026-10-19", headers={"X-Dev-Auth": "student:1"}).status_code, {401, 403})

    def test_damaged_archive_cannot_create_a_runtime_conflict_owner(self):
        draft = self.draft([self.lesson(review_required=True)])
        self.publish(draft)
        week = self.publish(draft, "week", week_start="2026-10-19")
        outsider = self.database.create_identity("student", "Unassigned synthetic student", "9A")
        with self.database.connection() as connection:
            row = connection.execute("SELECT metadata FROM schedule_constructor_publications WHERE id=?", (week["publication_id"],)).fetchone()
            metadata = json.loads(row["metadata"])
            metadata["published_source"]["records"][0]["known_student_ids"].append(str(outsider["id"]))
            connection.execute("UPDATE schedule_constructor_publications SET metadata=? WHERE id=?", (json.dumps(metadata), week["publication_id"]))
        self.assertEqual(canonical.project_student(self.database, outsider["id"], "2026-10-19")["items"], [])

    def test_damaged_source_is_not_returned_or_restored(self):
        from backend.services.constructor_publication import get_published_source
        draft = self.draft()
        published = self.publish(draft)
        archive = get_published_source(self.database, published["publication_id"])["published_source"]
        with self.database.connection() as connection:
            row = connection.execute("SELECT metadata FROM schedule_constructor_publications WHERE id=?", (published["publication_id"],)).fetchone()
            metadata = json.loads(row["metadata"])
            metadata["published_source"]["payload"]["lessons"][0]["subject"] = "Damaged archive"
            connection.execute("UPDATE schedule_constructor_publications SET metadata=? WHERE id=?", (json.dumps(metadata), published["publication_id"]))
        with self.assertRaises(ValueError):
            get_published_source(self.database, published["publication_id"])
        restore = preview_restore(self.database, published["publication_id"], expected_head_revision=1)
        self.assertFalse(restore["can_restore"])
        self.assertIn("published_source_integrity", {item["code"] for item in restore["blockers"]})
        self.assertEqual(len(archive["records"]), 1)

    def test_all_386_source_records_survive_publication_and_restore_without_assignment(self):
        from backend.services.constructor_publication import get_published_source
        lessons=[self.lesson(id=f"unknown-{i}", audience={"kind":"groups","group_ids":[f"unknown-{i}"]}) for i in range(386)]
        draft=self.draft(lessons)
        p=preview_publication(self.database,draft["id"],scope_kind="template",expected_revision=1)
        self.assertEqual(p["summary"]["source_lessons"],386)
        self.assertEqual(p["summary"]["campus_lessons"],0)
        self.assertEqual(p["summary"]["admin_only_lessons"],386)
        self.assertTrue(p["can_publish"])
        result=self.publish(draft)
        archive=get_published_source(self.database,result["publication_id"])["published_source"]
        self.assertEqual(archive["payload"],draft["payload"])
        self.assertEqual(len(archive["records"]),386)
        self.assertEqual({r["status"] for r in archive["records"]},{"unresolved"})
        self.assertEqual(canonical.read_canonical_template(self.database)["blocks"],{})
        with self.database.connection() as connection:
            connection.execute("UPDATE schedule_experiment_drafts SET payload='{}' WHERE id=?",(draft["id"],))
        self.assertEqual(get_published_source(self.database,result["publication_id"])["published_source"],archive)
        head=canonical.publication_head(self.database,"template")
        restore=preview_restore(self.database,result["publication_id"],expected_head_revision=head["revision"])
        restored=restore_publication(self.database,result["publication_id"],expected_head_revision=head["revision"],preview_token=restore["preview_token"],confirmed=True)
        self.assertEqual(get_published_source(self.database,restored["publication_id"])["published_source"],archive)

    def test_native_postgres_time_values_compare_like_minute_strings(self):
        from datetime import time
        from backend.services.constructor_publication import _semantic, _artifact_blocks
        native = {"weekday": 0, "start_time": time(9, 0), "end_time": time(9, 45), "grade_scope": "9", "assignments": []}
        authored = {"weekday": 0, "slot": {"start": "09:00", "end": "09:45"}, "grade_scope": "9", "assignments": []}
        self.assertEqual(_semantic(native), _semantic(authored))
        self.assertEqual(_artifact_blocks({"block": native})["block"]["slot"], authored["slot"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "disposable.db")
        self.database.initialize()
        initialize_publication_schema(self.database)
        self.group = self.database.create_group("9A", "class")
        self.math = self.database.create_group("9A math", "instructional", base_class_name="9A")
        self.teacher = self.database.create_identity("teacher", "Synthetic Teacher")
        self.other_teacher = self.database.create_identity("teacher", "Other Teacher")
        self.student = self.database.create_identity("student", "Synthetic Student", "9A")
        self.other_student = self.database.create_identity("student", "Other Student", "9A")
        for student in (self.student, self.other_student):
            self.database.create_membership(self.group["id"], student["id"], "student", "synthetic")
        self.database.create_membership(self.math["id"], self.student["id"], "student", "synthetic")

    def tearDown(self):
        self.temp.cleanup()

    def draft(self, lessons=None):
        return create_experiment(self.database, name="Synthetic", source="import", payload={
            "reference_date": "2026-10-19", "days": [0, 1], "classes": [str(self.group["id"])],
            "slots": [{"start": "09:00", "end": "09:45"}, {"start": "10:00", "end": "10:45"}],
            "lessons": lessons if lessons is not None else [self.lesson()]})

    def lesson(self, **overrides):
        return {"id": "l1", "day": 0, "period": 0, "classId": str(self.group["id"]),
                "teacherId": str(self.teacher["id"]), "subject": "Math", "room": "101",
                "audience": {"kind": "classes", "class_ids": [str(self.group["id"])]}, **overrides}

    def publish(self, draft, scope="template", **kwargs):
        args = {"scope_kind": scope, "expected_revision": draft["revision"], **kwargs}
        preview = preview_publication(self.database, draft["id"], **args)
        self.assertTrue(preview["can_publish"], preview["blockers"])
        result = publish_publication(self.database, draft["id"], **args,
                                     preview_token=preview["preview_token"], confirmed=True)
        draft["revision"] = result["draft_revision"]
        return result

    def test_recovery_context_is_bound_to_current_school_admin(self):
        from backend.handlers.constructor_publication_api import create_constructor_publication_router
        from fastapi import HTTPException

        def require_admin(token):
            if token not in {"admin-one", "admin-two"}:
                raise HTTPException(401)
            return token

        app = FastAPI()
        app.include_router(create_constructor_publication_router(lambda: (self.database, self.database), require_admin))
        client = TestClient(app)
        root = "/api/admin/schedule/constructor"
        one = {"X-School-Admin-Session": "admin-one"}
        two = {"X-School-Admin-Session": "admin-two"}
        with patch.dict(os.environ, {"MY_ISLAND_CONSOLIDATED_SCHEDULE": "1"}):
            context = root + "/publication/recovery-context"
            self.assertEqual(client.get(context).status_code, 401)
            response = client.get(context, headers=one)
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            owner = response.json()["owner"]
            self.assertNotEqual(owner, client.get(context, headers=two).json()["owner"])
            draft = self.draft()
            args = {"scope_kind": "template", "expected_revision": draft["revision"]}
            preview = preview_publication(self.database, draft["id"], **args)
            body = {**args, "confirmed": True, "preview_token": preview["preview_token"],
                    "recovery_owner": owner, "operation_id": "browser-pending"}
            path = root + "/" + draft["id"] + "/publication/publish"
            self.assertEqual(client.post(path, headers=two, json=body).status_code, 403)
            self.assertEqual(list_publications(self.database)["items"], [])
            result = client.post(path, headers=one, json=body)
            self.assertEqual(result.status_code, 200, result.text)
            receipt = root + "/publication/receipts/browser-pending"
            self.assertEqual(client.get(receipt, headers=two).status_code, 404)
            self.assertEqual(client.get(receipt, headers=one).json(), result.json())

    def test_receipt_survives_lost_response_and_rejects_key_reuse(self):
        from backend.services.constructor_publication import publication_receipt
        draft = self.draft()
        args = dict(scope_kind="template", expected_revision=draft["revision"])
        preview = preview_publication(self.database, draft["id"], **args)
        command = dict(**args, preview_token=preview["preview_token"], confirmed=True,
                       actor_user_id="admin-one", operation_id="repeatable")
        result = publish_publication(self.database, draft["id"], **command)
        self.assertEqual(publish_publication(self.database, draft["id"], **command), result)
        self.assertEqual(publication_receipt(self.database, "repeatable", "admin-one"), result)
        self.assertIsNone(publication_receipt(self.database, "repeatable", "admin-two"))
        for changes in ({"actor_user_id": "admin-two"}, {"expected_revision": 2}):
            with self.assertRaises(PublicationConflict):
                publish_publication(self.database, draft["id"], **(command | changes))
        self.assertEqual(len(list_publications(self.database)["items"]), 1)

    def test_preview_has_no_writes_and_roundtrip_template_week_runtime(self):
        draft = self.draft()
        with self.database.connection() as conn:
            before = conn.total_changes
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(list_publications(self.database)["items"], [])
        self.assertEqual(get_experiment(self.database, draft["id"])["revision"], 1)
        from backend.services import constructor_publication as publication
        with patch.object(publication, "_compile", wraps=publication._compile) as compile_schedule:
            published = publish_publication(self.database, draft["id"], scope_kind="template", expected_revision=1,
                preview_token=preview["preview_token"], confirmed=True)
        self.assertEqual(compile_schedule.call_count, 1)
        draft["revision"] = published["draft_revision"]
        self.assertTrue(published["runtime_readback"]["verified"])
        self.assertEqual(canonical.canonical_version(self.database)["version_id"], published["published_id"])
        weekly = self.publish(draft, "week", week_start="2026-10-19")
        self.assertTrue(weekly["runtime_readback"]["verified"])
        teacher = canonical.project_teacher(self.database, self.teacher["id"], "2026-10-19")
        student = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        self.assertEqual(teacher["items"][0]["activity"], "Math")
        self.assertEqual(student["items"][0]["activity"], "Math")
        self.assertEqual(len(list_publications(self.database)["items"]), 2)
        self.assertEqual(get_experiment(self.database, draft["id"])["payload"], draft["payload"])

    def test_admin_published_view_returns_effective_override_and_hides_unapproved_week(self):
        draft = self.draft()
        self.publish(draft)
        override = self.draft([self.lesson(subject="Science", room="202")])
        self.publish(override, "week", week_start="2026-10-19")

        admin = self.database.get_or_create_user(990001, "admin")
        with self.database.connection() as connection:
            self.database.execute(connection, "INSERT INTO user_roles(user_id,role,source) VALUES (?,'admin','test') ON CONFLICT(user_id,role) DO NOTHING", (admin["id"],))
        challenge, _ = self.database.create_admin_login_challenge(admin["id"])
        token, _ = self.database.consume_admin_login_challenge(challenge)
        app = FastAPI()
        app.include_router(create_router(self.database))
        client = TestClient(app)
        with patch.dict(os.environ, {"SCHEDULE_BACKEND": "canonical"}):
            response = client.get("/api/admin/schedule/published-view?week_start=2026-10-19", headers={"X-Dev-Auth": f"browser:{token}"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["published"])
        self.assertEqual(body["status"], "authoritative")
        assignment = body["items"][0]["assignments"][0]
        self.assertEqual(assignment["activity"], "Science")
        self.assertEqual(assignment["title"], "Science")
        self.assertEqual(assignment["room"], "202")
        self.assertEqual(assignment["teachers"], ["Synthetic Teacher"])
        self.assertIsInstance(assignment["audience"], dict)

        from backend.services.canonical_schedule import import_canonical_artifact, materialize_effective_week, published_week_view
        import_canonical_artifact(self.database, {"version_id": "approved-source", "status": "authoritative",
            "source_snapshot": {"id": "approved-source-snapshot", "fingerprint": "approved-source-fingerprint"},
            "blocks": self.artifact_blocks_for_draft()}, status="authoritative")
        materialize_effective_week(self.database, "approved-source", "2026-11-02")
        source_week = published_week_view(self.database, "2026-11-02")
        self.assertTrue(source_week["published"])
        self.assertEqual(source_week["status"], "authoritative")

        import_canonical_artifact(self.database, {"version_id": "unconfirmed-draft", "status": "draft",
            "source_snapshot": {"id": "draft-source", "fingerprint": "draft-fingerprint"},
            "blocks": self.artifact_blocks_for_draft()}, status="draft")
        materialize_effective_week(self.database, "unconfirmed-draft", "2026-10-26")
        hidden = published_week_view(self.database, "2026-10-26")
        self.assertFalse(hidden["published"])
        self.assertEqual(hidden["status"], "draft")
        self.assertEqual(hidden["items"], [])
        for projection in (
            canonical.project_student_range(self.database, self.student["id"], "2026-10-26"),
            canonical.project_teacher_range(self.database, self.teacher["id"], "2026-10-26"),
        ):
            self.assertEqual(projection["status"], "not_published")
            self.assertEqual(projection["items"], [])

    def test_selected_edition_is_shared_and_broken_pointer_never_falls_back(self):
        monday = "2026-12-28"
        sunday = "2027-01-03"
        empty = canonical.resolve_published_week(self.database, sunday)
        self.assertEqual((empty.metadata["week_start"], empty.metadata["origin"]), (monday, "missing"))
        draft = self.draft([self.lesson(), self.lesson(id="unresolved", day=1, teacherId="missing")])
        self.publish(draft)
        self.assertEqual(canonical.resolve_published_week(self.database, sunday).metadata["origin"], "template_only")
        weekly = self.publish(draft, "week", week_start=monday)
        def read_all():
            return (canonical.student_campus_projection(self.database, self.student["id"], monday, sunday),
                    canonical.project_teacher_range(self.database, self.teacher["id"], monday, sunday),
                    canonical.published_week_view(self.database, monday))
        student, teacher, admin = read_all()
        edition = admin["publication"]
        self.assertEqual(student["publications"], [edition])
        self.assertEqual(teacher["publication"], edition)
        self.assertEqual((edition["origin"], edition["coverage"], edition["revision"]), ("manual_week", "partial", 1))
        self.assertEqual(edition["publication_id"], weekly["publication_id"])
        self.assertEqual(edition["effective_week_id"], weekly["published_id"])
        self.assertEqual(edition["base_version_id"], weekly["version_id"])
        for column in ("version_id", "published_id"):
            with self.subTest(column=column):
                with self.database.connection() as conn:
                    self.database.execute(conn, f"UPDATE schedule_publication_heads SET {column}=? WHERE scope_key=?",
                                          ("missing", "week:" + monday))
                student, teacher, admin = read_all()
                self.assertEqual(admin["publication"]["origin"], "broken_head")
                self.assertEqual(student["publications"], [admin["publication"]])
                self.assertEqual(teacher["publication"], admin["publication"])
                self.assertTrue(all(r["items"] == [] and r["status"] == "broken_publication" for r in (student, teacher, admin)))
                with self.database.connection() as conn:
                    self.database.execute(conn, f"UPDATE schedule_publication_heads SET {column}=? WHERE scope_key=?",
                                          (weekly[column], "week:" + monday))

    def test_newer_unapproved_materialization_cannot_shadow_approved_history(self):
        for version, status, observed in (("old", "approved_baseline", "2026-11-01"), ("new", "draft", "2026-11-02")):
            canonical.import_canonical_artifact(self.database, {"version_id": version,
                "source_snapshot": {"id": version, "fingerprint": version}, "blocks": self.artifact_blocks_for_draft()}, status=status)
            canonical.materialize_effective_week(self.database, version, "2026-11-02", overlay_observed_at=observed)
        week = canonical.published_week_view(self.database, "2026-11-02")
        self.assertTrue(week["published"])
        self.assertEqual(week["publication"]["origin"], "approved_materialization")
        self.assertEqual(week["publication"]["base_version_id"], "old")
        self.assertIsNone(week["publication"]["publication_id"])

    def test_overview_uses_historical_runtime_week_after_new_template(self):
        def imported(version):
            canonical.import_canonical_artifact(self.database, {
                "version_id": version,
                "source_snapshot": {"id": f"snapshot-{version}", "fingerprint": f"fingerprint-{version}"},
                "blocks": self.artifact_blocks_for_draft(),
            }, status="approved_with_exceptions")
        imported("historical-approved")
        week = canonical.materialize_effective_week(self.database, "historical-approved", "2026-09-21")
        imported("latest-template")
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE canonical_schedule_versions SET approved_at='2099-01-01' WHERE version_id='latest-template'")
        self.assertEqual(canonical.canonical_version(self.database)["version_id"], "latest-template")
        overview = canonical.effective_status(self.database, "2026-09-21")
        runtime = canonical.published_week_view(self.database, "2026-09-21")
        self.assertTrue(runtime["published"])
        self.assertEqual(overview["effective_week"]["effective_week_id"], week["effective_week_id"])
        self.assertEqual(overview["effective_week"]["effective_week_id"], runtime["effective_week_id"])
        self.assertEqual(overview["canonical"]["version_id"], "historical-approved")
        self.assertEqual(overview["status"], runtime["status"])

    def artifact_blocks_for_draft(self):
        return {"draft-block": {"weekday": 0, "slot": {"start": "09:00", "end": "09:45"},
            "grade_scope": "9", "status": "resolved", "mode": "SINGLE",
            "assignments": [{"activity": "Draft Leak", "role": "primary",
                "audience": {"type": "canonical_groups", "canonical_group_ids": [str(self.math["id"])]},
                "teacher_ids": [str(self.teacher["id"])], "source_cells": ["Q1"]}]}}

    def test_stale_preview_revision_directory_and_head_rejected(self):
        draft = self.draft()
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.database.create_identity("teacher", "Directory Changed")
        with self.assertRaises(PublicationConflict):
            publish_publication(self.database, draft["id"], scope_kind="template", expected_revision=1,
                                preview_token=p["preview_token"], confirmed=True)
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.publish(draft)
        with self.assertRaises(PublicationConflict):
            publish_publication(self.database, draft["id"], scope_kind="template", expected_revision=1,
                                preview_token=p["preview_token"], confirmed=True)
        with self.assertRaises(PublicationConflict):
            preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)

    def test_explicit_confirmation_required(self):
        draft = self.draft()
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        with self.assertRaises(ValueError):
            publish_publication(self.database, draft["id"], scope_kind="template", expected_revision=1,
                                preview_token=p["preview_token"])
        self.assertIsNone(canonical.canonical_version(self.database))

    def test_unknown_teacher_review_and_empty_memberships_are_excluded(self):
        for lesson in [self.lesson(teacherId="unknown"), self.lesson(review_required=True),
                       self.lesson(audience={"kind": "groups", "group_ids": ["missing"]})]:
            draft = self.draft([lesson])
            p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
            self.assertTrue(p["can_publish"])
            self.assertEqual(p["summary"]["ready_lessons"], 0)
            self.assertEqual(p["deferred"][0]["action"], "retained_unresolved")
            self.assertTrue(p["diagnostics"])

    def test_technical_class_key_keeps_parallel_scope_and_unresolved_other_grade_does_not_block(self):
        class5 = self.database.create_group("5-А", "class", display_name="5-А", base_class_name="5-А")
        class7 = self.database.create_group("grade7:base:7-А", "class", display_name="7-А", base_class_name="7-А")
        student5 = self.database.create_identity("student", "Synthetic Student 5-А", "5-А")
        student7 = self.database.create_identity("student", "Synthetic Student 7-А", "7-А")
        self.database.create_membership(class5["id"], student5["id"], "student", "synthetic")
        self.database.create_membership(class7["id"], student7["id"], "student", "synthetic")
        confirmed5 = self.lesson(id="grade5", teacherId=str(self.teacher["id"]),
            classId=str(class5["id"]), classIds=[str(class5["id"])],
            audience={"kind": "available_slot", "grade": "5", "class_ids": [str(class5["id"])]})
        unresolved7 = self.lesson(id="unresolved7", classId=str(class7["id"]), classIds=[str(class7["id"])],
            teacherId=str(self.other_teacher["id"]), room="702", audience={"kind": "classes", "class_ids": [str(class7["id"])]},
            audience_snapshot={"student_ids": []})
        draft = self.draft([confirmed5, unresolved7])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(preview["summary"]["ready_lessons"], 1)
        self.assertEqual([item["lesson_id"] for item in preview["deferred"]], ["unresolved7"])
        self.assertTrue(preview["can_publish"])
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        student5_week = canonical.project_student(self.database, student5["id"], "2026-10-19")
        student7_week = canonical.project_student(self.database, student7["id"], "2026-10-19")
        self.assertEqual([item["activity"] for item in student5_week["items"]], ["Math"])
        self.assertEqual(student7_week["items"], [])

    def test_explicit_active_teacher_does_not_warn_about_missing_assignment_row(self):
        draft = self.draft([self.lesson(needsClarification=True,
            clarificationReason="Нет подтверждённого назначения преподавателя группе")])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertTrue(preview["can_publish"])
        self.assertEqual(preview["summary"]["ready_lessons"], 1)
        self.assertEqual(preview["summary"]["deferred_lessons"], 0)
        diagnostic_codes = [item["code"] for item in preview["diagnostics"]]
        self.assertNotIn("teacher_assignment_unverified", diagnostic_codes)
        self.assertNotIn("review_required", diagnostic_codes)
        self.assertEqual(preview["summary"]["info_count"], 0)
        stale_teacher_note = self.lesson(id="stale-teacher-note", needsClarification=True,
            clarificationReason="Не подтверждён преподаватель")
        active_note_draft = self.draft([stale_teacher_note])
        active_note_preview = preview_publication(self.database, active_note_draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(active_note_preview["summary"]["deferred_lessons"], 0)
        self.assertNotIn("review_required", [item["code"] for item in active_note_preview["diagnostics"]])
        compound = self.lesson(id="compound-note", needsClarification=True,
            clarificationReason="Не подтверждён преподаватель; уточните состав группы")
        compound_draft = self.draft([compound])
        compound_preview = preview_publication(self.database, compound_draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(compound_preview["summary"]["deferred_lessons"], 1)
        self.assertIn("уточните состав группы", " ".join(item.get("message", "") for item in compound_preview["diagnostics"]).casefold())
        unknown_teacher = self.lesson(id="unknown-teacher", teacherId="unknown",
            needsClarification=True, clarificationReason="Нет подтверждённого назначения преподавателя группе")
        unknown_draft = self.draft([unknown_teacher])
        unknown_preview = preview_publication(self.database, unknown_draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(unknown_preview["summary"]["ready_lessons"], 0)
        self.assertEqual(unknown_preview["summary"]["deferred_lessons"], 1)
        self.assertIn("unknown_teacher", [item["code"] for item in unknown_preview["diagnostics"]])

    def test_missing_teacher_is_informational_with_confirmed_students_but_generic_review_is_not(self):
        missing_teacher = self.lesson(teacherId="", needsClarification=True,
            clarificationReason="Не подтверждён преподаватель")
        draft = self.draft([missing_teacher])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertTrue(preview["can_publish"])
        self.assertEqual(preview["summary"]["ready_lessons"], 1)
        self.assertEqual(preview["summary"]["deferred_lessons"], 0)
        self.assertIn("missing_teacher", [item["code"] for item in preview["diagnostics"]])
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        projected = canonical.project_student(self.database, self.student["id"], "2026-10-19")["items"]
        self.assertEqual([item["activity"] for item in projected], ["Math"])
        self.assertEqual(projected[0].get("teacher_names") or [], [])

        manual_review = self.lesson(id="manual-review", teacherId="", needsClarification=True,
            clarificationReason="Ручная проверка занятия")
        manual_draft = self.draft([manual_review])
        manual_preview = preview_publication(self.database, manual_draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(manual_preview["summary"]["ready_lessons"], 0)
        self.assertEqual(manual_preview["summary"]["deferred_lessons"], 1)
        self.assertIn("review_required", [item["code"] for item in manual_preview["diagnostics"]])

    def test_missing_teacher_diagnostics_match_club_event_and_independent_practice_rules(self):
        from backend.services.constructor_resolution import apply_resolution, resolution_preview

        ordinary = self.draft([self.lesson(teacherId="")])
        ordinary_preview = preview_publication(self.database, ordinary["id"], scope_kind="template", expected_revision=1)
        self.assertIn("missing_teacher", [item["code"] for item in ordinary_preview["diagnostics"]])

        for lesson in (
            self.lesson(id="club", teacherId="", activityType="club", activityNameSnapshot="Club"),
            self.lesson(id="event", teacherId="", activityType="event", activityNameSnapshot="Event", kind="event"),
        ):
            draft = self.draft([lesson])
            preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
            self.assertNotIn("missing_teacher", [item["code"] for item in preview["diagnostics"]])

        for responsible in ([], [str(self.teacher["id"])]):
            draft = self.draft([self.lesson(id="practice", teacherId="", responsible_teacher_ids=responsible)])
            resolution = resolution_preview(self.database, draft["id"], expected_revision=1, lesson_ids=["practice"])
            draft = apply_resolution(self.database, draft["id"], expected_revision=1,
                preview_token=resolution["preview_token"], lesson_ids=["practice"], action="independent_practice",
                reason="Synthetic independent practice", actor_user_id="synthetic-admin")
            preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
            self.assertNotIn("missing_teacher", [item["code"] for item in preview["diagnostics"]])

    def test_online_and_sdep_day_modes_publish_to_student_and_teacher_campus(self):
        online = self.draft([self.lesson(teacherId=str(self.teacher["id"]), room="")])
        online_payload = copy.deepcopy(online["payload"])
        online_payload["grade_modes"] = {"9": {"0": "online"}}
        online = save_experiment(self.database, online["id"], expected_revision=online["revision"], payload=online_payload)
        online_preview = preview_publication(self.database, online["id"], scope_kind="template", expected_revision=online["revision"])
        self.assertTrue(online_preview["can_publish"], online_preview["blockers"])
        self.assertNotIn("missing_room", [item["code"] for item in online_preview["diagnostics"]])
        self.publish(online)
        self.publish(online, "week", week_start="2026-10-19")
        student_online = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        self.assertEqual(student_online["day_modes"], [{"date": "2026-10-19", "mode": "online"}])
        teacher_online = canonical.project_teacher(self.database, self.teacher["id"], "2026-10-19")
        self.assertEqual(teacher_online["day_modes"][0]["mode"], "online")

        sdep = self.draft([
            self.lesson(id="sdep-a", teacherId="", room=""),
            self.lesson(id="sdep-b", teacherId="", room="", subject="Science"),
        ])
        sdep_payload = copy.deepcopy(sdep["payload"])
        sdep_payload["grade_modes"] = {"9": {"0": "sdep"}}
        sdep = save_experiment(self.database, sdep["id"], expected_revision=sdep["revision"], payload=sdep_payload)
        sdep_preview = preview_publication(self.database, sdep["id"], scope_kind="template", expected_revision=sdep["revision"])
        diagnostic_codes = [item["code"] for item in sdep_preview["diagnostics"]]
        self.assertTrue(sdep_preview["can_publish"], sdep_preview["blockers"])
        self.assertNotIn("missing_teacher", diagnostic_codes)
        self.assertNotIn("missing_room", diagnostic_codes)
        self.assertNotIn("student_overlap", diagnostic_codes)
        self.publish(sdep)
        self.publish(sdep, "week", week_start="2026-10-19")
        student_sdep = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        self.assertEqual(student_sdep["day_modes"], [{"date": "2026-10-19", "mode": "sdep"}])

        conducted = self.draft([
            self.lesson(id="conducted-a", room="101"),
            self.lesson(id="conducted-b", room="101", subject="Science"),
        ])
        conducted_payload = copy.deepcopy(conducted["payload"])
        conducted_payload["grade_modes"] = {"9": {"0": "sdep"}}
        conducted = save_experiment(self.database, conducted["id"], expected_revision=conducted["revision"], payload=conducted_payload)
        conducted_preview = preview_publication(self.database, conducted["id"], scope_kind="template", expected_revision=conducted["revision"])
        conducted_codes = {item["code"] for item in conducted_preview["diagnostics"]}
        self.assertNotIn("student_overlap", conducted_codes)
        self.assertNotIn("teacher_overlap", conducted_codes)
        self.assertNotIn("room_overlap", conducted_codes)
        self.assertEqual(conducted_preview["summary"]["deferred_lessons"], 0)
        self.publish(conducted)
        self.publish(conducted, "week", week_start="2026-10-19")
        self.assertNotIn("teacher_ids", canonical.project_student(self.database, self.student["id"], "2026-10-19")["items"][0])
        self.assertNotIn("room", canonical.project_student(self.database, self.student["id"], "2026-10-19")["items"][0])
        self.assertEqual(canonical.project_teacher(self.database, self.teacher["id"], "2026-10-19")["items"], [])

    def test_one_student_overlap_keeps_unaffected_student_projection_and_marks_only_conflicted_student(self):
        other_group = self.database.create_group("9A English", "instructional", base_class_name="9A")
        self.database.create_membership(other_group["id"], self.student["id"], "student", "synthetic")
        self.database.create_membership(other_group["id"], self.other_student["id"], "student", "synthetic")
        lesson_a = self.lesson(id="math-group", subject="Math", audience={"kind": "groups", "group_ids": [str(self.math["id"])]})
        lesson_b = self.lesson(id="english-group", subject="English", teacherId=str(self.other_teacher["id"]),
            room="102", audience={"kind": "groups", "group_ids": [str(other_group["id"])]})
        draft = self.draft([lesson_a, lesson_b])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertTrue(preview["can_publish"], preview["blockers"])
        self.assertIn("student_overlap", [item["code"] for item in preview["diagnostics"]])
        self.assertEqual(preview["summary"]["deferred_lessons"], 0)
        self.assertEqual(preview["summary"]["conflict_lessons"], 2)
        self.assertEqual(preview["summary"]["affected_students"], 1)
        self.assertEqual(preview["summary"]["direct_exclusions"], 0)
        self.assertEqual(preview["summary"]["cascade_exclusions"], 0)
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        conflicted = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        self.assertEqual([item["state"] for item in conflicted["items"]], ["UNRESOLVED"])
        unaffected = canonical.project_student(self.database, self.other_student["id"], "2026-10-19")
        self.assertEqual([item["activity"] for item in unaffected["items"]], ["English"])

    def test_student_overlap_exemption_does_not_hide_independent_projection_error(self):
        other_group = self.database.create_group("9A English", "instructional", base_class_name="9A")
        self.database.create_membership(other_group["id"], self.student["id"], "student", "synthetic")
        self.database.create_membership(other_group["id"], self.other_student["id"], "student", "synthetic")
        lessons = [
            self.lesson(id="math-overlap", subject="Math",
                        audience={"kind": "groups", "group_ids": [str(self.math["id"])]}),
            self.lesson(id="english-overlap", subject="English", teacherId=str(self.other_teacher["id"]),
                        room="102", audience={"kind": "groups", "group_ids": [str(other_group["id"])]}),
            self.lesson(id="independent-projection-error", day=1, subject="Science"),
        ]
        draft = self.draft(lessons)
        original = canonical._project_student_block

        def corrupt_independent_block(database, context, student, block, day):
            if block["weekday"] == 1:
                return {"state": "UNRESOLVED", "issue_codes": ["UNKNOWN_CANONICAL_GROUP"]}, []
            return original(database, context, student, block, day)

        with patch.object(canonical, "_project_student_block", side_effect=corrupt_independent_block):
            preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)

        self.assertIn("student_overlap", [item["code"] for item in preview["diagnostics"]])
        self.assertIn("projection_mismatch", [item["code"] for item in preview["diagnostics"]])
        self.assertEqual(preview["summary"]["ready_lessons"], 2)
        self.assertEqual(preview["summary"]["deferred_lessons"], 1)
        self.assertEqual([item["lesson_id"] for item in preview["deferred"]], ["independent-projection-error"])

    def test_elective_shared_block_keeps_real_overlap_with_required_lesson(self):
        class_id = str(self.group["id"])
        elective = self.lesson(id="choice", subject="Programming", activityType="elective",
            blockId="choice-block", blockName="Цифровой трек", classId=class_id, classIds=[class_id],
            eligibleGrades=[9], audience={"kind": "students", "student_ids": []},
            teacherId=str(self.other_teacher["id"]), room="E-1")
        required = self.lesson(id="required", subject="Math", teacherId=str(self.teacher["id"]), room="101")
        draft = self.draft([elective, required])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        overlaps = [item for item in preview["diagnostics"] if item["code"] == "student_overlap"]
        self.assertEqual(len(overlaps), 1)
        self.assertEqual(set(overlaps[0]["related_lesson_ids"]), {"choice", "required"})
        self.assertEqual(preview["summary"]["deferred_lessons"], 0)
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        projected = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        self.assertEqual([item["state"] for item in projected["items"]], ["UNRESOLVED"])

    def test_grade_10_and_11_sdep_records_ignore_teacher_room_and_normal_class_occupancy(self):
        class10 = self.database.create_group("10A", "class", base_class_name="10")
        class11 = self.database.create_group("11A", "class", base_class_name="11")
        student10 = self.database.create_identity("student", "Synthetic Student 10", "10A")
        student11 = self.database.create_identity("student", "Synthetic Student 11", "11A")
        self.database.create_membership(class10["id"], student10["id"], "student", "synthetic")
        self.database.create_membership(class11["id"], student11["id"], "student", "synthetic")
        sdep10 = self.lesson(id="sdep-10", classId=str(class10["id"]), classIds=[str(class10["id"])],
            audience={"kind": "classes", "class_ids": [str(class10["id"])]}, subject="English", room="101",
            teacherId=str(self.teacher["id"]), day=4, sdepIndependent=False)
        sdep11 = self.lesson(id="sdep-11", classId=str(class11["id"]), classIds=[str(class11["id"])],
            audience={"kind": "classes", "class_ids": [str(class11["id"])]}, subject="Math", room="101",
            teacherId=str(self.teacher["id"]), day=4, sdepIndependent=False)
        normal9 = self.lesson(id="normal-9", subject="History", room="101", teacherId=str(self.teacher["id"]), day=4)
        draft = self.draft([sdep10, sdep11, normal9])
        payload = copy.deepcopy(draft["payload"])
        payload["classes"] = [str(self.group["id"]), str(class10["id"]), str(class11["id"])]
        payload["days"] = [0, 4]
        payload["grade_modes"] = {"10": {"4": "sdep"}, "11": {"4": "sdep"}}
        draft = save_experiment(self.database, draft["id"], expected_revision=draft["revision"], payload=payload)
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        codes = [item["code"] for item in preview["diagnostics"]]
        self.assertNotIn("teacher_overlap", codes)
        self.assertNotIn("room_overlap", codes)
        self.assertNotIn("student_overlap", codes)
        self.assertEqual(preview["summary"]["deferred_lessons"], 0)
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        for student, expected in ((student10, "English"), (student11, "Math")):
            projection = canonical.project_student(self.database, student["id"], "2026-10-19")
            plan = next(item for item in projection["items"] if item.get("day_mode") == "sdep")
            self.assertEqual(plan["activity"], expected)
            self.assertEqual(projection["day_modes"], [{"date": "2026-10-23", "mode": "sdep"}])
            self.assertNotIn("teacher_ids", plan)
            self.assertNotIn("room", plan)
        teacher_items = canonical.project_teacher(self.database, self.teacher["id"], "2026-10-19")["items"]
        self.assertEqual([item["activity"] for item in teacher_items], ["History"])
        stale_sdep_teacher = self.lesson(id="sdep-invalid-teacher", classId=str(class10["id"]), classIds=[str(class10["id"])],
            audience={"kind": "classes", "class_ids": [str(class10["id"])]}, teacherId="missing-teacher",
            subject="Reading", sdepIndependent=False)
        stale_draft = self.draft([stale_sdep_teacher])
        stale_payload = copy.deepcopy(stale_draft["payload"])
        stale_payload["classes"] = [str(class10["id"])]
        stale_payload["grade_modes"] = {"10": {"0": "sdep"}}
        stale_draft = save_experiment(self.database, stale_draft["id"], expected_revision=stale_draft["revision"], payload=stale_payload)
        stale_preview = preview_publication(self.database, stale_draft["id"], scope_kind="template", expected_revision=stale_draft["revision"])
        stale_codes = {item["code"] for item in stale_preview["diagnostics"]}
        self.assertFalse({"unknown_teacher", "missing_room", "missing_teacher"} & stale_codes)
        self.assertEqual(stale_preview["summary"]["deferred_lessons"], 0)

    def test_online_stale_room_does_not_create_physical_room_collision(self):
        class10 = self.database.create_group("10A", "class", base_class_name="10")
        student10 = self.database.create_identity("student", "Synthetic Student 10", "10A")
        self.database.create_membership(class10["id"], student10["id"], "student", "synthetic")
        online = self.lesson(id="online-9", room="101")
        physical = self.lesson(id="physical-10", classId=str(class10["id"]), classIds=[str(class10["id"])],
            audience={"kind": "classes", "class_ids": [str(class10["id"])]}, teacherId=str(self.other_teacher["id"]), room="101")
        draft = self.draft([online, physical])
        payload = copy.deepcopy(draft["payload"])
        payload["classes"] = [str(self.group["id"]), str(class10["id"])]
        payload["grade_modes"] = {"9": {"0": "online"}}
        draft = save_experiment(self.database, draft["id"], expected_revision=draft["revision"], payload=payload)
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        codes = [item["code"] for item in preview["diagnostics"]]
        self.assertNotIn("room_overlap", codes)
        self.assertNotIn("missing_room", codes)
        self.assertEqual(preview["summary"]["deferred_lessons"], 0)
        competing_online = self.lesson(id="competing-online", classId=str(class10["id"]), classIds=[str(class10["id"])],
            audience={"kind": "classes", "class_ids": [str(class10["id"])]}, teacherId=str(self.teacher["id"]), room="202")
        competing_draft = self.draft([online, competing_online])
        competing_payload = copy.deepcopy(competing_draft["payload"])
        competing_payload["classes"] = [str(self.group["id"]), str(class10["id"])]
        competing_payload["grade_modes"] = {"9": {"0": "online"}, "10": {"0": "online"}}
        competing_draft = save_experiment(self.database, competing_draft["id"], expected_revision=competing_draft["revision"], payload=competing_payload)
        competing_preview = preview_publication(self.database, competing_draft["id"], scope_kind="template", expected_revision=competing_draft["revision"])
        competing_codes = [item["code"] for item in competing_preview["diagnostics"]]
        self.assertTrue({"teacher_overlap", "joint_activity_unconfirmed"} & set(competing_codes))
        self.assertNotIn("room_overlap", competing_codes)

    def test_explicit_sped_friday_waives_only_exact_field_advisories(self):
        class10 = self.database.create_group("10A", "class", base_class_name="10")
        student10 = self.database.create_identity("student", "Synthetic Student 10", "10A")
        self.database.create_membership(class10["id"], student10["id"], "student", "synthetic")
        id10 = str(class10["id"])
        id9 = str(self.group["id"])
        base = dict(classId=id10, classIds=[id10], groupId=id10, groupIds=[id10], day=4,
                    audience={"kind": "classes", "class_ids": [id10]}, teacherId="",
                    room="", needsClarification=True)
        irrelevant = "Не подтверждён преподаватель; Для очного занятия не указан кабинет"
        def trial(*, configured=True, **kwargs):
            draft = self.draft([self.lesson(id="friday-entry", **(base | kwargs))])
            payload = copy.deepcopy(draft["payload"])
            payload.update(classes=[id9, id10], days=[0, 4], grade_modes={"10": {"4": "sdep"}} if configured else {})
            draft = save_experiment(self.database, draft["id"], expected_revision=draft["revision"], payload=payload)
            return preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        def codes(preview):
            return {x["code"] for x in preview["diagnostics"] if x.get("lesson_id") == "friday-entry"}
        self.assertFalse(codes(trial(clarificationReason=irrelevant)) & {"review_required", "missing_room", "missing_teacher"})
        self.assertIn("review_required", codes(trial(clarificationReason=irrelevant + "; Проверить исходную аудиторию")))
        self.assertIn("mixed_day_modes_unsupported", codes(trial(classIds=[id9, id10], clarificationReason=irrelevant)))
        for ordinary in (trial(day=0, clarificationReason=irrelevant), trial(configured=False, clarificationReason=irrelevant)):
            self.assertTrue({"missing_teacher", "missing_room"} <= codes(ordinary))
        self.assertIn("review_required", codes(trial(importError=True, clarificationReason=irrelevant)))

    def test_explicit_online_keeps_student_and_teacher_collisions(self):
        lessons = [
            self.lesson(id="remote-a", format="online", subject="Math", room="101", needsClarification=True,
                        clarificationReason="Кабинет не указан"),
            self.lesson(id="remote-b", format="online", subject="History", room="101"),
        ]
        draft = self.draft(lessons)
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        codes = {x["code"] for x in preview["diagnostics"]}
        self.assertNotIn("room_overlap", codes)
        self.assertNotIn("review_required", codes)
        self.assertIn("teacher_overlap", codes)
        self.assertIn("student_overlap", codes)

    def test_online_room_waiver_preserves_an_independent_review_cause(self):
        draft = self.draft([self.lesson(id="remote-uncertain", format="online", room="", teacherId="",
            needsClarification=True, clarificationReason="Кабинет не указан; Не подтверждён преподаватель; Проверьте аудиторию")])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        diagnostics = {item["code"]: item["message"] for item in preview["diagnostics"]}
        self.assertIn("missing_teacher", diagnostics)
        self.assertIn("Проверьте аудиторию", diagnostics["review_required"])
        self.assertNotIn("Кабинет", diagnostics["review_required"])
        self.assertNotIn("missing_room", diagnostics)

    def test_explicit_lunch_publishes_without_routine_scope_advisory(self):
        lunch = self.lesson(subject="Обед", specialMark="lunch", teacherId="", needsClarification=True,
            clarificationReason="Уточните охват обеда: класс или отдельная группа")
        draft = self.draft([lunch])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertTrue(preview["can_publish"])
        self.assertEqual(preview["summary"]["ready_lessons"], 1)
        self.assertEqual(preview["summary"]["deferred_lessons"], 0)
        self.assertNotIn("lunch_scope_advisory", [item["code"] for item in preview["diagnostics"]])

        unknown = self.lesson(id="unknown-lunch", subject="Обед", specialMark="lunch", teacherId="",
            classId="unknown-class", classIds=["unknown-class"], needsClarification=True,
            clarificationReason="Уточните охват обеда: класс или отдельная группа")
        unknown_draft = self.draft([unknown])
        unknown_preview = preview_publication(self.database, unknown_draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(unknown_preview["summary"]["ready_lessons"], 0)
        self.assertFalse(any(item["code"] == "lunch_scope_advisory" for item in unknown_preview["diagnostics"]))
        self.assertIn("unknown_class", [item["code"] for item in unknown_preview["diagnostics"]])

        unknown_scope = self.lesson(id="unknown-scope-lunch", subject="Обед", specialMark="lunch", teacherId="",
            classId="", classIds=[], audience=None, needsClarification=True,
            clarificationReason="Уточните охват обеда: класс или отдельная группа")
        scope_draft = self.draft([unknown_scope])
        scope_preview = preview_publication(self.database, scope_draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(scope_preview["summary"]["ready_lessons"], 0)
        self.assertFalse(any(item["code"] == "lunch_scope_advisory" for item in scope_preview["diagnostics"]))
        self.assertIn("missing_special_scope", [item["code"] for item in scope_preview["diagnostics"]])

    def test_configured_unknown_lunch_group_is_not_widened_to_class(self):
        lunch = self.lesson(subject="Обед", specialMark="lunch", teacherId="", teacherIds=[], room="",
            classIds=[str(self.group["id"])], audience={"kind": "groups", "group_ids": ["removed-math-group"]},
            needsClarification=True, clarificationReason="Уточните охват обеда: класс или отдельная группа")
        draft = self.draft([lunch])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        codes = [item["code"] for item in preview["diagnostics"]]
        self.assertEqual(preview["summary"]["ready_lessons"], 0)
        self.assertEqual(preview["summary"]["deferred_lessons"], 1)
        self.assertNotIn("lunch_scope_advisory", codes)
        self.assertIn("audience_unresolved", codes)
        self.assertNotIn("missing_teacher", codes)
        self.assertNotIn("missing_room", codes)

    def test_configured_lunch_group_survives_publication_and_only_eligible_member_sees_it(self):
        other = self.database.create_identity("student", "Synthetic other lunch", "9A")
        self.database.create_membership(self.group["id"], other["id"], "student", "lunch-test")
        lunch_group = self.database.create_group("Synthetic lunch subgroup", "subject_group", base_class_name="9A")
        self.database.create_membership(lunch_group["id"], self.student["id"], "student", "lunch-test")
        lunch = self.lesson(id="subgroup-lunch", subject="Обед", specialMark="lunch", teacherId="", teacherIds=[],
            audience={"kind": "groups", "group_ids": [str(lunch_group["id"])]},
            needsClarification=True, clarificationReason="Уточните охват обеда: класс или отдельная группа")
        draft = self.draft([lunch])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(preview["summary"]["ready_lessons"], 1, preview["diagnostics"])
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        personal = canonical.student_campus_projection(self.database, self.student["id"], "2026-10-19", "2026-10-25")
        outsider = canonical.student_campus_projection(self.database, other["id"], "2026-10-19", "2026-10-25")
        self.assertEqual([item["activity"] for item in personal["items"]], ["Обед"])
        self.assertNotIn("Обед", [item["activity"] for item in outsider["items"]])
        shared = canonical.published_week_view(self.database, "2026-10-19")
        self.assertIn("Обед", [assignment["activity"] for block in shared["items"] for assignment in block["assignments"]])

    def test_lunch_exact_legacy_clause_does_not_clear_other_issue(self):
        lunch = self.lesson(id="lunch-double", subject="Обед", specialMark="lunch", teacherId="",
            needsClarification=True, clarificationReason="Уточните охват обеда: класс или отдельная группа; Нет соответствующего звонка")
        draft = self.draft([lunch])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        issue = next(item for item in preview["diagnostics"] if item["code"] == "review_required")
        self.assertIn("Нет соответствующего звонка", issue["message"])
        self.assertNotIn("Уточните охват", issue["message"])
        self.assertEqual(preview["summary"]["ready_lessons"], 0)

    def test_verified_lunch_class_and_time_waive_only_legacy_scope_not_unknown_roster(self):
        from backend.services.schedule_normalization import normalization_preview

        empty_class = self.database.create_group("8A", "class", base_class_name="8A")
        class_id = str(empty_class["id"])
        source = {
            "reference_date": "2026-10-19", "days": [3], "classes": [class_id],
            "slots": [{"start": "11:50", "end": "12:35"}],
            "lessons": [self.lesson(id="grade8-thursday-lunch", day=3, period=0, classId=class_id,
                classIds=[class_id], subject="Обед", specialMark="lunch", teacherId="", teacherIds=[], room="",
                audience={"kind": "classes", "class_ids": [class_id]}, needsClarification=True,
                clarificationReason="Уточните охват обеда: класс или отдельная группа")],
        }
        review = normalization_preview({"revision": 1, "payload": source}, verified_classes={class_id: "8"})
        self.assertEqual(len(review["scope_proposals"]), 1)
        lunch_proposal = review["scope_proposals"][0]
        self.assertEqual((lunch_proposal["lesson_id"], lunch_proposal["kind"], lunch_proposal["field"]),
                         ("grade8-thursday-lunch", "lunch_verified", "lunch_scope_warning"))
        self.assertTrue(lunch_proposal["current_note_eligible"])

        draft = create_experiment(self.database, name="Verified lunch scope", source="import", payload=source)
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        codes = [item["code"] for item in preview["diagnostics"]]
        self.assertIn("audience_unresolved", codes)
        self.assertNotIn("review_required", codes)
        self.assertEqual(preview["summary"]["deferred_lessons"], 1)
        self.assertEqual(preview["deferred"][0]["cause_codes"], ["audience_unresolved"])

        empty_group = self.database.create_group("8A lunch subgroup", "subject_group", base_class_name="8A")
        guarded_rules = {
            "explicit-group": {"kind": "groups", "group_ids": [str(empty_group["id"])]},
            "remainder": {"kind": "remaining", "class_ids": [class_id], "partition_group_ids": []},
            "exclusion": {"kind": "classes", "class_ids": [class_id],
                          "exclude_student_ids": [str(self.student["id"])]},
        }
        for case, audience in guarded_rules.items():
            with self.subTest(case=case):
                guarded = copy.deepcopy(source)
                guarded["lessons"][0]["id"] = f"guarded-{case}"
                guarded["lessons"][0]["audience"] = audience
                draft = create_experiment(self.database, name=f"Guarded lunch {case}", source="import", payload=guarded)
                preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
                self.assertIn("review_required", [item["code"] for item in preview["diagnostics"]])

    def test_unverified_import_reference_is_deferred_without_subject_restrictions(self):
        stale_math = self.database.create_group("Synthetic math split", "subject_group", subject="Математика",
            subject_subgroup="1", base_class_name="9A")
        self.database.create_membership(stale_math["id"], self.student["id"], "student", "synthetic")
        lesson = self.lesson(subject="Биология", groupId=str(stale_math["id"]), groupIds=[str(stale_math["id"])],
            audience={"kind": "groups", "group_ids": [str(stale_math["id"])]}, needsClarification=True,
            clarificationReason="Для этого предмета не подтверждена учебная группа; прежняя математическая группа удалена")
        draft = self.draft([lesson])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        issue = next(item for item in preview["diagnostics"] if item["code"] == "stale_group_subject_mismatch")
        self.assertEqual(issue["severity"], "info")
        self.assertIn("Биология", issue["message"])
        self.assertIn("9A", issue["message"])
        self.assertEqual(preview["summary"]["campus_lessons"], 0)
        self.assertEqual(preview["summary"]["admin_only_lessons"], 1)
        self.assertEqual(preview["deferred"][0]["cause_codes"], ["stale_group_subject_mismatch"])

    def test_explicit_groups_do_not_assert_exhaustive_coverage_of_other_pupils(self):
        first = self.database.create_group("english:1", "subject_group", base_class_name="9A")
        second = self.database.create_group("english:2", "subject_group", base_class_name="9A")
        third = self.database.create_identity("student", "Third Synthetic Student", "9A")
        self.database.create_membership(self.group["id"], third["id"], "student", "synthetic")
        self.database.create_membership(first["id"], self.student["id"], "student", "synthetic")
        self.database.create_membership(second["id"], self.other_student["id"], "student", "synthetic")
        third_teacher = self.database.create_identity("teacher", "Third Synthetic Teacher")
        draft = self.draft([
            self.lesson(id="english1", subject="English", audience={"kind":"groups","group_ids":[str(first["id"])]}),
            self.lesson(id="english2", subject="English", teacherId=str(self.other_teacher["id"]), room="902", audience={"kind":"groups","group_ids":[str(second["id"])]}),
            self.lesson(id="russian", subject="Russian", teacherId=str(third_teacher["id"]), room="903",
                        audience={"kind":"students","student_ids":[str(third["id"])]}),
        ])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(preview["summary"]["ready_lessons"], 3)
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        for student, activity in ((self.student,"English"),(self.other_student,"English"),(third,"Russian")):
            result = canonical.project_student(self.database, student["id"], "2026-10-19")
            self.assertEqual([r["activity"] for r in result["items"] if r["state"]=="ACTIVITY"], [activity])
            self.assertEqual(result["issues"], [])

    def test_lunch_is_fallback_across_independent_class_blocks(self):
        other_class = self.database.create_group("9B", "class", base_class_name="9B")
        peer = self.database.create_identity("student", "Synthetic 9B", "9B")
        self.database.create_membership(other_class["id"], peer["id"], "student", "synthetic")
        draft = self.draft([self.lesson(), self.lesson(id="lunch", subject="Lunch", specialMark="lunch", teacherId="",
            classId="", classIds=[str(self.group["id"]),str(other_class["id"])],
            audience={"kind":"classes","class_ids":[str(self.group["id"]),str(other_class["id"])]})])
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        for student, activity in ((self.student,"Math"),(peer,"Lunch")):
            result = canonical.project_student(self.database, student["id"], "2026-10-19")
            self.assertEqual([r["activity"] for r in result["items"] if r["state"]=="ACTIVITY"], [activity])
            self.assertEqual(result["issues"], [])

    def test_lunch_does_not_capture_available_slot_or_adjacent_interval(self):
        draft = self.draft([
            self.lesson(id="grouped", audience={"kind":"groups","group_ids":[str(self.math["id"])]}),
            self.lesson(id="other",subject="Other",teacherId=str(self.other_teacher["id"]),room="102",audience={"kind":"available_slot","class_ids":[str(self.group["id"])],"grade":"9"}),
            self.lesson(id="lunch",subject="Lunch",specialMark="lunch",teacherId="stale-teacher",room="101",classId=str(self.group["id"]),classIds=[str(self.group["id"])]),
            self.lesson(id="adjacent",subject="Adjacent",start="09:45",end="09:55",room="103",audience={"kind":"available_slot","class_ids":[str(self.group["id"])],"grade":"9"}),
        ])
        preview=preview_publication(self.database,draft["id"],scope_kind="template",expected_revision=1)
        self.assertEqual(preview["summary"]["ready_lessons"],4)
        self.assertNotIn("unknown_teacher",[item["code"] for item in preview["diagnostics"]])
        self.assertNotIn("room_overlap",[item["code"] for item in preview["diagnostics"]])
        self.publish(draft)
        self.publish(draft,"week",week_start="2026-10-19")
        for student, expected in ((self.student,["Math","Adjacent"]),(self.other_student,["Other","Adjacent"])):
            result=canonical.project_student(self.database,student["id"],"2026-10-19")
            self.assertEqual([r["activity"] for r in result["items"] if r["state"]=="ACTIVITY"],expected)
            self.assertEqual(result["issues"],[])

    def test_distinct_administrative_classes_in_one_parallel_are_independent_units(self):
        other_class=self.database.create_group("9B","class",base_class_name="9B")
        peer=self.database.create_identity("student","Synthetic 9B","9B")
        self.database.create_membership(other_class["id"],peer["id"],"student","synthetic")
        draft=self.draft([self.lesson(),self.lesson(id="review",classId=str(other_class["id"]),classIds=[str(other_class["id"])],
            teacherId=str(self.other_teacher["id"]),room="902",audience={"kind":"classes","class_ids":[str(other_class["id"])]},needsClarification=True)])
        preview=preview_publication(self.database,draft["id"],scope_kind="template",expected_revision=1)
        self.assertEqual(preview["summary"]["ready_lessons"],1)
        self.assertEqual([d["lesson_id"] for d in preview["deferred"]],["review"])

    def test_matching_rows_are_not_joint_until_administrator_links_them(self):
        other_class=self.database.create_group("10A","class",base_class_name="10A")
        peer=self.database.create_identity("student","Synthetic 10A","10A")
        self.database.create_membership(other_class["id"],peer["id"],"student","synthetic")
        draft=self.draft([self.lesson(),self.lesson(id="joint",classId=str(other_class["id"]),classIds=[str(other_class["id"])],
            audience={"kind":"classes","class_ids":[str(other_class["id"])]})])
        p=preview_publication(self.database,draft["id"],scope_kind="template",expected_revision=1)
        self.assertEqual(p["summary"]["ready_lessons"],0)
        self.assertIn("teacher_overlap",[i["code"] for i in p["diagnostics"]])

    def test_explicit_joint_activity_link_preserves_each_class_audience(self):
        from backend.services.constructor_resolution import apply_resolution, resolution_preview
        other_class=self.database.create_group("10A","class",base_class_name="10A")
        peer=self.database.create_identity("student","Synthetic 10A","10A")
        self.database.create_membership(other_class["id"],peer["id"],"student","synthetic")
        draft=self.draft([self.lesson(),self.lesson(id="joint",classId=str(other_class["id"]),classIds=[str(other_class["id"])],
            audience={"kind":"classes","class_ids":[str(other_class["id"])]})])
        ids=["l1","joint"]
        resolution=resolution_preview(self.database,draft["id"],expected_revision=1,lesson_ids=ids)
        draft=apply_resolution(self.database,draft["id"],expected_revision=1,preview_token=resolution["preview_token"],
            lesson_ids=ids,action="joint",reason="Совместное занятие подтверждено администратором",actor_user_id="synthetic-admin")
        p=preview_publication(self.database,draft["id"],scope_kind="template",expected_revision=draft["revision"])
        self.assertEqual(p["summary"]["ready_lessons"],2)
        self.assertIn("joint_activity",[i["code"] for i in p["diagnostics"]])
        self.publish(draft)
        self.publish(draft,"week",week_start="2026-10-19")
        for student in (self.student,peer):
            result=canonical.project_student(self.database,student["id"],"2026-10-19")
            self.assertEqual([i["activity"] for i in result["items"] if i["state"]=="ACTIVITY"],["Math"])
        teacher=canonical.project_teacher(self.database,self.teacher["id"],"2026-10-19")
        self.assertEqual(len(teacher["items"]),1)
        self.assertEqual(set(teacher["items"][0]["audience_group_ids"]),{str(self.group["id"]),str(other_class["id"])})
        self.assertEqual(teacher["issues"],[])

    def test_joint_activity_is_atomic_and_missing_or_different_rooms_do_not_confirm_it(self):
        other_class=self.database.create_group("10A","class",base_class_name="10A")
        peer=self.database.create_identity("student","Synthetic 10A","10A")
        self.database.create_membership(other_class["id"],peer["id"],"student","synthetic")
        other=self.lesson(id="joint",classId=str(other_class["id"]),classIds=[str(other_class["id"])],
            audience={"kind":"classes","class_ids":[str(other_class["id"])]})
        for room,code in (("","joint_activity_unconfirmed"),("Different","teacher_overlap")):
            draft=self.draft([self.lesson(),{**other,"room":room}])
            p=preview_publication(self.database,draft["id"],scope_kind="template",expected_revision=1)
            self.assertEqual(p["summary"]["ready_lessons"],0)
            self.assertIn(code,[i["code"] for i in p["diagnostics"]])
        draft=self.draft([self.lesson(),{**other,"teacherId":str(self.other_teacher["id"]),"room":"902","needsClarification":True}])
        p=preview_publication(self.database,draft["id"],scope_kind="template",expected_revision=1)
        self.assertEqual(p["summary"]["ready_lessons"],1)
        self.assertEqual(p["summary"]["deferred_lessons"],1)
        self.assertEqual({tuple(d["cause_codes"]) for d in p["deferred"]},{("review_required",)})

    def test_compile_backfills_details_without_mutating_authored_payload(self):
        from backend.services.constructor_publication import _compile
        payload=self.draft([self.lesson(canonical={"assignment":{"metadata":{"joint_activity_id":"obsolete-link","constructor_details":{"description":"Preserved source detail"}}}})])["payload"]
        before=copy.deepcopy(payload)
        blocks,_,_=_compile(self.database,payload,None,"template","2026-10-19")
        self.assertEqual(payload,before)
        assignment=next(iter(blocks.values()))["assignments"][0]
        self.assertEqual(assignment["metadata"]["constructor_details"]["description"],"Preserved source detail")
        self.assertNotIn("joint_activity_id",assignment["metadata"])

    def test_grade_filter_cannot_silently_drop_a_confirmed_audience_member(self):
        wrong_class=self.database.create_group("10A","class",base_class_name="10A")
        self.database.create_membership(wrong_class["id"],self.student["id"],"student","synthetic")
        draft=self.draft([self.lesson(classId=str(wrong_class["id"]),classIds=[str(wrong_class["id"])],
            audience={"kind":"classes","class_ids":[str(wrong_class["id"])]})])
        p=preview_publication(self.database,draft["id"],scope_kind="template",expected_revision=1)
        self.assertEqual(p["summary"]["ready_lessons"],0)
        self.assertIn("projection_mismatch",[i["code"] for i in p["diagnostics"]])

    def test_dated_membership_overrides_agree_in_save_preview_and_campus(self):
        with self.database.connection() as connection:
            for student, action in ((self.other_student, "include"), (self.student, "exclude")):
                self.database.execute(connection, """INSERT INTO membership_overrides
                    (identity_id,group_id,member_role,action,reason,active,valid_from,valid_until)
                    VALUES (?,?,'student',?,'synthetic',TRUE,'2026-10-20','2026-10-20')""",
                    (student["id"], self.math["id"], action))
        rule = {"kind": "groups", "group_ids": [str(self.math["id"])]}
        draft = self.draft([self.lesson(id="monday", audience=rule),
                            self.lesson(id="tuesday", day=1, audience=rule)])
        draft = save_experiment(self.database, draft["id"], expected_revision=draft["revision"], payload=draft["payload"])
        snapshots = {row["id"]: row["audience_snapshot"]["student_ids"] for row in draft["payload"]["lessons"]}
        self.assertEqual(snapshots, {"monday": [str(self.student["id"])], "tuesday": [str(self.other_student["id"])]})
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        for student, expected_day in ((self.student, "2026-10-19"), (self.other_student, "2026-10-20")):
            items = canonical.project_student(self.database, student["id"], "2026-10-19")["items"]
            self.assertEqual([item["date"] for item in items if item["state"] == "ACTIVITY"], [expected_day])

    def test_same_activity_text_student_conflict_is_personal_and_does_not_drop_rows(self):
        draft=self.draft([self.lesson(subject="Creative"),self.lesson(id="second",subject="Creative",room="202",teacherId=str(self.other_teacher["id"]))])
        p=preview_publication(self.database,draft["id"],scope_kind="template",expected_revision=1)
        self.assertEqual(p["summary"]["ready_lessons"],2)
        self.assertIn("student_overlap",[i["code"] for i in p["diagnostics"]])
        self.assertEqual(p["summary"]["conflict_lessons"],2)

    def test_parallel_electives_do_not_conflict_by_class_but_keep_teacher_validation(self):
        class_id = str(self.group["id"])
        courses = [self.lesson(id=f"elective-{index}", subject=f"Elective {index}", activityType="elective",
                               activityNameSnapshot=f"Elective {index}",
                               blockId="elective-common", blockName="Курс по выбору",
                               classId=class_id, classIds=[class_id], eligibleGrades=[9],
                               confirmedParticipantIds=[],
                               audience={"kind": "classes", "class_ids": [class_id]},
                               teacherId="", room=f"E-{index}") for index in range(3)]
        draft = self.draft(courses)
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertNotIn("student_overlap", [item["code"] for item in preview["diagnostics"]])

        confirmed_pair = self.draft([{**courses[0], "confirmedParticipantIds": [str(self.student["id"])]},
            {**courses[1], "confirmedParticipantIds": [str(self.student["id"])]}])
        confirmed_preview = preview_publication(self.database, confirmed_pair["id"], scope_kind="template", expected_revision=1)
        self.assertNotIn("student_overlap", [item["code"] for item in confirmed_preview["diagnostics"]])
        self.assertEqual(confirmed_pair["payload"]["lessons"][0]["confirmedParticipantIds"], [str(self.student["id"])])

        assigned = {**courses[0], "teacherId": str(self.teacher["id"])}
        conflicting = self.draft([assigned, {**courses[1], "teacherId": str(self.teacher["id"]), "room": "E-2"}])
        conflict_preview = preview_publication(self.database, conflicting["id"], scope_kind="template", expected_revision=1)
        self.assertIn("teacher_overlap", [item["code"] for item in conflict_preview["diagnostics"]])
        room_conflict = self.draft([{**courses[0], "room": "shared"}, {**courses[1], "room": "shared"}])
        room_preview = preview_publication(self.database, room_conflict["id"], scope_kind="template", expected_revision=1)
        self.assertIn("room_overlap", [item["code"] for item in room_preview["diagnostics"]])

    def test_shared_elective_blocks_project_once_per_cohort_and_keep_teacher_lanes_private(self):
        groups = {
            name: self.database.create_group(name, "class", base_class_name=name[0])
            for name in ("5A", "6A", "7A", "8A")
        }
        students = {}
        for name in groups:
            students[name] = self.database.create_identity("student", f"Synthetic {name}", name)
            self.database.create_membership(groups[name]["id"], students[name]["id"], "student", "elective-test")
        teachers = [self.database.create_identity("teacher", f"Elective teacher {index}") for index in range(8)]
        inactive_teacher = self.database.create_identity("teacher", "Inactive elective teacher")
        with self.database.connection() as connection:
            connection.execute("UPDATE identities SET status='inactive' WHERE id=?", (inactive_teacher["id"],))
        lessons = []
        for cohort, grade_classes, day in (
            ("block-56", ("5A", "6A"), 0),
            ("block-78", ("7A", "8A"), 2),
        ):
            for index, title in enumerate(("Cambridge Flyers", "Логикон", "Лингвистический детектив", "Юннаты XXI")):
                legacy = cohort == "block-56" and index == 0
                lesson = self.lesson(
                    id=f"{cohort}-{index}", day=day, period=0, subject=title,
                    activityNameSnapshot=title, classId=str(groups[grade_classes[0]]["id"]),
                    classIds=[str(groups[name]["id"]) for name in grade_classes],
                    audience={"kind": "students", "student_ids": []}, eligibleGrades=[int(name[0]) for name in grade_classes],
                    teacherId=("" if cohort == "block-56" and index == 0 else
                               str(inactive_teacher["id"]) if cohort == "block-56" and index == 1 else
                               str(teachers[index if cohort == "block-56" else index + 4]["id"])),
                    room=f"{cohort}-{index}", blockId=cohort,
                    blockName="Цифровой трек" if cohort == "block-78" else "",
                    activityType="elective",
                )
                if legacy:
                    lesson.pop("activityType")
                lessons.append(lesson)
        draft = create_experiment(self.database, name="Shared electives", source="import", payload={
            "reference_date": "2026-10-19", "days": [0, 2],
            "classes": [str(group["id"]) for group in groups.values()],
            "slots": [{"start": "09:00", "end": "09:45"}], "lessons": lessons,
        })
        original_payload = copy.deepcopy(draft["payload"])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        self.assertTrue(preview["can_publish"], preview["blockers"])
        self.assertNotIn("student_overlap", {item["code"] for item in preview["diagnostics"]})
        self.assertIn("missing_teacher", {item["code"] for item in preview["diagnostics"]})
        self.assertIn("unknown_teacher", {item["code"] for item in preview["diagnostics"]})
        self.assertEqual(get_experiment(self.database, draft["id"])["payload"], original_payload)
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        shared = canonical.published_week_view(self.database, "2026-10-19")
        self.assertCountEqual([assignment["activity"] for block in shared["items"] for assignment in block["assignments"]],
                              [lesson["subject"] for lesson in lessons])
        for name, title in (("5A", "Курс по выбору"), ("6A", "Курс по выбору"),
                            ("7A", "Цифровой трек"), ("8A", "Цифровой трек")):
            elective_items = [item for item in canonical.project_student(self.database, students[name]["id"], "2026-10-19")["items"]
                              if item.get("activity_type") == "elective"]
            self.assertEqual(len(elective_items), 1, name)
            self.assertEqual(elective_items[0]["activity"], title)
            campus = canonical.student_campus_projection(self.database, students[name]["id"], "2026-10-19", "2026-10-25")
            self.assertEqual([item["activity"] for item in campus["items"] if item.get("activity_type") == "elective"], [title])
            for hidden in ("teacher_ids", "teacher_names", "room", "trace"):
                self.assertNotIn(hidden, elective_items[0])
            self.assertFalse(any(direction in str(elective_items[0]) for direction in
                                 ("Cambridge Flyers", "Логикон", "Лингвистический детектив", "Юннаты XXI")))
        self.assertEqual(canonical.project_student(self.database, self.student["id"], "2026-10-19")["items"], [])
        for index, teacher in enumerate(teachers):
            assigned = [item for item in canonical.project_teacher(self.database, teacher["id"], "2026-10-19")["items"]
                        if item.get("activity", "").startswith(("Cambridge", "Логикон", "Лингвистический", "Юннаты"))]
            expected = 0 if index in {0, 1} else 1
            self.assertEqual(len(assigned), expected)
            if expected:
                self.assertIn(assigned[0]["activity"], {lesson["subject"] for lesson in lessons})

    def test_unlinked_elective_is_not_auto_grouped_by_name_or_time(self):
        class_id = str(self.group["id"])
        lessons = [self.lesson(id=f"unlinked-{index}", subject="Programming", activityType="elective",
                               classId=class_id, classIds=[class_id], eligibleGrades=[9], blockName="Цифровой трек",
                               teacherId=str(self.teacher["id"]), room=f"D-{index}") for index in range(2)]
        draft = self.draft(lessons)
        before = copy.deepcopy(draft["payload"])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual([item["code"] for item in preview["diagnostics"]].count("elective_block_unlinked"), 2)
        self.assertEqual(len(preview["deferred"]), 2)
        self.assertEqual(get_experiment(self.database, draft["id"])["payload"], before)

    def test_legacy_published_digital_lanes_remain_a_neutral_student_card(self):
        from backend.services.canonical_schedule import import_canonical_artifact, materialize_effective_week

        class_id = str(self.group["id"])
        block = {"weekday": 0, "slot": {"start": "09:00", "end": "09:45"}, "grade_scope": "9",
            "status": "resolved", "mode": "PARALLEL", "assignments": [
                {"activity": "F54 · Программирование", "role": "primary", "canonical_group_ids": [class_id],
                 "teacher_ids": [str(self.teacher["id"])], "metadata": {"activity_type": "elective",
                    "constructor_details": {"activityType": "elective", "electiveDirection": "digital"},
                    "teachers": ["Synthetic Teacher"], "room": "D-54"}},
                {"activity": "ШУМ · Медиамастерская", "role": "primary", "canonical_group_ids": [class_id],
                 "teacher_ids": [str(self.other_teacher["id"])], "metadata": {"activity_type": "elective",
                    "constructor_details": {"activityType": "elective", "electiveDirection": "digital"},
                    "teachers": ["Other Synthetic Teacher"], "room": "D-55"}},
            ]}
        import_canonical_artifact(self.database, {"version_id": "legacy-digital-publication",
            "status": "authoritative", "source_snapshot": {"id": "legacy-digital-source", "fingerprint": "legacy-digital"},
            "blocks": {"legacy-digital-block": block}}, status="authoritative")
        materialize_effective_week(self.database, "legacy-digital-publication", "2026-10-19")

        student_projection = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        cards = [item for item in student_projection["items"] if item.get("activity_type") == "elective"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["activity"], "Цифровой трек")
        self.assertTrue(cards[0]["provenance"]["requires_administrative_block_mapping"])
        self.assertNotIn("teacher_ids", cards[0])
        self.assertNotIn("teacher_names", cards[0])
        self.assertNotIn("room", cards[0])
        self.assertNotIn("F54", str(cards[0]))
        self.assertNotIn("Медиамастерская", str(cards[0]))
        self.assertEqual(student_projection["optional_activities"], [])

        teacher_projection = canonical.project_teacher(self.database, self.teacher["id"], "2026-10-19")
        teacher_items = [item for item in teacher_projection["items"] if item.get("activity") == "F54 · Программирование"]
        self.assertEqual(len(teacher_items), 1)
        self.assertEqual(teacher_items[0].get("room"), "D-54")

    def test_renamed_legacy_elective_persists_explicit_type_through_preview_publish_and_campus(self):
        legacy = self.lesson(id="legacy-elective", subject="Project workshop", blockId="legacy-workshops",
            blockName="Курсы по выбору", eligibleGrades=[9], confirmedParticipantIds=[])
        draft = self.draft([legacy])
        initial = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertTrue(initial["can_publish"], initial["blockers"])

        # This is the payload the constructor saves when the recognized legacy
        # block is renamed. Subsequent reads must not depend on its old label.
        changed = copy.deepcopy(get_experiment(self.database, draft["id"])["payload"])
        changed["lessons"][0].update(activityType="elective", blockName="Мастерские")
        saved = save_experiment(self.database, draft["id"], expected_revision=1, payload=changed)
        reopened = get_experiment(self.database, draft["id"])
        self.assertEqual(reopened["revision"], saved["revision"])
        self.assertEqual(reopened["payload"]["lessons"][0]["activityType"], "elective")
        self.assertEqual(reopened["payload"]["lessons"][0]["blockName"], "Мастерские")

        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=saved["revision"])
        self.assertTrue(preview["can_publish"], preview["blockers"])
        published = publish_publication(self.database, draft["id"], scope_kind="template",
            expected_revision=saved["revision"], preview_token=preview["preview_token"], confirmed=True)
        current_revision = get_experiment(self.database, draft["id"])["revision"]
        week_preview = preview_publication(self.database, draft["id"], scope_kind="week",
            expected_revision=current_revision, week_start="2026-10-19")
        self.assertTrue(week_preview["can_publish"], week_preview["blockers"])
        publish_publication(self.database, draft["id"], scope_kind="week", expected_revision=current_revision,
            week_start="2026-10-19", preview_token=week_preview["preview_token"], confirmed=True)
        template = canonical.read_canonical_template(self.database, published["published_id"])
        assignment = next(item for block in template["blocks"].values() for item in block.get("assignments") or [])
        metadata = assignment.get("metadata") or {}
        details = metadata.get("constructor_details") or {}
        self.assertEqual(metadata.get("activity_type"), "elective")
        self.assertEqual(details.get("blockName"), "Мастерские")

        campus = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        cards = [item for item in campus["items"] if item.get("activity_type") == "elective"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["activity"], "Мастерские")

    def test_available_slot_projection_cache_matches_uncached_with_individual_dependencies(self):
        from backend.services.constructor_publication import _projection_mismatches

        class_id = str(self.group["id"])
        first_id, second_id = str(self.student["id"]), str(self.other_student["id"])
        def assignment(lesson_id, activity, rule):
            return {"activity": activity, "role": "primary", "canonical_group_ids": [class_id],
                "teacher_ids": [], "metadata": {"constructor_lesson_id": lesson_id,
                    "audience_rule": rule}}
        available = assignment("available", "Available choice", {
            "kind": "available_slot", "grade": "9", "class_group_id": class_id})
        included = assignment("included", "Included assignment", {
            "kind": "students", "include_student_ids": [first_id]})
        excluded = assignment("excluded", "Excluded assignment", {
            "kind": "students", "exclude_student_ids": [second_id]})
        def block(activity):
            return {"weekday": 0, "slot": {"start": "09:00", "end": "09:45"},
                "grade_scope": "9", "assignments": [activity]}
        blocks = {"available": block(available), "included": block(included), "excluded": block(excluded)}
        audience = {"available": {"student_ids": [second_id]},
                    "included": {"student_ids": [first_id]},
                    "excluded": {"student_ids": [first_id]}}
        with patch.object(canonical, "_project_student_block", wraps=canonical._project_student_block) as cached_project:
            cached = _projection_mismatches(self.database, blocks, audience, date.fromisoformat("2026-10-19"))
        cached_available_students = {str(call.args[2]["id"]) for call in cached_project.call_args_list
            if call.args[3].get("block_key") == "available"}
        uncached = _projection_mismatches(self.database, blocks, audience,
            date.fromisoformat("2026-10-19"), use_projection_cache=False)
        self.assertEqual(cached, uncached)
        self.assertEqual(cached_available_students, {first_id, second_id})

    def test_elective_success_cannot_mask_required_assignment_projection_in_same_source_block(self):
        from backend.services.constructor_publication import _projection_mismatches

        class_id = str(self.group["id"])
        assignments = [
            {"activity": "Цифровое направление", "canonical_group_ids": [class_id], "teacher_ids": [],
             "metadata": {"activity_type": "elective", "constructor_details": {"activityType": "elective", "blockId": "digital-shared"},
                          "constructor_lesson_id": "elective-lane"}},
            {"activity": "Обязательная математика", "canonical_group_ids": [class_id], "teacher_ids": [str(self.teacher["id"])],
             "metadata": {"activity_type": "lesson", "constructor_lesson_id": "required-math"}},
        ]
        block = {"weekday": 0, "slot": {"start": "09:00", "end": "09:45"}, "grade_scope": "9",
                 "assignments": assignments}
        audience = {"elective-lane": {"student_ids": []}, "required-math": {"student_ids": [str(self.student["id"])]}}

        def projector(_database, _context, _student, candidate, _day):
            if len(candidate.get("patch", {}).get("assignments") or []) == 2:
                return {"state": "ACTIVITY", "activity": "Цифровой трек", "activity_type": "elective"}, []
            return {"state": "UNRESOLVED", "activity": None, "issue_codes": ["UNKNOWN_CANONICAL_GROUP"]}, []

        with patch.object(canonical, "_project_student_block", side_effect=projector) as projection:
            mismatches = _projection_mismatches(self.database, {"shared-source-block": block}, audience,
                date.fromisoformat("2026-10-19"))
        self.assertGreaterEqual(projection.call_count, 2)
        self.assertEqual(mismatches, {"shared-source-block"})

    def test_optional_activities_are_available_without_personal_membership(self):
        elective = self.lesson(id="shared-elective", subject="Проектная лаборатория", activityType="elective",
            activityNameSnapshot="Проектная лаборатория", classId=str(self.group["id"]), classIds=[str(self.group["id"])],
            blockId="shared-elective-block", blockName="Цифровой трек",
            audience={"kind": "students", "student_ids": []}, eligibleGrades=[9], confirmedParticipantIds=[str(self.student["id"])],
            teacherId="", teacherIds=[], room="")
        second_elective = self.lesson(id="shared-elective-2", subject="Медиалаборатория", activityType="elective",
            activityNameSnapshot="Медиалаборатория", classId=str(self.group["id"]), classIds=[str(self.group["id"])],
            blockId="shared-elective-block", blockName="Цифровой трек",
            audience={"kind": "students", "student_ids": []}, eligibleGrades=[9], confirmedParticipantIds=[],
            teacherId="", teacherIds=[], room="")
        club = self.lesson(id="shared-club", day=1, subject="Творческая мастерская", activityType="club",
            activityNameSnapshot="Творческая мастерская", classId="", classIds=[], displayClassIds=[],
            audience={"kind": "students", "student_ids": []}, eligibleGrades=[9], teacherId="", teacherIds=[], room="")
        draft = self.draft([elective, second_elective, club])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertTrue(preview["can_publish"], preview["blockers"])
        codes = [item["code"] for item in preview["diagnostics"]]
        self.assertNotIn("audience_unresolved", codes)
        self.assertIn("missing_teacher", codes)
        self.assertTrue(all(item["severity"] == "info" for item in preview["diagnostics"] if item["code"] == "missing_teacher"))
        self.assertNotIn("missing_room", codes)
        delivery = preview["published_source"]["elective_delivery_records"]
        self.assertEqual(len(delivery), 1)
        self.assertEqual(delivery[0]["display_label"], "Цифровой трек")
        self.assertEqual(set(delivery[0]["source_lesson_ids"]), {"shared-elective", "shared-elective-2"})
        self.assertEqual(set(delivery[0]["known_student_ids"]),
                         {str(self.student["id"]), str(self.other_student["id"])})
        source_records = {row["lesson_id"]: row for row in preview["published_source"]["records"]}
        self.assertEqual(source_records["shared-elective"]["known_student_ids"], [])
        self.assertEqual(source_records["shared-elective-2"]["known_student_ids"], [])
        generic_lanes = [{**copy.deepcopy(item), "id": f"generic-{index}", "blockId": "generic-block",
                          "blockName": "", "confirmedParticipantIds": []}
                         for index, item in enumerate((elective, second_elective), 1)]
        generic_draft = self.draft(generic_lanes)
        generic_preview = preview_publication(self.database, generic_draft["id"], scope_kind="template",
                                              expected_revision=1)
        self.assertEqual(generic_preview["published_source"]["elective_delivery_records"][0]["display_label"],
                         "Курс по выбору")
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        projection = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        elective_items = [item for item in projection["items"] if item.get("activity_type") == "elective"]
        self.assertEqual([item.get("activity") for item in elective_items], ["Цифровой трек"])
        self.assertNotIn("teacher_ids", elective_items[0])
        self.assertNotIn("room", elective_items[0])
        self.assertEqual(projection["optional_activities"], [])
        campus = canonical.student_campus_projection(self.database, self.student["id"], "2026-10-19", "2026-10-25")
        self.assertNotIn("Творческая мастерская", [item["activity"] for item in campus["items"]])
        public = canonical.published_week_view(self.database, "2026-10-19")
        self.assertIn("Творческая мастерская", [assignment["activity"] for block in public["items"] for assignment in block["assignments"]])
        self.assertIn(str(self.student["id"]), preview["published_source"]["payload"]["lessons"][0]["confirmedParticipantIds"])

    def test_event_has_projection_priority_without_mutating_overlapping_source_lesson(self):
        event = self.lesson(id="museum", subject="Экскурсия в музей", kind="event", activityType="event",
            day=0, period=0, start="09:15", end="10:15", teacherId="", teacherIds=[], room="", place="Музей",
            audience={"kind": "classes", "class_ids": [str(self.group["id"])]}, classId=str(self.group["id"]),
            classIds=[str(self.group["id"])])
        draft = self.draft([self.lesson(), event])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertTrue(preview["can_publish"], preview["blockers"])
        self.assertEqual(preview["summary"]["ready_lessons"], 2)
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        projected = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        math = next(item for item in projected["items"] if item.get("activity") == "Math")
        museum = next(item for item in projected["items"] if item.get("activity") == "Экскурсия в музей")
        self.assertEqual((math["start_time"], math["end_time"]), ("09:00", "09:15"))
        self.assertEqual((museum["start_time"], museum["end_time"]), ("09:15", "10:15"))
        self.assertEqual(len(preview["published_source"]["records"]), 2)

    def test_event_can_be_outside_the_regular_bell_grid(self):
        event = self.lesson(id="evening-meeting", subject="Вечерняя встреча", kind="event", activityType="event",
            day=0, period=0, start="18:00", end="19:30", teacherId="", teacherIds=[], room="",
            audience={"kind": "classes", "class_ids": [str(self.group["id"])]}, classId=str(self.group["id"]),
            classIds=[str(self.group["id"])])
        draft = self.draft([event])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertTrue(preview["can_publish"], preview["blockers"])
        self.assertNotIn("missing_teacher", [item["code"] for item in preview["diagnostics"]])
        self.assertNotIn("missing_room", [item["code"] for item in preview["diagnostics"]])

    def test_all_day_event_is_projected_for_its_confirmed_class(self):
        event = self.lesson(id="all-day-event", subject="Школьный праздник", kind="event", activityType="event",
            allDay=True, start="00:00", end="23:59", teacherId="", teacherIds=[], room="",
            audience={"kind": "classes", "class_ids": [str(self.group["id"])]},
            classId=str(self.group["id"]), classIds=[str(self.group["id"])])
        draft = self.draft([event])
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(preview["summary"]["ready_lessons"], 1)
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        projected = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        self.assertEqual([(item["activity"], item["start_time"], item["end_time"])
                          for item in projected["items"]], [("Школьный праздник", "00:00", "23:59")])

    def test_compatible_duplicate_without_teacher_uses_its_confirmed_source_copy(self):
        draft=self.draft([self.lesson(id="a-unknown",teacherId=""),self.lesson(id="z-known")])
        self.publish(draft)
        self.publish(draft,"week",week_start="2026-10-19")
        result=canonical.project_student(self.database,self.student["id"],"2026-10-19")
        self.assertEqual(len(result["items"]),1)
        self.assertEqual(result["items"][0]["teacher_ids"],[str(self.teacher["id"])])

    def test_parallel_groups_and_remaining_memberships_runtime(self):
        grouped = self.lesson(audience={"kind": "groups", "group_ids": [str(self.math["id"])]})
        remainder = self.lesson(id="remaining", subject="Other", teacherId=str(self.other_teacher["id"]), room="902",
            audience={"kind": "available_slot", "grade": "9", "class_ids": [str(self.group["id"])]})
        draft = self.draft([grouped, remainder])
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        first = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        second = canonical.project_student(self.database, self.other_student["id"], "2026-10-19")
        self.assertEqual(first["items"][0]["activity"], "Math")
        self.assertEqual(second["items"][0]["activity"], "Other")

    def test_reviewed_multi_grade_available_slot_freezes_exact_remaining_students(self):
        from backend.services.schedule_experiments import save_experiment

        class10 = self.database.create_group("10-А", "class", base_class_name="10")
        class11 = self.database.create_group("11-А", "class", base_class_name="11")
        society = self.database.create_group("Обществознание 11", "subject_group",
            subject="Обществознание", base_class_name="11")

        def student(name, class_name, class_group):
            identity = self.database.create_identity("student", name, class_name)
            self.database.create_membership(class_group["id"], identity["id"], "student", "synthetic")
            return identity

        ten_first = student("Synthetic 10 first", "10-А", class10)
        ten_second = student("Synthetic 10 second", "10-А", class10)
        eleven_society = student("Synthetic 11 society", "11-А", class11)
        eleven_remaining = student("Synthetic 11 remaining", "11-А", class11)
        eleven_excluded = student("Synthetic 11 excluded", "11-А", class11)
        self.database.create_membership(society["id"], eleven_society["id"], "student", "synthetic")

        society_lesson = self.lesson(id="society", subject="Обществознание", room="1101",
            classId=str(class11["id"]), classIds=[str(class11["id"])],
            audience={"kind": "groups", "group_ids": [str(society["id"])]})
        geography = self.lesson(id="geography", subject="География", teacherId=str(self.other_teacher["id"]), room="1001",
            classId=str(class10["id"]), classIds=[str(class10["id"]), str(class11["id"])],
            audience={"kind": "available_slot", "grade": "10,11",
                      "class_ids": [str(class10["id"]), str(class11["id"])],
                      "exclude_student_ids": [str(eleven_excluded["id"])]},
            audience_snapshot={"student_ids": [str(ten_first["id"]), str(ten_second["id"]),
                                                str(eleven_remaining["id"])]})
        draft = self.draft([society_lesson, geography])
        payload = copy.deepcopy(draft["payload"])
        payload["classes"] = [str(class10["id"]), str(class11["id"])]
        draft = save_experiment(self.database, draft["id"], expected_revision=draft["revision"], payload=payload)

        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        self.assertEqual(preview["summary"]["ready_lessons"], 2, preview["diagnostics"])
        self.assertNotIn("unsupported_partial_cohort", {item["code"] for item in preview["diagnostics"]})
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        later_ten = student("Synthetic 10 after publication", "10-А", class10)

        def activities(identity):
            return [item.get("activity") for item in canonical.project_student(
                self.database, identity["id"], "2026-10-19")["items"] if item.get("state") == "ACTIVITY"]

        self.assertEqual(activities(ten_first), ["География"])
        self.assertEqual(activities(ten_second), ["География"])
        self.assertEqual(activities(eleven_society), ["Обществознание"])
        self.assertEqual(activities(eleven_remaining), ["География"])
        self.assertEqual(activities(eleven_excluded), [])
        self.assertEqual(activities(later_ten), [])

    def test_multi_grade_available_slot_rejects_partial_unknown_and_missing_snapshot_scopes(self):
        class10 = self.database.create_group("10-А", "class", base_class_name="10")
        class11a = self.database.create_group("11-А", "class", base_class_name="11")
        class11b = self.database.create_group("11-Б", "class", base_class_name="11")
        for lesson_id, class_ids in (
            ("partial", [str(class10["id"]), str(class11a["id"])]),
            ("unknown", [str(class10["id"]), "unknown-class-id"]),
            ("missing-snapshot", [str(class10["id"]), str(class11a["id"]), str(class11b["id"])]),
        ):
            lesson = self.lesson(id=lesson_id, subject="География",
                classId=class_ids[0], classIds=class_ids,
                audience={"kind": "available_slot", "grade": "10,11", "class_ids": class_ids})
            draft = self.draft([lesson])
            preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
            self.assertEqual(preview["summary"]["ready_lessons"], 0)
            self.assertIn("unsupported_partial_cohort", {item["code"] for item in preview["diagnostics"]})

    def test_information_only_does_not_defer_and_is_audited(self):
        draft = self.draft([self.lesson(room="")])
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertTrue(p["can_publish"])
        self.assertEqual(p["summary"]["info_count"], 1)
        self.assertEqual(p["summary"]["deferred_lessons"], 0)
        self.publish(draft)
        self.assertEqual(list_publications(self.database)["items"][0]["diagnostics"][0]["code"], "missing_room")

    def test_legacy_history_keeps_preserved_base_meaning_without_inventing_counts(self):
        result = self.publish(self.draft())
        with self.database.connection() as connection:
            row = self.database.execute(connection, "SELECT metadata FROM schedule_constructor_publications WHERE id=?", (result["publication_id"],)).fetchone()
            metadata = json.loads(row["metadata"])
            metadata["preview"].pop("summary")
            metadata["preview"]["deferred"] = [{"lesson_id":"old-deferred", "reason":"Unresolved change retained in draft; base block preserved"}]
            self.database.execute(connection, "UPDATE schedule_constructor_publications SET metadata=? WHERE id=?", (json.dumps(metadata), result["publication_id"]))
        history = list_publications(self.database)["items"][0]
        self.assertEqual(history["deferred"][0]["action"], "preserved_base")
        self.assertIsNone(history["summary"])

    def test_first_template_partial_keeps_draft_and_only_valid_runtime(self):
        draft = self.draft([self.lesson(), self.lesson(id="bad", day=1, teacherId="missing")])
        original = copy.deepcopy(draft["payload"])
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(p["summary"]["ready_lessons"], 1)
        self.assertEqual(p["summary"]["deferred_lessons"], 1)
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-26")
        self.assertEqual(get_experiment(self.database, draft["id"])["payload"], original)
        for identity in (self.student, self.other_student):
            rows = canonical.project_student(self.database, identity["id"], "2026-10-26")["items"]
            self.assertEqual([r["activity"] for r in rows if r["state"] == "ACTIVITY"], ["Math"])
            self.assertTrue(all(r["state"] == "UNRESOLVED" or r["activity"] == "Math" for r in rows))
        self.assertEqual(canonical.project_teacher(self.database, self.teacher["id"], "2026-10-26")["items"][0]["activity"], "Math")

    def test_stale_saved_roster_is_withheld_from_student_schedule(self):
        from backend.services.schedule_experiments import save_experiment
        scoped = self.lesson(id="scoped", subject="Scoped", audience={"kind": "groups", "group_ids": [str(self.math["id"])]})
        draft = self.draft([scoped, self.lesson(id="other", subject="Other", period=1)])
        draft = save_experiment(self.database, draft["id"], expected_revision=draft["revision"], payload=draft["payload"])
        self.database.create_membership(self.math["id"], self.other_student["id"], "student", "synthetic")
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        from backend.services.constructor_resolution import bulk_audience_preview
        rosters = bulk_audience_preview(self.database, draft["id"], expected_revision=draft["revision"], scope="template")
        row = next(row for row in rosters["lessons"] if row["id"] == "scoped")
        self.assertEqual((row["old_count"], row["proposed_count"]), (1, 2))
        self.assertEqual([row["lesson_id"] for row in preview["deferred"]], ["scoped"])
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-26")
        for identity in (self.student, self.other_student):
            rows = canonical.project_student(self.database, identity["id"], "2026-10-26")["items"]
            self.assertEqual([row["activity"] for row in rows if row["state"] == "ACTIVITY"], ["Other"])

    def test_bad_parallel_member_does_not_expand_remaining_audience(self):
        draft = self.draft([
            self.lesson(teacherId="unknown", audience={"kind":"groups", "group_ids":[str(self.math["id"])]}),
            self.lesson(id="remaining", subject="Other", teacherId=str(self.other_teacher["id"]),
                audience={"kind":"available_slot", "grade":"9", "class_ids":[str(self.group["id"])]}),
            self.lesson(id="good", day=1)])
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(p["summary"]["ready_lessons"], 1)
        self.assertEqual(p["summary"]["deferred_lessons"], 2)
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-26")
        for identity in (self.student, self.other_student):
            self.assertFalse(any(r.get("activity") == "Other" for r in canonical.project_student(self.database, identity["id"], "2026-10-26")["items"]))

    def test_overlapping_different_slots_defer_both_and_same_subject_is_not_exempt(self):
        draft = self.draft([self.lesson(), self.lesson(id="collision", start="09:15", end="09:50", period=1),
                            self.lesson(id="good", day=1)])
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(p["summary"]["ready_lessons"], 1)
        self.assertEqual({d["lesson_id"] for d in p["deferred"]}, {"l1", "collision"})

    def test_structural_errors_still_block_and_fingerprint_is_required(self):
        for lessons in ([self.lesson(), self.lesson()], [self.lesson(day=9)]):
            draft = self.draft(lessons)
            p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
            self.assertFalse(p["can_publish"])
            with self.assertRaises(PublicationBlocked):
                publish_publication(self.database, draft["id"], scope_kind="template", expected_revision=1,
                                    preview_token=p["preview_token"], confirmed=True)
        draft = self.draft()
        with self.assertRaises(PublicationConflict):
            publish_publication(self.database, draft["id"], scope_kind="template", expected_revision=1,
                                preview_token="wrong", confirmed=True)

    def test_week_without_base_remains_critical(self):
        draft = self.draft()
        p = preview_publication(self.database, draft["id"], scope_kind="week", expected_revision=1, week_start="2026-10-26")
        self.assertFalse(p["can_publish"])
        self.assertIn("missing_base", [d["code"] for d in p["blockers"]])

    def test_partial_republish_and_restore_do_not_modify_historical_week(self):
        draft = self.draft([self.lesson(), self.lesson(id="bad", day=1, teacherId="unknown")])
        first = self.publish(draft)
        self.publish(draft, "week", week_start="2026-09-21")
        historical = canonical.project_student(self.database, self.student["id"], "2026-09-21")
        payload = copy.deepcopy(draft["payload"])
        payload["lessons"][1]["teacherId"] = str(self.teacher["id"])
        draft = save_experiment(self.database, draft["id"], expected_revision=draft["revision"], payload=payload)
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-26")
        self.assertEqual(len(canonical.project_student(self.database, self.student["id"], "2026-10-26")["items"]), 2)
        self.assertEqual(canonical.project_student(self.database, self.student["id"], "2026-09-21"), historical)
        p = preview_restore(self.database, first["publication_id"], expected_head_revision=2)
        self.assertTrue(p["can_restore"])
        restore_publication(self.database, first["publication_id"], expected_head_revision=2, preview_token=p["preview_token"], confirmed=True)
        self.assertEqual(canonical.project_student(self.database, self.student["id"], "2026-09-21"), historical)

    def test_split_store_preview_is_read_only_and_confirm_cannot_reuse_token(self):
        draft = self.draft()
        # Independent adapter object models the approved source/local draft split.
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1, draft_database=copy.copy(self.database))
        self.assertTrue(p["read_only"])
        self.assertFalse(p["publication_available"])
        with self.assertRaises(PublicationConflict):
            publish_publication(self.database, draft["id"], scope_kind="template", expected_revision=1, preview_token=p["preview_token"], confirmed=True)

    def test_split_store_route_allows_only_preview_and_requires_school_admin(self):
        from backend.handlers.constructor_publication_api import create_constructor_publication_router
        from fastapi import HTTPException
        store = Database(Path(self.temp.name) / "draft-only.db")
        store.initialize()
        draft = create_experiment(store, name="Local read-only preview", source="import", payload=self.draft()["payload"])
        def require_admin(token):
            if token != "synthetic-admin":
                raise HTTPException(401, "School admin session required")
            return 1
        app = FastAPI()
        app.include_router(create_constructor_publication_router(lambda: (self.database, store), require_admin))
        client = TestClient(app)
        path = f"/api/admin/schedule/constructor/{draft['id']}/publication/"
        body = {"scope_kind":"template", "expected_revision":1}
        headers = {"X-School-Admin-Session":"synthetic-admin"}
        with patch.dict(os.environ, {"MY_ISLAND_CONSOLIDATED_SCHEDULE":"1"}):
            self.assertEqual(client.post(path+"preview", json=body).status_code, 401)
            response = client.post(path+"preview", json=body, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            preview = response.json()
            self.assertTrue(preview["read_only"])
            self.assertFalse(preview["publication_available"])
            self.assertEqual(client.post(path+"publish", json={**body,"preview_token":preview["preview_token"],"confirmed":True}, headers=headers).status_code, 409)
        self.assertIsNone(canonical.canonical_version(self.database))
        self.assertEqual(get_experiment(store, draft["id"])["revision"], 1)

    def test_resolution_routes_require_admin_and_use_server_actor(self):
        from backend.handlers.constructor_publication_api import create_constructor_publication_router
        from fastapi import HTTPException
        draft=self.draft()
        def require_admin(token):
            if token!="synthetic-admin":raise HTTPException(401,"School admin session required")
            return {"id":"verified-admin"}
        app=FastAPI();app.include_router(create_constructor_publication_router(lambda:(self.database,self.database),require_admin))
        client=TestClient(app);base=f"/api/admin/schedule/constructor/{draft['id']}"
        preview_body={"expected_revision":1,"lesson_ids":["l1"]}
        apply_url=base+"/resolution/"
        with patch.dict(os.environ,{"MY_ISLAND_CONSOLIDATED_SCHEDULE":"1"}):
            self.assertEqual(client.post(apply_url+"preview",json=preview_body).status_code,401)
            headers={"X-School-Admin-Session":"synthetic-admin"}
            preview=client.post(apply_url+"preview",json=preview_body,headers=headers)
            self.assertEqual(preview.status_code,200,preview.text)
            result=client.post(apply_url+"apply",headers=headers,json={**preview_body,"preview_token":preview.json()["preview_token"],"action":"independent_practice","reason":"Verified","actor_user_id":"forged-admin"})
            self.assertEqual(result.status_code,200,result.text)
            receipt=result.json()["payload"]["lessons"][0]["resolution_history"][0]
            self.assertEqual(receipt["actor_id"],"verified-admin")
            self.assertNotEqual(receipt["actor_id"],"forged-admin")
            self.assertEqual(client.post(apply_url+"apply",headers=headers,json={**preview_body,"preview_token":preview.json()["preview_token"],"action":"defer","reason":"Replay"}).status_code,409)
            draft=get_experiment(self.database,draft["id"])
            published=self.publish(draft)
            source_url=f"/api/admin/schedule/constructor/publication/{published['publication_id']}/source"
            self.assertEqual(client.get(source_url).status_code,401)
            self.assertEqual(client.get(source_url,headers=headers).status_code,200)

    def test_bulk_audience_routes_preview_stale_guard_and_apply_with_verified_actor(self):
        from backend.handlers.constructor_publication_api import create_constructor_publication_router
        from backend.services.schedule_experiments import save_experiment
        from fastapi import HTTPException
        draft = self.draft()
        draft = save_experiment(self.database, draft['id'], expected_revision=draft['revision'], payload=draft['payload'])
        def require_admin(token):
            if token != 'synthetic-admin':
                raise HTTPException(401, 'School admin session required')
            return {'id': 'verified-admin'}
        app = FastAPI()
        app.include_router(create_constructor_publication_router(lambda: (self.database, self.database), require_admin))
        client = TestClient(app)
        base = f"/api/admin/schedule/constructor/{draft['id']}/audiences/"
        body = {'expected_revision': draft['revision'], 'scope': 'template'}
        headers = {'X-School-Admin-Session': 'synthetic-admin'}
        with patch.dict(os.environ, {'MY_ISLAND_CONSOLIDATED_SCHEDULE': '1'}):
            self.assertEqual(client.post(base+'preview', json=body).status_code, 401)
            preview = client.post(base+'preview', json=body, headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()['summary']['safe_count'], 0)
            self.assertIn('preview_token', preview.json())

            added = self.database.create_identity('student', 'Route synthetic pupil', '9A')
            self.database.create_membership(self.group['id'], added['id'], 'student', 'synthetic')
            stale = client.post(base+'apply', headers=headers, json={**body, 'preview_token': preview.json()['preview_token'], 'reason': 'Confirm checked audience'})
            self.assertEqual(stale.status_code, 409, stale.text)

            refreshed = client.post(base+'preview', json=body, headers=headers)
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
            applied = client.post(base+'apply', headers=headers, json={**body, 'preview_token': refreshed.json()['preview_token'], 'reason': 'Confirm checked audience'})
            self.assertEqual(applied.status_code, 200, applied.text)
            saved = applied.json()
            lesson = saved['payload']['lessons'][0]
            self.assertIn(str(added['id']), lesson['audience_snapshot']['student_ids'])
            self.assertEqual(lesson['resolution_history'][-1]['actor_id'], 'verified-admin')
            self.assertEqual(saved['revision'], draft['revision'] + 1)

    def test_group_semantics_change_invalidates_preview(self):
        draft = self.draft()
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        with self.database.connection() as connection:
            connection.execute("UPDATE groups SET semantic_dimension='changed' WHERE id=?", (self.group["id"],))
        with self.assertRaises(PublicationConflict):
            publish_publication(self.database, draft["id"], scope_kind="template", expected_revision=1, preview_token=p["preview_token"], confirmed=True)

    def test_projector_mismatch_defers_safe_unit_before_writing(self):
        draft = self.draft([self.lesson(), self.lesson(id="other", day=1)])
        original = canonical._project_student_block
        def corrupt(database, context, student, block, day):
            if block["weekday"] == 0:
                return {"state":"ACTIVITY", "activity":"Unconfirmed", "teacher_ids":[]}, []
            return original(database, context, student, block, day)
        with patch.object(canonical, "_project_student_block", side_effect=corrupt):
            p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertTrue(p["can_publish"])
        self.assertEqual(p["summary"]["ready_lessons"], 1)
        self.assertEqual(p["deferred"][0]["lesson_id"], "l1")
        self.assertIn("projection_mismatch", [d["code"] for d in p["diagnostics"]])

    def test_source_import_refresh_cannot_replace_manual_heads(self):
        draft = self.draft()
        template = self.publish(draft)
        week = self.publish(draft, "week", week_start="2026-10-19")
        canonical.import_canonical_artifact(self.database, {"version_id": "source-newer", "blocks": {},
            "source_snapshot": {"id": "new", "fingerprint": "new"}}, status="authoritative", approved_at="2099-01-01")
        canonical.materialize_effective_week(self.database, "source-newer", "2026-10-19", overlay_observed_at="2099-01-01")
        self.assertEqual(canonical.canonical_version(self.database)["version_id"], template["published_id"])
        self.assertEqual(canonical.effective_blocks(self.database, "2026-10-19")["effective_week"]["effective_week_id"], week["published_id"])
        with patch("backend.services.canonical_weekly_refresh._prepare_current_week", side_effect=AssertionError("must not read source")):
            result = refresh_current_week(self.database, None, "2026-10-19")
        self.assertEqual(result["status"], "manual_publication_protected")

    def test_week_can_remove_base_block_without_readback_failure(self):
        draft = self.draft([self.lesson(), self.lesson(id="second", day=1, period=1)])
        self.publish(draft)
        weekly = self.draft([self.lesson()])
        result = self.publish(weekly, "week", week_start="2026-10-19")
        self.assertEqual(result["runtime_readback"]["block_count"], 1)
        teacher = canonical.project_teacher(self.database, self.teacher["id"], "2026-10-19")
        self.assertEqual(len(teacher["items"]), 1)

    def test_restore_is_new_immutable_artifact_and_cas_guarded(self):
        draft = self.draft()
        first = self.publish(draft)
        changed = self.draft([self.lesson(subject="Changed")])
        second = self.publish(changed)
        with self.assertRaises(PublicationConflict):
            preview_restore(self.database, first["publication_id"], expected_head_revision=1)
        preview = preview_restore(self.database, first["publication_id"], expected_head_revision=2)
        command = dict(expected_head_revision=2, preview_token=preview["preview_token"],
                       confirmed=True, operation_id="restore-once")
        restored = restore_publication(self.database, first["publication_id"], **command)
        self.assertEqual(restore_publication(self.database, first["publication_id"], **command), restored)
        self.assertNotEqual(restored["published_id"], first["published_id"])
        self.assertEqual(next(iter(canonical.read_canonical_template(self.database)["blocks"].values()))["assignments"][0]["activity"], "Math")
        self.assertIsNotNone(canonical.read_canonical_template(self.database, second["published_id"]))
        self.assertEqual(list_publications(self.database)["items"][0]["restored_from"], first["publication_id"])

    def test_runtime_failure_rolls_back_artifact_head_and_audit(self):
        draft = self.draft()
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        original = canonical.read_canonical_template
        def corrupt(db, version_id=None):
            return None if version_id is None else original(db, version_id)
        with patch.object(canonical, "read_canonical_template", side_effect=corrupt):
            with self.assertRaises(RuntimeError):
                publish_publication(self.database, draft["id"], scope_kind="template", expected_revision=1,
                    preview_token=p["preview_token"], confirmed=True)
        self.assertIsNone(canonical.canonical_version(self.database))
        self.assertEqual(list_publications(self.database)["items"], [])

    def test_pre_migration_canonical_read_remains_compatible(self):
        with self.database.connection() as connection:
            connection.execute("DROP TABLE schedule_publication_heads")
        self.assertIsNone(canonical.canonical_version(self.database))

    def test_deferred_week_excludes_block_without_implicit_base_fallback(self):
        base = self.draft()
        self.publish(base)
        draft = create_experiment(self.database, name="Weekly", source="canonical")
        payload = copy.deepcopy(draft["payload"])
        payload["lessons"][0].update(subject="Unresolved replacement", review_required=True, deferred=True)
        draft = save_experiment(self.database, draft["id"], expected_revision=1, payload=payload)
        p = preview_publication(self.database, draft["id"], scope_kind="week", expected_revision=draft["revision"], week_start="2026-10-19")
        self.assertTrue(p["can_publish"], p["blockers"])
        self.assertEqual(len(p["deferred"]), 1)
        self.publish(draft, "week", week_start="2026-10-19")
        unresolved = canonical.project_teacher(self.database, self.teacher["id"], "2026-10-19")["items"]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["state"], "UNRESOLVED")
        self.assertIsNone(unresolved[0]["activity"])
        self.assertEqual(p["deferred"][0]["action"], "retained_unresolved")
        self.assertTrue(get_experiment(self.database, draft["id"])["payload"]["lessons"][0]["deferred"])

    def test_protected_unchanged_allowed_but_edit_or_missing_base_block_blocked(self):
        base = self.draft()
        self.publish(base)
        draft = create_experiment(self.database, name="Protected", source="canonical")
        payload = copy.deepcopy(draft["payload"])
        payload["lessons"][0]["protected"] = True
        draft = save_experiment(self.database, draft["id"], expected_revision=1, payload=payload)
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        self.assertTrue(p["can_publish"], p["blockers"])
        payload["lessons"][0]["subject"] = "Not allowed"
        draft = save_experiment(self.database, draft["id"], expected_revision=draft["revision"], payload=payload)
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=draft["revision"])
        self.assertIn("protected", [b["code"] for b in p["blockers"]])

    def test_conflicting_students_and_teacher_defer_both_sides(self):
        draft = self.draft([self.lesson(), self.lesson(id="conflict", subject="Different")])
        p = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertIn("student_overlap", [b["code"] for b in p["diagnostics"]])
        self.assertIn("teacher_overlap", [b["code"] for b in p["diagnostics"]])
        self.assertEqual(len(p["deferred"]), 2)
        self.assertTrue(p["can_publish"])

    def test_lunch_and_no_lesson_keep_scope_and_details(self):
        draft = self.draft([self.lesson(specialMark="lunch", subject="Обед", description="Long lunch"),
                            self.lesson(id="none", day=1, specialMark="no_lesson", subject="")])
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        projection = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        self.assertEqual(projection["items"][0]["activity"], "Обед")
        self.assertEqual(projection["items"][1]["state"], "NO_LESSON")
        self.assertEqual(canonical.project_teacher(self.database, self.teacher["id"], "2026-10-19")["items"], [])
        template = canonical.read_canonical_template(self.database)
        details = next(iter(template["blocks"].values()))["assignments"][0]["metadata"]["constructor_details"]
        self.assertEqual(details["description"], "Long lunch")

    def test_simultaneous_confirmations_publish_once(self):
        draft = self.draft()
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        def confirm():
            try:
                return publish_publication(self.database, draft["id"], scope_kind="template", expected_revision=1,
                    preview_token=preview["preview_token"], confirmed=True)["status"]
            except PublicationConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(lambda _: confirm(), range(2))), ["published", "conflict"])
        self.assertEqual(len(list_publications(self.database)["items"]), 1)

    def test_repeated_template_publication_advances_only_base_metadata(self):
        draft = self.draft()
        original_payload = copy.deepcopy(draft["payload"])
        first = self.publish(draft)
        stored = get_experiment(self.database, draft["id"])
        self.assertEqual(stored["payload"], original_payload)
        self.assertEqual(stored["base_version_id"], first["published_id"])
        self.assertEqual(stored["revision"], 2)
        second = self.publish(stored)
        self.assertNotEqual(first["published_id"], second["published_id"])
        self.assertEqual(second["draft_revision"], 3)

    def test_missing_protected_base_and_restore_missing_membership_block(self):
        draft = self.draft()
        first = self.publish(draft)
        with self.database.connection() as connection:
            connection.execute("UPDATE canonical_schedule_blocks SET evidence=? WHERE version_id=?",
                               ('{"protected":true}', first["published_id"]))
        empty = self.draft([])
        preview = preview_publication(self.database, empty["id"], scope_kind="template", expected_revision=1)
        self.assertIn("protected_base", [b["code"] for b in preview["blockers"]])
        with self.database.connection() as connection:
            connection.execute("UPDATE memberships SET active=0")
        restore = preview_restore(self.database, first["publication_id"], expected_head_revision=1)
        self.assertFalse(restore["can_restore"])
        self.assertIn("unknown_membership", [b["code"] for b in restore["blockers"]])

    def test_school_publication_updates_main_selection_only_for_school_template(self):
        ordinary = self.draft()
        self.publish(ordinary)
        with self.assertRaises(ValueError):
            open_active_school_template(self.database)
        draft = self.draft()
        payload = {**draft["payload"], "draft_kind": "school"}
        draft = save_experiment(self.database, draft["id"], expected_revision=1, payload=payload)
        self.publish(draft)
        main = open_active_school_template(self.database)
        self.assertEqual(main["id"], draft["id"])
        self.assertEqual(main["revision"], draft["revision"])
        with self.database.connection() as connection:
            selection = dict(connection.execute("SELECT * FROM school_template_selection").fetchone())
        self.assertEqual(selection["source_revision"], draft["revision"])
        self.assertEqual(selection["source_draft_id"], draft["id"])
        self.publish(draft, "week", week_start="2026-10-19")
        self.assertEqual(open_active_school_template(self.database)["id"], draft["id"])

    def test_canonical_copy_preserves_activity_details_and_republishes(self):
        lessons = [
            self.lesson(id="event", subject="Meeting", kind="event", activityType="event", informational=True,
                        description="Meeting details", place="Hall", format="offline"),
            self.lesson(id="lunch", subject="Обед", period=1, specialMark="lunch", teacherId=""),
            self.lesson(id="none", subject="", day=1, period=1, specialMark="no_lesson", teacherId=""),
            self.lesson(id="club", subject="Creative club", day=1, activityType="club",
                        activityNameSnapshot="Creative club", classId="", classIds=[], audience={"kind": "students", "student_ids": []},
                        eligibleGrades=[9], description="Club details"),
            self.lesson(id="elective", subject="Digital course", day=2, activityType="elective",
                        activityNameSnapshot="Digital course", classId=str(self.group["id"]), classIds=[str(self.group["id"])],
                        blockId="digital-block", blockName="Цифровой трек", eligibleGrades=[9],
                        electiveDirection="digital", confirmedParticipantIds=[str(self.student["id"])])]
        draft = self.draft(lessons)
        draft = save_experiment(self.database, draft["id"], expected_revision=1, payload=draft["payload"])
        self.publish(draft)
        copied = create_experiment(self.database, name="Fresh canonical", source="canonical")
        records = {item["subject"]: item for item in copied["payload"]["lessons"]}
        self.assertEqual(records["Meeting"]["description"], "Meeting details")
        self.assertEqual(records["Meeting"]["place"], "Hall")
        self.assertEqual(records["Meeting"]["format"], "offline")
        self.assertTrue(records["Meeting"]["informational"])
        self.assertEqual(records["Creative club"]["activityType"], "club")
        self.assertEqual(records["Creative club"]["eligibleGrades"], [9])
        self.assertEqual(records["Digital course"]["confirmedParticipantIds"], [str(self.student["id"])])
        self.assertEqual(records["Digital course"]["electiveDirection"], "digital")
        self.assertEqual(records["Digital course"]["blockId"], "digital-block")
        self.assertEqual(records["Digital course"]["blockName"], "Цифровой трек")
        self.assertEqual(records["Обед"]["specialMark"], "lunch")
        self.assertEqual(records["NO_LESSON"]["specialMark"], "no_lesson")
        self.assertTrue(all(not l["needsClarification"] for l in copied["payload"]["lessons"]))
        self.publish(copied)


if __name__ == "__main__":
    unittest.main()
