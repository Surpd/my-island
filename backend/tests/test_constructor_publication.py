import copy
import tempfile
import unittest
import os
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from backend.database import Database
from backend.services import canonical_schedule as canonical
from backend.services.constructor_publication import (
    initialize_publication_schema, preview_publication, publish_publication,
    PublicationConflict, PublicationBlocked, list_publications, restore_publication, preview_restore,
)
from backend.services.schedule_experiments import create_experiment, get_experiment, save_experiment
from backend.services.canonical_weekly_refresh import refresh_current_week
from backend.services.school_template_selection import open_active_school_template
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.handlers.api import create_router


class ConstructorPublicationTests(unittest.TestCase):
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

    def test_preview_has_no_writes_and_roundtrip_template_week_runtime(self):
        draft = self.draft()
        with self.database.connection() as conn:
            before = conn.total_changes
        preview = preview_publication(self.database, draft["id"], scope_kind="template", expected_revision=1)
        self.assertEqual(list_publications(self.database)["items"], [])
        self.assertEqual(get_experiment(self.database, draft["id"])["revision"], 1)
        published = self.publish(draft)
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
            self.assertEqual(p["deferred"][0]["action"], "excluded")
            self.assertTrue(p["diagnostics"])

    def test_parallel_groups_and_remaining_memberships_runtime(self):
        grouped = self.lesson(audience={"kind": "groups", "group_ids": [str(self.math["id"])]})
        remainder = self.lesson(id="remaining", subject="Other", teacherId=str(self.other_teacher["id"]),
            audience={"kind": "available_slot", "grade": "9", "class_ids": [str(self.group["id"])]})
        draft = self.draft([grouped, remainder])
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        first = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        second = canonical.project_student(self.database, self.other_student["id"], "2026-10-19")
        self.assertEqual(first["items"][0]["activity"], "Math")
        self.assertEqual(second["items"][0]["activity"], "Other")

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
            self.assertEqual([r["activity"] for r in rows], ["Math"])
        self.assertEqual(canonical.project_teacher(self.database, self.teacher["id"], "2026-10-26")["items"][0]["activity"], "Math")

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
        restored = restore_publication(self.database, first["publication_id"], expected_head_revision=2,
                                       preview_token=preview["preview_token"], confirmed=True)
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
        self.assertEqual(canonical.project_teacher(self.database, self.teacher["id"], "2026-10-19")["items"], [])
        self.assertEqual(p["deferred"][0]["action"], "excluded")
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
        draft = self.draft([self.lesson(specialMark="lunch", subject="Обед", teacherId="", description="Long lunch"),
                            self.lesson(id="none", day=1, specialMark="no_lesson", subject="", teacherId="")])
        self.publish(draft)
        self.publish(draft, "week", week_start="2026-10-19")
        projection = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        self.assertEqual(projection["items"][0]["activity"], "Обед")
        self.assertEqual(projection["items"][1]["state"], "NO_LESSON")
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
                        activityNameSnapshot="Digital course", classId="", classIds=[],
                        eligibleGrades=[9], electiveDirection="digital", confirmedParticipantIds=[str(self.student["id"])])]
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
        self.assertEqual(records["Обед"]["specialMark"], "lunch")
        self.assertEqual(records["NO_LESSON"]["specialMark"], "no_lesson")
        self.assertTrue(all(not l["needsClarification"] for l in copied["payload"]["lessons"]))
        self.publish(copied)


if __name__ == "__main__":
    unittest.main()
