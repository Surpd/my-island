from unittest import TestCase
from unittest.mock import patch

from backend.services.canonical_schedule import import_canonical_artifact, materialize_effective_week
from backend.services.class_load_analytics import class_load_analytics
from backend.tests import test_canonical_schedule as fixtures


class ClassLoadTests(TestCase):
    setUp = fixtures.CanonicalScheduleRuntimeTests.setUp
    tearDown = fixtures.CanonicalScheduleRuntimeTests.tearDown
    artifact = fixtures.CanonicalScheduleRuntimeTests.artifact

    def known_report(self, *args, **kwargs):
        from backend.services.canonical_schedule import resolve_published_week

        def resolve(database, day):
            edition = resolve_published_week(database, day)
            if edition.effective:
                edition.metadata["coverage"] = "complete"
            return edition

        with patch("backend.services.class_load_analytics.canonical.resolve_published_week", side_effect=resolve):
            return class_load_analytics(self.database, *args, **kwargs)

    def test_class_load_deduplicates_co_teaching_interval(self):
        second_teacher = self.database.create_identity("teacher", "Teacher Two")
        artifact = self.artifact()
        assignment = artifact["blocks"][self.block_key]["assignments"][0]
        assignment["teacher_ids"] = [str(self.teacher["id"]), str(second_teacher["id"])]
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-09-07")

        result = self.known_report("2026-09-07")
        group = next(item for item in result["groups"] if str(self.math_group["id"]) == item["group_id"])
        self.assertEqual(group["scheduled_minutes"], 45)
        self.assertEqual(group["lesson_count"], 1)

    def test_class_load_reports_trajectory_and_merges_overlapping_windows(self):
        artifact = self.artifact()
        artifact["blocks"][self.block_key]["assignments"][0]["teacher_ids"] = [str(self.teacher["id"])]
        artifact["blocks"][self.block_key]["assignments"][0]["activity"] = "Первая часть"
        artifact["blocks"]["2026-09-07|1|09:15|9A"] = {
            "weekday": 0, "slot": {"start": "09:15", "end": "10:00"}, "grade_scope": "9",
            "status": "resolved", "mode": "SINGLE", "assignments": [{
                "activity": "Вторая часть", "role": "primary",
                "audience": {"type": "canonical_groups", "canonical_group_ids": [str(self.math_group["id"])]},
                "teacher_ids": [str(self.teacher["id"])], "source_cells": ["C3"],
            }],
        }
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-09-07")
        result = self.known_report("2026-09-07", group_type="instructional")
        group = next(item for item in result["groups"] if item["group_id"] == str(self.math_group["id"]))
        self.assertEqual(group["trajectory"], "")
        self.assertEqual(group["scheduled_minutes"], 60)
        self.assertEqual(group["day_summaries"][0]["window_minutes"], 60)
        self.assertEqual(group["day_summaries"][0]["gap_minutes"], 0)

    def test_class_load_batch_query_count_does_not_scale_with_teacher_roster(self):
        import_canonical_artifact(self.database, self.artifact())
        materialize_effective_week(self.database, self.version_id, "2026-09-07")
        with patch.object(self.database, "execute", wraps=self.database.execute) as execute:
            class_load_analytics(self.database, "2026-09-07")
            small_roster_calls = execute.call_count
        for index in range(40):
            self.database.create_identity("teacher", f"Class Unassigned {index}")
        with patch.object(self.database, "execute", wraps=self.database.execute) as execute:
            class_load_analytics(self.database, "2026-09-07")
            large_roster_calls = execute.call_count
        self.assertLessEqual(large_roster_calls, small_roster_calls + 1)

    def test_three_parallel_groups_common_period_pause_optional_and_unstaffed_lessons(self):
        klass = str(self.base_group["id"])
        tracks = [str(self.database.create_group(f"Track {i}", "instructional", base_class_name="9A",
                  subject="Lab", subject_subgroup=str(i))["id"]) for i in range(3)]
        with self.database.connection() as conn:
            conn.execute("UPDATE identities SET status='archived' WHERE id=?", (self.teacher["id"],))
        artifact = self.artifact()
        artifact["blocks"] = {}
        def add(key, start, end, targets, *, teacher=None, kind="lesson", joint=None):
            metadata = {"activity_type": kind}
            if klass not in targets:
                metadata["audience_rule"] = {"kind": "groups", "class_group_id": klass}
            if joint:
                metadata.update(audience_coverage="explicit", joint_activity_id=joint)
            artifact["blocks"][key] = {"weekday": 0, "grade_scope": "9", "status": "resolved",
                "slot": {"start":start, "end":end}, "mode":"SINGLE", "assignments":[{"activity":"Lab",
                    "audience":{"canonical_group_ids":targets}, "role":"primary", "metadata":metadata,
                    "teacher_ids":[str(teacher)] if teacher else []}]}
        add("common","08:00","08:30",[klass])
        for i,gid in enumerate(tracks):
            add(f"group-{i}", "09:00","09:45",[gid],teacher=self.teacher["id"] if i else None)
        add("joint-a","10:00","10:30",[tracks[0]],joint="verified")
        add("joint-b","10:00","10:30",[tracks[1]],joint="verified")
        add("lunch","09:45","10:00",[klass],kind="lunch")
        add("club","16:00","17:30",[klass],kind="club")
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-12-28")
        result = self.known_report("2026-12-28","2027-01-03")
        by_id = {g["group_id"]:g for g in result["groups"]}
        summary = by_id[klass]
        self.assertEqual(summary["lesson_count"],5)
        self.assertEqual(summary["scheduled_minutes"],105)
        self.assertEqual(summary["assignment_minutes"],195)
        self.assertEqual(summary["mean_lesson_minutes"],39)
        self.assertEqual(summary["optional_minutes"],90)
        self.assertEqual(summary["day_summaries"][0]["gap_minutes"],30)
        self.assertEqual(summary["day_summaries"][0]["excluded_pause_minutes"],15)
        self.assertEqual(summary["subject_minutes"],{"Lab":195})
        self.assertEqual([by_id[x]["lesson_count"] for x in tracks],[3,3,2])
        joint = next(x for x in summary["items"] if x["joint_activity_id"])
        self.assertEqual(len(joint["provenance"]["sources"]),2)
        self.assertEqual(joint["provenance"]["effective_week_id"],result["coverage"]["weeks"][0]["effective_week_id"])

    def test_missing_broken_and_approved_ranges_have_independent_coverage(self):
        from backend.services.constructor_publication import initialize_publication_schema
        initialize_publication_schema(self.database)
        import_canonical_artifact(self.database,self.artifact())
        materialize_effective_week(self.database,self.version_id,"2026-12-28")
        materialize_effective_week(self.database,self.version_id,"2027-01-04")
        with self.database.connection() as conn:
            conn.execute("INSERT INTO schedule_publication_heads VALUES(?,?,?,?,?)",
                         ("week:2027-01-04","broken",self.version_id,1,"2027-01-04"))
        result = class_load_analytics(self.database,"2026-12-28","2027-01-17")
        self.assertEqual(result["status"],"partial")
        self.assertEqual([x["origin"] for x in result["coverage"]["weeks"]],
                         ["approved_materialization","broken_head","template_only"])
        self.assertTrue(all(x["date"] < "2027-01-04" for g in result["groups"] for x in g["items"]))
        missing = class_load_analytics(self.database,"2027-01-04",group_id=str(self.base_group["id"]))
        self.assertEqual(missing["status"], "broken_publication")
        self.assertIsNone(missing["groups"][0]["scheduled_minutes"])
        self.assertIsNone(missing["groups"][0]["observed_scheduled_minutes"])
        partial = next(x for x in result["groups"] if x["group_id"] == str(self.base_group["id"]))
        self.assertEqual(partial["coverage_status"], "partial")
        self.assertIsNone(partial["lesson_count"])
        self.assertIsNone(partial["scheduled_minutes"])
        self.assertEqual(partial["observed_scheduled_minutes"], 45)
        empty = self.known_report("2027-01-03",group_id=str(self.base_group["id"]))
        self.assertEqual(empty["groups"][0]["coverage_status"], "complete")
        self.assertEqual(empty["groups"][0]["scheduled_minutes"], 0)

    def test_historical_parent_comes_from_dated_membership_not_current_label(self):
        old_class = str(self.base_group["id"])
        new_class = str(self.database.create_group("9B", "class", base_class_name="9B")["id"])
        group = str(self.math_group["id"])
        with self.database.connection() as conn:
            conn.execute("UPDATE groups SET base_class_name='9B' WHERE id=?", (group,))
            conn.execute("DELETE FROM memberships")
            for group_id, start, end in ((group, "2026-09-01", "2026-09-30"),
                                         (old_class, "2026-09-01", "2026-09-30"),
                                         (new_class, "2026-10-01", "2026-12-31")):
                conn.execute("""INSERT INTO memberships (group_id,identity_id,member_role,source,active,valid_from,valid_until)
                    VALUES (?,?,'student','admin_override',1,?,?)""", (group_id, self.student["id"], start, end))
        artifact = self.artifact()
        artifact["blocks"] = {self.block_key: artifact["blocks"][self.block_key]}
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-09-07")
        materialize_effective_week(self.database, self.version_id, "2026-10-05")
        result = class_load_analytics(self.database, "2026-09-07")
        self.assertIn(old_class, [entry["group_id"] for entry in result["groups"]])
        self.assertNotIn(new_class, [entry["group_id"] for entry in result["groups"]])
        unavailable = class_load_analytics(self.database, "2026-10-05")
        self.assertEqual(unavailable["coverage"]["unknown_parent_items"], 1)
        self.assertEqual([row["group_id"] for row in unavailable["groups"]], [group])

    def test_sdep_plan_is_not_counted_as_a_class_lesson(self):
        artifact = self.artifact()
        artifact["blocks"] = {self.block_key: artifact["blocks"][self.block_key]}
        artifact["blocks"][self.block_key]["assignments"][0]["metadata"] = {"day_plan_mode": "sdep"}
        import_canonical_artifact(self.database, artifact)
        materialize_effective_week(self.database, self.version_id, "2026-09-07")
        result = class_load_analytics(self.database, "2026-09-07")
        self.assertEqual(result["groups"], [])
