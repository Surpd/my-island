import os
import tempfile
import unittest

from backend.database import Database
from backend.services.schedule_allocation import rebuild_schedule_allocations


class ScheduleAllocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(os.path.join(self.temp.name, "allocation.db"))
        self.database.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_explicit_groups_complement_lunch_and_membership_conflict(self):
        with self.database.connection() as connection:
            source = connection.execute("INSERT INTO school_sources(source_type,external_key,display_name) VALUES ('schedule','test','Test') RETURNING id").fetchone()[0]
            run = connection.execute("INSERT INTO school_sync_runs(source_id,mode,status,idempotency_key) VALUES (?,'incremental','applied','run') RETURNING id", (source,)).fetchone()[0]
            snapshot = connection.execute("INSERT INTO school_source_snapshots(source_id,sync_run_id,fingerprint,observed_at,status,is_last_known_valid) VALUES (?,?,'fp',CURRENT_TIMESTAMP,'valid',1) RETURNING id", (source, run)).fetchone()[0]
            class_group = connection.execute("INSERT INTO groups(name,display_name,group_type,base_class_name,canonical) VALUES ('9-А','9-А','class','9-А',1) RETURNING id").fetchone()[0]
            math = connection.execute("INSERT INTO groups(name,display_name,group_type,subject,base_class_name,subject_subgroup,canonical) VALUES ('math-a','Math A','subject_group','Математика','9','A',1) RETURNING id").fetchone()[0]
            literature = connection.execute("INSERT INTO groups(name,display_name,group_type,subject,base_class_name,subject_subgroup,exam_track,canonical) VALUES ('lit-oge','Literature OGE','subject_group','Литература','9','ОГЭ','ОГЭ',1) RETURNING id").fetchone()[0]
            students = [connection.execute("INSERT INTO identities(kind,display_name,status) VALUES ('student',?,'active') RETURNING id", (f"Ученик {index}",)).fetchone()[0] for index in range(1, 5)]
            for student in students:
                connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (class_group, student))
            connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (math, students[0]))
            connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (literature, students[0]))
            connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (literature, students[1]))
            lessons = [
                ("math", "Математика", "lesson", '{"subject_subgroup":"A"}', "B4"),
                ("lit", "Литература", "lesson", '{"exam_track":"ОГЭ"}', "C4"),
                ("lunch", "Обед / перерыв", "nonlesson", '{}', "D4"),
            ]
            for key, subject, kind, modifiers, cell in lessons:
                connection.execute("""INSERT INTO schedule_lessons(source_snapshot_id,record_key,version_kind,week_start,lesson_date,start_time,end_time,subject,audience,activity_type,lesson_kind,modifiers,resolution_status,source_cell)
                    VALUES (?,?, 'weekly','2026-09-07','2026-09-07','09:00','09:45',?,'9-А',?,?,?,'RESOLVED',?)""",
                    (snapshot, key, subject, kind, kind, modifiers, cell))
            result = rebuild_schedule_allocations(self.database, connection, snapshot)
            self.assertEqual(result["slots"], 1)
            rows = connection.execute("SELECT student_identity_id,allocation_kind,status,reason FROM schedule_student_allocations ORDER BY student_identity_id").fetchall()
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["status"], "conflict")
            self.assertEqual(rows[1]["reason"], "explicit membership")
            self.assertEqual(rows[2]["reason"], "contextual lunch")
            self.assertEqual(rows[3]["allocation_kind"], "lunch")


if __name__ == "__main__":
    unittest.main()
