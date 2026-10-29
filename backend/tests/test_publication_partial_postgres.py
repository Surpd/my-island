"""Partial constructor-publication contract against an isolated PostgreSQL DB.

Run with MY_ISLAND_RELEASE_TEST_DATABASE_URL set to the disposable loopback
fixture documented by backend/scripts/release_postgres_check.py. The guard
below intentionally refuses every other host, port, or database name.
"""
from __future__ import annotations

import copy
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from uuid import uuid4
from unittest.mock import patch

import psycopg
from psycopg import sql

from backend.database import Database
from backend.services import canonical_schedule as canonical
from backend.services.constructor_publication import (
    PublicationConflict,
    list_publications,
    preview_publication,
    preview_restore,
    publish_publication,
    restore_publication,
    _TransactionDatabase, _compile, _persist,
)
from backend.services.schedule_experiments import create_experiment, save_experiment


ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_PORT = 55432
ALLOWED_DATABASE = "my_island_release_check"


def _database_url() -> str:
    value = os.environ.get("MY_ISLAND_RELEASE_TEST_DATABASE_URL", "")
    parsed = urlparse(value)
    if (parsed.scheme not in {"postgres", "postgresql"}
            or parsed.hostname not in ALLOWED_HOSTS
            or parsed.port != ALLOWED_PORT
            or parsed.path.lstrip("/") != ALLOWED_DATABASE):
        raise RuntimeError(
            "Refusing PostgreSQL test connection; use the disposable loopback "
            "database on 127.0.0.1:55432/my_island_release_check"
        )
    return value


class PartialPublicationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not os.environ.get("MY_ISLAND_RELEASE_TEST_DATABASE_URL"):
            raise unittest.SkipTest("MY_ISLAND_RELEASE_TEST_DATABASE_URL is not configured")
        cls.database = Database(database_url=_database_url())
        with psycopg.connect(cls.database.database_url) as connection:
            version = connection.execute("SELECT current_setting('server_version_num')::int").fetchone()[0]
        if version < 170000:
            raise RuntimeError("Partial publication PostgreSQL contract requires PostgreSQL 17+")
        with cls.database.connection() as connection:
            required = ("canonical_schedule_versions", "schedule_experiment_drafts",
                        "schedule_publication_heads", "schedule_constructor_publications")
            for table in required:
                exists = cls.database.execute(connection,
                    "SELECT to_regclass(?) AS table_name", (f"public.{table}",)).fetchone()
                if not exists["table_name"]:
                    raise RuntimeError(f"PostgreSQL schema fixture is missing public.{table}")
            # This fixture must be schema-only. Never add publication test data to
            # a previously used fixture, let alone a school database.
            cls.fixture_tables = [row["tablename"] for row in cls.database.execute(connection,
                "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()]
            for table in cls.fixture_tables:
                count = connection.execute(
                    sql.SQL("SELECT count(*) AS n FROM public.{}").format(sql.Identifier(table))).fetchone()["n"]
                if count:
                    raise RuntimeError(f"Refusing non-empty PostgreSQL fixture: public.{table}")

    def tearDown(self):
        # The class guard proved this exact loopback database was schema-only.
        # Remove only synthetic rows produced by this test, keeping each case isolated.
        with psycopg.connect(_database_url()) as connection:
            connection.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(",").join(sql.Identifier("public", table) for table in self.fixture_tables)))

    def setUp(self):
        self.suffix = uuid4().hex[:10]
        self.database = self.__class__.database
        self.class9 = self.database.create_group(f"9A Partial {self.suffix}", "class", base_class_name="9A")
        self.class9b = self.database.create_group(f"9B Partial {self.suffix}", "class", base_class_name="9B")
        self.class10 = self.database.create_group(f"10A Partial {self.suffix}", "class", base_class_name="10A")
        self.teacher = self.database.create_identity("teacher", f"PG Teacher {self.suffix}")
        self.student = self.database.create_identity("student", f"PG Student {self.suffix}", "9A")
        self.student9b = self.database.create_identity("student", f"PG Student 9B {self.suffix}", "9B")
        self.database.create_membership(self.class9["id"], self.student["id"], "student", "admin_override", self.suffix)
        self.database.create_membership(self.class9b["id"], self.student9b["id"], "student", "admin_override", self.suffix)
        self.database.create_membership(self.class10["id"], self.student["id"], "student", "admin_override", self.suffix)

    def lesson(self, lesson_id: str, group=None, **overrides):
        group = group or self.class9
        return {
            "id": lesson_id,
            "day": 0,
            "period": 0,
            "classId": str(group["id"]),
            "teacherId": str(self.teacher["id"]),
            "subject": lesson_id,
            "room": "R1",
            "audience": {"kind": "classes", "class_ids": [str(group["id"])]},
            **overrides,
        }

    def draft(self, lessons, *, slots=None):
        return create_experiment(self.database, name=f"PG partial {self.suffix}", source="import", payload={
            "reference_date": "2026-10-19",
            "days": [0],
            "classes": [str(self.class9["id"]), str(self.class10["id"])],
            "slots": slots or [
                {"start": "09:00", "end": "09:45"},
                {"start": "10:00", "end": "10:45"},
                {"start": "11:00", "end": "11:45"},
            ],
            "lessons": lessons,
        })

    def preview(self, draft, scope="template", **kwargs):
        return preview_publication(self.database, draft["id"], scope_kind=scope,
            expected_revision=draft["revision"], **kwargs)

    def confirm(self, draft, preview, scope="template", **kwargs):
        return publish_publication(self.database, draft["id"], scope_kind=scope,
            expected_revision=draft["revision"], preview_token=preview["preview_token"],
            confirmed=True, **kwargs)

    def test_all_386_unresolved_sources_remain_immutable_in_postgres(self):
        from backend.services.constructor_publication import get_published_source

        draft = self.draft([
            self.lesson(f"unresolved-{index}", subject="Unresolved source", audience={"kind": "groups", "group_ids": [f"unknown-{index}"]})
            for index in range(386)
        ])
        preview = self.preview(draft)
        self.assertEqual(preview["summary"]["source_lessons"], 386)
        self.assertEqual(preview["summary"]["campus_lessons"], 0)
        self.assertEqual(preview["summary"]["admin_only_lessons"], 386)
        self.assertTrue(preview["can_publish"], preview["blockers"])
        published = self.confirm(draft, preview)
        archive = get_published_source(self.database, published["publication_id"])["published_source"]
        self.assertEqual(archive["payload"], draft["payload"])
        self.assertEqual(len(archive["records"]), 386)
        self.assertEqual({row["status"] for row in archive["records"]}, {"unresolved"})
        draft["revision"] = published["draft_revision"]
        week = self.confirm(draft, self.preview(draft, "week", week_start="2026-11-02"),
                            "week", week_start="2026-11-02")
        week_archive = get_published_source(self.database, week["publication_id"])["published_source"]
        self.assertEqual(len(week_archive["records"]), 386)
        self.assertEqual(canonical.project_student(self.database, self.student["id"], "2026-11-02")["items"], [])
        teacher = canonical.project_teacher(self.database, self.teacher["id"], "2026-11-02")
        self.assertEqual(len(teacher["items"]), 1)
        self.assertEqual(teacher["items"][0]["state"], "UNRESOLVED")
        self.assertIsNone(teacher["items"][0]["activity"])
        self.assertEqual(len(teacher["items"][0]["provenance"]["unresolved_record_ids"]), 386)

        # Editing the mutable draft cannot change either published JSONB source.
        changed = copy.deepcopy(draft["payload"])
        changed["lessons"][0]["subject"] = "Changed only in draft"
        save_experiment(self.database, draft["id"], expected_revision=week["draft_revision"], payload=changed)
        self.assertEqual(get_published_source(self.database, published["publication_id"])["published_source"], archive)
        self.assertEqual(get_published_source(self.database, week["publication_id"])["published_source"], week_archive)
        head = canonical.publication_head(self.database, "template")
        restore = preview_restore(self.database, published["publication_id"], expected_head_revision=head["revision"])
        restored = restore_publication(self.database, published["publication_id"],
            expected_head_revision=head["revision"], preview_token=restore["preview_token"], confirmed=True)
        self.assertEqual(get_published_source(self.database, restored["publication_id"])["published_source"], archive)
        self.assertEqual(get_published_source(self.database, week["publication_id"])["published_source"], week_archive)

    def test_partial_preview_publish_cas_history_projection_and_rollback(self):
        # Persist the new explicit/joint contract in PostgreSQL JSONB, verify
        # both runtime projections, then roll back this entire sub-scenario.
        joint_class = self.database.create_group(f"11A Joint {self.suffix}", "class", base_class_name="11A")
        joint_student = self.database.create_identity("student", f"PG Joint {self.suffix}", "11A")
        self.database.create_membership(joint_class["id"], joint_student["id"], "student", "admin_override", self.suffix)
        joint = self.draft([self.lesson("joint-9", subject="Joint"),
                            self.lesson("joint-11", group=joint_class, subject="Joint")])
        with self.database.connection() as connection:
            with connection.transaction(force_rollback=True):
                bound = _TransactionDatabase(self.database, connection)
                joint_preview = preview_publication(bound, joint["id"], scope_kind="template", expected_revision=1)
                blocks,_,_ = _compile(bound, joint["payload"], None, "template", "2026-10-19")
                _persist(bound, joint_preview, blocks, None)
                weekly_preview = preview_publication(bound, joint["id"], scope_kind="week", expected_revision=1, week_start="2026-10-19")
                _persist(bound, weekly_preview, blocks, None)
                teacher = canonical.project_teacher(bound, self.teacher["id"], "2026-10-19")
                self.assertEqual(len(teacher["items"]), 1)
                self.assertEqual(teacher["issues"], [])
                self.assertEqual(set(teacher["items"][0]["audience_group_ids"]), {str(self.class9["id"]),str(joint_class["id"])})
                for student in (self.student,joint_student):
                    projected = canonical.project_student(bound, student["id"], "2026-10-19")
                    self.assertEqual([r["activity"] for r in projected["items"] if r["state"]=="ACTIVITY"], ["Joint"])
                    self.assertEqual(projected["issues"], [])
        # First-template publication has no canonical base. A valid lesson can
        # publish while unknown-teacher and review-required units are excluded.
        first = self.draft([
            self.lesson("first-safe", period=0, room=""),
            self.lesson("unknown-teacher", period=1, teacherId=str(uuid4())),
            self.lesson("needs-review", period=2, review_required=True),
        ])
        first_preview = self.preview(first)
        self.assertIsNone(first_preview["base_version_id"])
        self.assertTrue(first_preview["can_publish"], first_preview["blockers"])
        self.assertEqual(first_preview["blockers"], [])
        issues = {item["code"]: item for item in first_preview["diagnostics"]}
        self.assertEqual(issues["missing_room"]["severity"], "info")
        self.assertEqual(issues["unknown_teacher"]["severity"], "conflict")
        self.assertEqual(issues["unknown_teacher"]["action"], "retained_unresolved")
        self.assertEqual(issues["review_required"]["action"], "retained_unresolved")
        self.assertEqual(first_preview["summary"]["ready_lessons"], 1)
        self.assertEqual({item["lesson_id"] for item in first_preview["deferred"]},
                         {"unknown-teacher", "needs-review"})
        first_result = self.confirm(first, first_preview)
        first_version = first_result["published_id"]
        first_artifact = canonical.read_canonical_template(self.database, first_version)
        first_semantic_snapshot = copy.deepcopy(first_artifact)
        self.assertTrue(first_result["runtime_readback"]["verified"])

        # Keep one historical and one current-week runtime snapshot across
        # later template and current-week republishing.
        historical = self.draft([self.lesson("Historical", period=0)])
        historical_result = self.confirm(historical,
            self.preview(historical, "week", week_start="2026-09-21"),
            "week", week_start="2026-09-21")
        current = self.draft([self.lesson("Current", period=0)])
        current_result = self.confirm(current,
            self.preview(current, "week", week_start="2026-10-26"),
            "week", week_start="2026-10-26")
        historical_id = canonical._effective_week(self.database, None, "2026-09-21")["effective_week_id"]
        historical_projection = canonical.project_student(self.database, self.student["id"], "2026-09-21")
        current_id = canonical._effective_week(self.database, None, "2026-10-26")["effective_week_id"]
        self.assertEqual(historical_result["published_id"], historical_id)
        self.assertEqual(current_result["published_id"], current_id)

        week = self.draft([
            self.lesson("week-safe", period=0),
            self.lesson("week-unknown-teacher", period=1, teacherId=str(uuid4())),
            self.lesson("week-needs-review", period=2, review_required=True),
        ])
        week_preview = self.preview(week, "week", week_start="2026-10-19")
        self.assertTrue(week_preview["can_publish"], week_preview["blockers"])
        self.confirm(week, week_preview, "week", week_start="2026-10-19")
        projection = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        self.assertEqual([item["activity"] for item in projection["items"] if item["state"] == "ACTIVITY"], ["week-safe"])
        unresolved = [item for item in projection["items"] if item["state"] == "UNRESOLVED"]
        self.assertEqual(len(unresolved), 2)
        self.assertTrue(all(item["activity"] is None for item in unresolved))
        self.assertEqual({item["start_time"] for item in unresolved}, {"10:00", "11:00"})
        self.assertNotIn("known_student_ids", str(projection))

        # Information-only preview remains publishable, and publishing a new
        # template never mutates the prior immutable artifact.
        info = self.draft([self.lesson("info-only", period=0, room="")])
        info_preview = self.preview(info)
        self.assertTrue(info_preview["can_publish"], info_preview["blockers"])
        self.assertEqual(info_preview["summary"]["info_count"], 1)
        self.assertEqual(info_preview["blockers"], [])
        info_result = self.confirm(info, info_preview)
        self.assertEqual(canonical.read_canonical_template(self.database, first_version), first_semantic_snapshot)

        # Rebase and republish the current week after the new template. The
        # historical week keeps both its effective id and projected content.
        current_republish = self.draft([self.lesson("Current Updated", period=0)])
        current_republish_result = self.confirm(current_republish,
            self.preview(current_republish, "week", week_start="2026-10-26"),
            "week", week_start="2026-10-26")
        self.assertNotEqual(current_republish_result["published_id"], current_id)
        self.assertEqual(canonical._effective_week(self.database, None, "2026-09-21")["effective_week_id"], historical_id)
        self.assertEqual(canonical.project_student(self.database, self.student["id"], "2026-09-21"), historical_projection)
        current_projection = canonical.project_student(self.database, self.student["id"], "2026-10-26")
        self.assertIn("Current Updated", [item["activity"] for item in current_projection["items"]])

        # Both sides of a student-and-teacher collision are excluded; only a
        # separate safe block enters canonical runtime and the student view.
        parallel = self.draft([
            self.lesson("collision-9", group=self.class9, period=0, subject="Math"),
            self.lesson("collision-10", group=self.class10, period=0, subject="Science"),
            self.lesson("safe-after-collision", group=self.class9, period=1, subject="History"),
        ])
        overlap_preview = self.preview(parallel, "week", week_start="2026-10-19")
        self.assertTrue(overlap_preview["can_publish"], overlap_preview["blockers"])
        overlap_codes = {item["code"] for item in overlap_preview["diagnostics"]}
        self.assertIn("student_overlap", overlap_codes)
        self.assertIn("teacher_overlap", overlap_codes)
        excluded_ids = {item["lesson_id"] for item in overlap_preview["deferred"]}
        self.assertTrue({"collision-9", "collision-10"}.issubset(excluded_ids))
        overlap_result = self.confirm(parallel, overlap_preview, "week", week_start="2026-10-19")
        self.assertTrue(overlap_result["runtime_readback"]["verified"])
        projection = canonical.project_student(self.database, self.student["id"], "2026-10-19")
        projected_activities = [item["activity"] for item in projection["items"]]
        self.assertIn("History", projected_activities)
        self.assertNotIn("Math", projected_activities)
        self.assertNotIn("Science", projected_activities)

        # Unknown groups and an ambiguous available-slot parallel are excluded
        # as whole units. In particular, the residual must not spill into 9B.
        ambiguous = self.draft([
            self.lesson("safe-ambiguous-neighbor", period=0),
            self.lesson("unknown-group", period=1, classId="", classIds=[],
                audience={"kind": "groups", "group_ids": [str(uuid4())]}),
            self.lesson("ambiguous-residual", period=2,
                audience={"kind": "available_slot", "grade": "9",
                          "class_ids": [str(self.class9["id"]), str(self.class10["id"])]}),
        ])
        ambiguous_preview = self.preview(ambiguous, "week", week_start="2026-10-19")
        self.assertTrue(ambiguous_preview["can_publish"], ambiguous_preview["blockers"])
        self.assertEqual(ambiguous_preview["summary"]["ready_lessons"], 1)
        ambiguous_ids = {item["lesson_id"] for item in ambiguous_preview["deferred"]}
        self.assertTrue({"unknown-group", "ambiguous-residual"}.issubset(ambiguous_ids))
        self.assertIn("unsupported_partial_cohort", {item["code"] for item in ambiguous_preview["diagnostics"]})
        self.confirm(ambiguous, ambiguous_preview, "week", week_start="2026-10-19")
        for identity in (self.student["id"], self.student9b["id"]):
            projected = canonical.project_student(self.database, identity, "2026-10-19")["items"]
            self.assertFalse(any(item.get("activity") == "ambiguous-residual" for item in projected))
            self.assertFalse(any(item.get("activity") == "unknown-group" for item in projected))

        # Directory fingerprint and publication head are both checked at CAS.
        stale = self.draft([self.lesson("stale", period=0)])
        stale_preview = self.preview(stale)
        wrong_token = {**stale_preview, "preview_token": "wrong-preview-token"}
        with self.assertRaises(PublicationConflict):
            self.confirm(stale, wrong_token)
        self.database.create_identity("teacher", f"Directory mutation {self.suffix}")
        with self.assertRaises(PublicationConflict):
            self.confirm(stale, stale_preview)
        refreshed = self.preview(stale)
        winner = self.draft([self.lesson("head-winner", period=0)])
        winner_preview = self.preview(winner)
        self.confirm(winner, winner_preview)
        with self.assertRaises(PublicationConflict):
            self.confirm(stale, refreshed)

        saved = self.draft([self.lesson("save-cas", period=0)])
        saved_preview = self.preview(saved)
        edited_payload = copy.deepcopy(saved["payload"])
        edited_payload["lessons"][0]["room"] = "R2"
        saved_result = save_experiment(self.database, saved["id"], expected_revision=saved["revision"],
                                       payload=edited_payload)
        self.assertEqual(saved_result["revision"], 2)
        with self.assertRaises(PublicationConflict):
            self.confirm(saved, saved_preview)

        simultaneous = self.draft([self.lesson("simultaneous-confirm", period=0)])
        simultaneous_preview = self.preview(simultaneous)

        def confirm_simultaneously(_):
            try:
                self.confirm(simultaneous, simultaneous_preview)
                return "published"
            except PublicationConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(confirm_simultaneously, range(2)))
        self.assertCountEqual(outcomes, ["published", "conflict"])
        simultaneous_audits = [item for item in list_publications(self.database)["items"]
                               if item["draft_id"] == simultaneous["id"]]
        self.assertEqual(len(simultaneous_audits), 1)

        # A failed PostgreSQL runtime readback rolls back canonical rows, head,
        # and publication audit rows together.
        rollback = self.draft([self.lesson("rollback", period=0)])
        rollback_preview = self.preview(rollback)
        tables = ("canonical_schedule_versions", "canonical_schedule_blocks",
                  "canonical_schedule_assignments", "schedule_publication_heads",
                  "schedule_constructor_publications")
        with self.database.connection() as connection:
            before = {table: self.database.execute(connection,
                f"SELECT count(*) AS n FROM public.{table}").fetchone()["n"] for table in tables}
        original = canonical.read_canonical_template

        def hide_new_template(database, version_id=None):
            return None if version_id is None else original(database, version_id)

        with patch.object(canonical, "read_canonical_template", side_effect=hide_new_template):
            with self.assertRaises(RuntimeError):
                self.confirm(rollback, rollback_preview)
        with self.database.connection() as connection:
            after = {table: self.database.execute(connection,
                f"SELECT count(*) AS n FROM public.{table}").fetchone()["n"] for table in tables}
        self.assertEqual(after, before)
        self.assertGreater(info_result["draft_revision"], 0)

        # Restore creates a fresh immutable template version while both saved
        # week versions remain attached to their original historical heads.
        current_head = canonical.publication_head(self.database, "template")
        restore_preview = preview_restore(self.database, first_result["publication_id"],
                                           expected_head_revision=current_head["revision"])
        self.assertTrue(restore_preview["can_restore"], restore_preview["blockers"])
        restored = restore_publication(self.database, first_result["publication_id"],
            expected_head_revision=current_head["revision"], preview_token=restore_preview["preview_token"],
            confirmed=True)
        self.assertNotEqual(restored["published_id"], first_version)
        self.assertEqual(list_publications(self.database)["items"][0]["restored_from"], first_result["publication_id"])
        self.assertEqual(canonical._effective_week(self.database, None, "2026-09-21")["effective_week_id"], historical_id)
        self.assertEqual(canonical.project_student(self.database, self.student["id"], "2026-09-21"), historical_projection)
        self.assertEqual(canonical._effective_week(self.database, None, "2026-10-26")["effective_week_id"],
                         current_republish_result["published_id"])

        # Protected canonical state remains a critical blocker if omitted.
        restored_template = canonical.read_canonical_template(self.database, restored["published_id"])
        protected_key = next(key for key, block in restored_template["blocks"].items()
                             if any(a.get("activity") == "first-safe" for a in block.get("assignments", [])))
        with self.database.connection() as connection:
            self.database.execute(connection,
                "UPDATE canonical_schedule_blocks SET evidence=jsonb_build_object('protected',true) WHERE version_id=? AND block_key=?",
                (restored["published_id"], protected_key))
        omit_protected = self.draft([])
        protected_preview = self.preview(omit_protected)
        self.assertFalse(protected_preview["can_publish"])
        self.assertEqual({item["code"] for item in protected_preview["blockers"]}, {"protected_base"})
        self.assertEqual({item["severity"] for item in protected_preview["diagnostics"] if item["code"] == "protected_base"},
                         {"critical"})


if __name__ == "__main__":
    unittest.main()
