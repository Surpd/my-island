import os
import tempfile
import unittest

from backend.database import Database
from backend.services.schedule_allocation import _candidate_groups, rebuild_schedule_allocations


class ScheduleAllocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(os.path.join(self.temp.name, "allocation.db"))
        self.database.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_english_number_is_authoritative_without_active_members(self):
        groups = [
            {"id": "six", "name": "english:6", "display_name": "Английский · группа 6", "group_type": "subject_group", "subject": "Английский язык", "base_class_name": None, "subject_subgroup": "6"},
            {"id": "seven", "name": "english:7", "display_name": "Английский · группа 7", "group_type": "subject_group", "subject": "Английский язык", "base_class_name": None, "subject_subgroup": "7"},
        ]
        lesson = {"subject": "Английский", "modifiers": {"subject_subgroup": "7"}, "activity_type": "lesson"}
        group_ids, reason = _candidate_groups(lesson, "9", groups, {"six": {"student"}, "seven": set()}, {"student"}, [], {})
        self.assertEqual(group_ids, ["seven"])
        self.assertEqual(reason, "explicit membership")

    def test_english_one_two_line_is_projected_to_both_grades(self):
        with self.database.connection() as connection:
            source = connection.execute("INSERT INTO school_sources(source_type,external_key,display_name) VALUES ('schedule','english-line','English line') RETURNING id").fetchone()[0]
            run = connection.execute("INSERT INTO school_sync_runs(source_id,mode,status,idempotency_key) VALUES (?,'incremental','applied','english-line') RETURNING id", (source,)).fetchone()[0]
            snapshot = connection.execute("INSERT INTO school_source_snapshots(source_id,sync_run_id,fingerprint,observed_at,status,is_last_known_valid) VALUES (?,?,'english-line',CURRENT_TIMESTAMP,'valid',1) RETURNING id", (source, run)).fetchone()[0]
            classes = {grade: connection.execute("INSERT INTO groups(name,display_name,group_type,base_class_name,canonical) VALUES (?,?, 'class',?,1) RETURNING id", (grade, grade, grade)).fetchone()[0] for grade in ('5', '6')}
            english = {number: connection.execute("INSERT INTO groups(name,display_name,group_type,subject,subject_subgroup,canonical) VALUES (?,?, 'subject_group','Английский язык',?,1) RETURNING id", (f'english:{number}', f'English {number}', number)).fetchone()[0] for number in ('1', '2')}
            students = {}
            for grade in ('5', '6'):
                for number in ('1', '2'):
                    student = connection.execute("INSERT INTO identities(kind,display_name,status) VALUES ('student',?,'active') RETURNING id", (f'{grade}-{number}',)).fetchone()[0]
                    students[(grade, number)] = student
                    connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (classes[grade], student))
                    connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (english[number], student))
            for number, audience, cell in (('1', '5', 'B3'), ('2', '6', 'D3')):
                connection.execute("""INSERT INTO schedule_lessons(source_snapshot_id,record_key,version_kind,week_start,lesson_date,start_time,end_time,subject,audience,activity_type,lesson_kind,modifiers,resolution_status,source_cell,raw_payload)
                    VALUES (?,?,'weekly','2026-09-07','2026-09-07','10:55','11:40','Английский',?,'lesson','lesson',?,'WARNING',?,?)""",
                    (snapshot, f'english-{number}', audience, '{"subject_subgroup":"' + number + '"}', cell, '{"source_column":1}'))
            result = rebuild_schedule_allocations(self.database, connection, snapshot)
            self.assertEqual(result["slots"], 2)
            rows = connection.execute("SELECT grade_scope,student_identity_id,allocation_kind,status FROM schedule_student_allocations ORDER BY grade_scope,student_identity_id").fetchall()
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(row["allocation_kind"] == "lesson" and row["status"] == "assigned" for row in rows))

    def test_explicit_groups_and_membership_conflict_without_lunch_projection(self):
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
            pretzel = connection.execute("""INSERT INTO schedule_lessons(source_snapshot_id,record_key,version_kind,week_start,lesson_date,start_time,end_time,subject,audience,activity_type,lesson_kind,modifiers,resolution_status,source_cell,raw_payload)
                VALUES (?,?,'weekly','2026-09-07','2026-09-07','09:00','09:45','🥨','9-А','lesson','lesson','{}','RESOLVED','E4',?) RETURNING id""",
                (snapshot, "pretzel", '{"raw_text":"🥨"}')).fetchone()[0]
            result = rebuild_schedule_allocations(self.database, connection, snapshot)
            self.assertEqual(result["slots"], 1)
            rows = connection.execute("SELECT student_identity_id,allocation_kind,status,reason FROM schedule_student_allocations ORDER BY student_identity_id").fetchall()
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["status"], "conflict")
            self.assertEqual(rows[1]["reason"], "explicit membership")
            self.assertTrue(all(row["allocation_kind"] in {"lesson", "no_lesson", "conflict"} for row in rows))
            self.assertEqual(sum(1 for row in rows if row["allocation_kind"] == "no_lesson"), 2)
            self.assertFalse(connection.execute("SELECT 1 FROM schedule_lesson_audiences WHERE lesson_id=?", (pretzel,)).fetchone())
            self.assertFalse(connection.execute("SELECT 1 FROM schedule_student_allocations WHERE lesson_id=?", (pretzel,)).fetchone())

    def test_friday_sdep_skips_slot_allocations_and_exposes_day_block(self):
        with self.database.connection() as connection:
            source = connection.execute("INSERT INTO school_sources(source_type,external_key,display_name) VALUES ('schedule','sdep','SDEP') RETURNING id").fetchone()[0]
            run = connection.execute("INSERT INTO school_sync_runs(source_id,mode,status,idempotency_key) VALUES (?,'incremental','applied','sdep-run') RETURNING id", (source,)).fetchone()[0]
            snapshot = connection.execute("INSERT INTO school_source_snapshots(source_id,sync_run_id,fingerprint,observed_at,status,is_last_known_valid) VALUES (?,?,'sdep-fp',CURRENT_TIMESTAMP,'valid',1) RETURNING id", (source, run)).fetchone()[0]
            class_group = connection.execute("INSERT INTO groups(name,display_name,group_type,base_class_name,canonical) VALUES ('10-А','10-А','class','10-А',1) RETURNING id").fetchone()[0]
            student = connection.execute("INSERT INTO identities(kind,display_name,status) VALUES ('student','SDEP student','active') RETURNING id").fetchone()[0]
            connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (class_group, student))
            connection.execute("""INSERT INTO schedule_lessons(source_snapshot_id,record_key,version_kind,week_start,lesson_date,start_time,end_time,subject,audience,activity_type,lesson_kind,modifiers,resolution_status,source_cell,raw_payload)
                VALUES (?,?,'weekly','2026-09-07','2026-09-11','10:00','10:45','Математика','10-А','lesson','lesson','{}','WARNING','B4',?)""",
                (snapshot, "sdep-math", '{"raw_text":"Математика 10-А"}'))
            result = rebuild_schedule_allocations(self.database, connection, snapshot)
            self.assertEqual(result["slots"], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schedule_student_allocations").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schedule_lesson_audiences").fetchone()[0], 0)
        qa = self.database.schedule_allocation_qa("2026-09-07", "10", str(student), None)
        self.assertEqual(qa["summary"]["slots"], 0)
        self.assertEqual(qa["summary"]["sdep_days"], 1)
        self.assertEqual(qa["day_blocks"][0]["label"], "SDEP")
        self.assertEqual(qa["day_blocks"][0]["raw_activities"][0]["source_cell"], "B4")
        self.assertEqual(qa["student_day_blocks"][0]["kind"], "sdep")
        self.assertEqual(self.database.schedule_allocation_qa("2026-09-07", "9", None, None)["summary"]["sdep_days"], 0)


if __name__ == "__main__":
    unittest.main()
