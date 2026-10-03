import json
import os
import tempfile
import unittest

from backend.database import Database


class StudentScheduleProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(os.path.join(self.temp.name, "projection.db"))
        self.database.initialize()
        with self.database.connection() as connection:
            source = connection.execute("INSERT INTO school_sources(source_type,external_key,display_name) VALUES ('schedule','projection','Projection') RETURNING id").fetchone()[0]
            run = connection.execute("INSERT INTO school_sync_runs(source_id,mode,status,idempotency_key) VALUES (?,'incremental','applied','projection') RETURNING id", (source,)).fetchone()[0]
            self.snapshot = connection.execute("INSERT INTO school_source_snapshots(source_id,sync_run_id,fingerprint,observed_at,status,is_last_known_valid) VALUES (?,?,'projection',CURRENT_TIMESTAMP,'valid',1) RETURNING id", (source, run)).fetchone()[0]
            self.identity = connection.execute("INSERT INTO identities(kind,display_name,class_name,status) VALUES ('student','Projection Student','10-А','active') RETURNING id").fetchone()[0]
            self.user = connection.execute("INSERT INTO users(telegram_user_id,role,identity_id) VALUES (9001,'student',?) RETURNING id", (self.identity,)).fetchone()[0]
            self.group = connection.execute("INSERT INTO groups(name,display_name,group_type,base_class_name,canonical) VALUES ('10-А','10-А','class','10-А',1) RETURNING id").fetchone()[0]
            connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (self.group, self.identity))
            connection.execute("INSERT INTO group_schedule_audiences(group_id,audience) VALUES (?,'10-А')", (self.group,))

    def tearDown(self):
        self.temp.cleanup()

    def _lesson(self, key, day, start, subject, audience="10-А", activity="lesson", source_day_label=""):
        with self.database.connection() as connection:
            return connection.execute("""INSERT INTO schedule_lessons(source_snapshot_id,record_key,version_kind,week_start,lesson_date,start_time,end_time,subject,teacher_hint,room,audience,activity_type,lesson_kind,resolution_status,raw_payload)
                VALUES (?,?, 'weekly','2026-09-07',?,?,'09:45',?,?,?,?,?,'lesson','RESOLVED',?) RETURNING id""", (self.snapshot, key, day, start, subject, "Teacher", "17", audience, activity, json.dumps({"source_day_label": source_day_label}))).fetchone()[0]

    def _allocation(self, day, start, kind, lesson_id=None, provenance=None):
        with self.database.connection() as connection:
            connection.execute("""INSERT INTO schedule_student_allocations(source_snapshot_id,week_start,lesson_date,start_time,end_time,grade_scope,student_identity_id,lesson_id,allocation_kind,status,reason,provenance)
                VALUES (?,?,?,?,?,'10',?,?,?,? ,?,?)""", (self.snapshot, '2026-09-07', day, start, '09:45', self.identity, lesson_id, kind, 'conflict' if kind == 'conflict' else 'assigned', kind, json.dumps(provenance or {})))

    def test_projection_calculates_gap_and_end_of_day_reasons(self):
        first = self._lesson("first", "2026-09-07", "09:00", "Математика")
        second = self._lesson("second", "2026-09-07", "11:00", "Английский")
        self._allocation("2026-09-07", "09:00", "lesson", first)
        self._allocation("2026-09-07", "10:00", "no_lesson")
        self._allocation("2026-09-07", "11:00", "lesson", second)
        self._allocation("2026-09-07", "12:00", "no_lesson")
        projection = self.database.student_schedule_projection(self.user, "2026-09-07")
        items = projection["days"][0]["items"]
        reasons = {item["slot"]["start"]: item["no_lesson"]["reason"] for item in items if item["state"] == "no_lesson"}
        self.assertEqual(reasons, {"10:00": "break", "12:00": "end_of_day"})

    def test_projection_keeps_all_conflict_alternatives_and_separate_event(self):
        left = self._lesson("left", "2026-09-08", "10:00", "Химия ОГЭ")
        right = self._lesson("right", "2026-09-08", "10:00", "Информатика ОГЭ")
        event = self._lesson("trip", "2026-09-08", "13:00", "Поездка в Абрамцево", activity="special_event")
        self._allocation("2026-09-08", "10:00", "conflict", provenance={"candidate_lesson_ids": [str(left), str(right)]})
        projection = self.database.student_schedule_projection(self.user, "2026-09-08")
        items = projection["days"][0]["items"]
        conflict = next(item for item in items if item["state"] == "conflict")
        self.assertEqual(conflict["conflict"]["label"], "Уточняется")
        self.assertEqual([item["title"] for item in conflict["conflict"]["alternatives"]], ["Химия ОГЭ", "Информатика ОГЭ"])
        event_item = next(item for item in items if item["state"] == "special_event")
        self.assertEqual(event_item["special_event"]["title"], "Поездка в Абрамцево")
        self.assertEqual(event_item["canonical_title"], None)

    def test_projection_replaces_friday_grade_10_with_sdep_day(self):
        lesson = self._lesson("friday", "2026-09-11", "09:00", "Математика", source_day_label="Пт SDEP")
        self._allocation("2026-09-11", "09:00", "lesson", lesson)
        projection = self.database.student_schedule_projection(self.user, "2026-09-11")
        self.assertEqual(projection["days"][0]["state"], "special")
        self.assertEqual(projection["days"][0]["special"]["kind"], "sdep")
        self.assertEqual(projection["days"][0]["items"], [])

    def test_projection_keeps_friday_sdep_rule_for_grade_ten_without_day_label(self):
        lesson = self._lesson("plain-friday", "2026-09-11", "09:00", "Математика", source_day_label="Пт")
        self._allocation("2026-09-11", "09:00", "lesson", lesson)
        projection = self.database.student_schedule_projection(self.user, "2026-09-11")
        self.assertEqual(projection["days"][0]["state"], "special")
        self.assertEqual(projection["days"][0]["special"]["kind"], "sdep")
        self.assertEqual(projection["days"][0]["items"], [])


if __name__ == "__main__":
    unittest.main()
