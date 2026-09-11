import json
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
            # A subgroup anchor wins before the OGE residual is considered;
            # this is the agreed row-wide cascade, not an unresolved conflict.
            self.assertEqual(rows[0]["status"], "assigned")
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

    def test_row_wide_parallel_lanes_assign_neighbouring_activities_by_anchor(self):
        with self.database.connection() as connection:
            source = connection.execute("INSERT INTO school_sources(source_type,external_key,display_name) VALUES ('schedule','row-wide','Row wide') RETURNING id").fetchone()[0]
            run = connection.execute("INSERT INTO school_sync_runs(source_id,mode,status,idempotency_key) VALUES (?,'incremental','applied','row-wide') RETURNING id", (source,)).fetchone()[0]
            snapshot = connection.execute("INSERT INTO school_source_snapshots(source_id,sync_run_id,fingerprint,observed_at,status,is_last_known_valid) VALUES (?,?,'row-wide',CURRENT_TIMESTAMP,'valid',1) RETURNING id", (source, run)).fetchone()[0]
            class_group = connection.execute("INSERT INTO groups(name,display_name,group_type,base_class_name,canonical) VALUES ('9-Д','9-Д','class','9-Д',1) RETURNING id").fetchone()[0]
            math_groups = {}
            for subgroup in ("A", "B", "C"):
                math_groups[subgroup] = connection.execute("INSERT INTO groups(name,display_name,group_type,subject,base_class_name,subject_subgroup,canonical) VALUES (?,?, 'subject_group','Математика','9',?,1) RETURNING id", (f"math-9-{subgroup}", f"Математика {subgroup}", subgroup)).fetchone()[0]
            students = {}
            for subgroup in ("A", "B", "C"):
                student = connection.execute("INSERT INTO identities(kind,display_name,status) VALUES ('student',?,'active') RETURNING id", (f"Student {subgroup}",)).fetchone()[0]
                students[subgroup] = student
                connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (class_group, student))
                connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (math_groups[subgroup], student))
            for index, (day, start, rows) in enumerate([
                ("2026-09-11", "11:50", [("Пластика", "9-А", 11, {}), ("Математика", "9-Д", 12, {"subject_subgroup": "B"}), ("География", "9-Д", 13, {})]),
                ("2026-09-11", "12:45", [("Математика", "9-А", 11, {"subject_subgroup": "A"}), ("Пластика", "9-Д", 12, {}), ("Пластика", "9-Д", 13, {})]),
                ("2026-09-11", "13:40", [("География", "9-А", 11, {}), ("География", "9-Д", 12, {}), ("Математика", "9-Д", 13, {"subject_subgroup": "C"})]),
            ]):
                for cell_index, (subject, audience, column, modifiers) in enumerate(rows):
                    connection.execute("""INSERT INTO schedule_lessons(source_snapshot_id,record_key,version_kind,week_start,lesson_date,start_time,end_time,subject,audience,activity_type,lesson_kind,modifiers,resolution_status,source_cell,raw_payload)
                        VALUES (?,?, 'weekly','2026-09-07',?,?,'12:35',?,?, 'lesson','lesson',?,'RESOLVED',?,?)""", (snapshot, f"row-{index}-{cell_index}", day, start, subject, audience, json.dumps(modifiers), f"{chr(76 + column - 11)}{index + 56}", json.dumps({"source_column": column})))
            rebuild_schedule_allocations(self.database, connection, snapshot)
            rows = connection.execute("""SELECT sa.start_time,sa.student_identity_id,sl.subject
                FROM schedule_student_allocations sa LEFT JOIN schedule_lessons sl ON sl.id=sa.lesson_id
                WHERE sa.source_snapshot_id=? ORDER BY sa.start_time,sa.student_identity_id""", (snapshot,)).fetchall()
            by_slot = {(row["start_time"], row["student_identity_id"]): row["subject"] for row in rows}
            self.assertEqual(by_slot[("11:50", students["C"])], "География")
            self.assertEqual(by_slot[("12:45", students["C"])], "Пластика")
            self.assertEqual(by_slot[("13:40", students["C"])], "Математика")
            self.assertEqual(by_slot[("11:50", students["A"])], "Пластика")
        # Every slot is fully covered by the three parallel cohorts; there is
        # therefore no residual no_lesson allocation in this focused case.
        self.assertEqual(sum(1 for row in rows if row["subject"] is None), 0)

    def test_0909_parallel_oge_and_no_lesson_marker_are_resolved_by_membership(self):
        with self.database.connection() as connection:
            source = connection.execute("INSERT INTO school_sources(source_type,external_key,display_name) VALUES ('schedule','0909','09.09') RETURNING id").fetchone()[0]
            run = connection.execute("INSERT INTO school_sync_runs(source_id,mode,status,idempotency_key) VALUES (?,'incremental','applied','0909') RETURNING id", (source,)).fetchone()[0]
            snapshot = connection.execute("INSERT INTO school_source_snapshots(source_id,sync_run_id,fingerprint,observed_at,status,is_last_known_valid) VALUES (?,?,'0909',CURRENT_TIMESTAMP,'valid',1) RETURNING id", (source, run)).fetchone()[0]
            base = connection.execute("INSERT INTO groups(name,display_name,group_type,base_class_name,canonical) VALUES ('9-Д','9-Д','class','9-Д',1) RETURNING id").fetchone()[0]
            defs = {
                "A": ("Математика", "A", None), "B": ("Математика", "B", None), "C": ("Математика", "C", None),
                "chem": ("Химия", "ОГЭ", "ОГЭ"), "social": ("Обществознание", "ОГЭ", "ОГЭ"),
                "physics": ("Физика", "ОГЭ", "ОГЭ"),
            }
            groups = {}
            for key, (subject, subgroup, exam) in defs.items():
                groups[key] = connection.execute("INSERT INTO groups(name,display_name,group_type,subject,base_class_name,subject_subgroup,exam_track,canonical) VALUES (?,?, 'subject_group',?,?,?,?,1) RETURNING id", (key, key, subject, "9", subgroup, exam)).fetchone()[0]
            students = {}
            for key, memberships in {"A": ["A"], "B": ["B"], "C": ["C", "social"], "E": ["chem", "physics"], "D": []}.items():
                student = connection.execute("INSERT INTO identities(kind,display_name,status) VALUES ('student',?,'active') RETURNING id", (f"09.09 {key}",)).fetchone()[0]
                students[key] = student
                connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (base, student))
                for group_key in memberships:
                    connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (groups[group_key], student))
            lessons = [
                ("09:00", "Химия", "9-Д", 11, {"exam_track": "ОГЭ"}), ("09:00", "Математика", "9-Д", 12, {"subject_subgroup": "B"}),
                ("09:00", "🥨", "9-Д", 13, {}), ("09:55", "🥨", "9-Д", 11, {}), ("09:55", "🥨", "9-Д", 12, {}),
                ("09:55", "Математика", "9-Д", 13, {"subject_subgroup": "C"}), ("10:55", "Математика", "9-Д", 11, {"subject_subgroup": "A"}),
                ("10:55", "Обществознание", "9-Д", 12, {"exam_track": "ОГЭ"}), ("10:55", "Физика", "9-Д", 13, {"exam_track": "ОГЭ"}),
                ("10:55", "Химия", "9-Д", 14, {"exam_track": "ОГЭ"}),
            ]
            for index, (start, subject, audience, column, modifiers) in enumerate(lessons):
                activity = "nonlesson" if subject == "🥨" else "lesson"
                connection.execute("""INSERT INTO schedule_lessons(source_snapshot_id,record_key,version_kind,week_start,lesson_date,start_time,end_time,subject,audience,activity_type,lesson_kind,modifiers,resolution_status,source_cell,raw_payload)
                    VALUES (?,?, 'weekly','2026-09-07','2026-09-09',?,'11:40',?,?,?, ?,?,'RESOLVED',?,?)""", (snapshot, f"0909-{index}", start, subject, audience, activity, activity, json.dumps(modifiers), f"{chr(76 + column - 11)}{index + 1}", json.dumps({"source_column": column, "raw_text": subject})))
            rebuild_schedule_allocations(self.database, connection, snapshot)
            rows = connection.execute("SELECT student_identity_id,start_time,allocation_kind,lesson_id FROM schedule_student_allocations WHERE source_snapshot_id=?", (snapshot,)).fetchall()
            lookup = {(row["start_time"], row["student_identity_id"]): row for row in rows}
            self.assertEqual(lookup[("09:00", students["B"])]["allocation_kind"], "lesson")
            self.assertEqual(lookup[("09:55", students["C"])]["allocation_kind"], "lesson")
            self.assertEqual(lookup[("09:55", students["D"])]["allocation_kind"], "no_lesson")
            self.assertGreaterEqual(sum(row["allocation_kind"] == "lesson" for row in rows), 4)

    def test_0809_mixed_base_english_math_oge_and_history_art_stays_cohort_aware(self):
        with self.database.connection() as connection:
            source = connection.execute("INSERT INTO school_sources(source_type,external_key,display_name) VALUES ('schedule','0809','08.09') RETURNING id").fetchone()[0]
            run = connection.execute("INSERT INTO school_sync_runs(source_id,mode,status,idempotency_key) VALUES (?,'incremental','applied','0809') RETURNING id", (source,)).fetchone()[0]
            snapshot = connection.execute("INSERT INTO school_source_snapshots(source_id,sync_run_id,fingerprint,observed_at,status,is_last_known_valid) VALUES (?,?,'0809',CURRENT_TIMESTAMP,'valid',1) RETURNING id", (source, run)).fetchone()[0]
            base = connection.execute("INSERT INTO groups(name,display_name,group_type,base_class_name,canonical) VALUES ('9-А','9-А','class','9-А',1) RETURNING id").fetchone()[0]
            specs = [("eng6", "Английский язык", "6", None), ("mathA", "Математика", "A", None), ("geo", "География", "ОГЭ", "ОГЭ"), ("art", "История искусства", "A/B", None)]
            groups = {}
            for key, subject, subgroup, exam in specs:
                groups[key] = connection.execute("INSERT INTO groups(name,display_name,group_type,subject,base_class_name,subject_subgroup,exam_track,canonical) VALUES (?,?, 'subject_group',?,?,?,?,1) RETURNING id", (key, key, subject, "9", subgroup, exam)).fetchone()[0]
            students = {}
            for key, memberships in {"eng": ["eng6"], "math": ["mathA"], "oge": ["geo"], "art": ["art"], "idle": []}.items():
                student = connection.execute("INSERT INTO identities(kind,display_name,status) VALUES ('student',?,'active') RETURNING id", (f"08.09 {key}",)).fetchone()[0]
                students[key] = student
                connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (base, student))
                for group_key in memberships:
                    connection.execute("INSERT INTO memberships(group_id,identity_id,member_role,source,active) VALUES (?,?,'student','test',1)", (groups[group_key], student))
            activities = [("09:00", "Английский", "eng6", 11, {"subject_subgroup": "6"}), ("09:00", "Математика", "mathA", 12, {"subject_subgroup": "A"}), ("09:00", "География", "geo", 13, {"exam_track": "ОГЭ"}), ("09:00", "История искусства", "art", 14, {"subject_subgroup": "A/B"})]
            for index, (start, subject, _, column, modifiers) in enumerate(activities):
                connection.execute("""INSERT INTO schedule_lessons(source_snapshot_id,record_key,version_kind,week_start,lesson_date,start_time,end_time,subject,audience,activity_type,lesson_kind,modifiers,resolution_status,source_cell,raw_payload)
                    VALUES (?,?, 'weekly','2026-09-07','2026-09-08',?,'09:45',?,'9-А','lesson','lesson',?,'RESOLVED',?,?)""", (snapshot, f"0809-{index}", start, subject, json.dumps(modifiers), f"{chr(76 + column - 11)}{index + 1}", json.dumps({"source_column": column})))
            rebuild_schedule_allocations(self.database, connection, snapshot)
            rows = connection.execute("SELECT student_identity_id,lesson_id,allocation_kind FROM schedule_student_allocations WHERE source_snapshot_id=?", (snapshot,)).fetchall()
            self.assertEqual(sum(row["allocation_kind"] == "lesson" for row in rows), 4)
            self.assertEqual(sum(row["allocation_kind"] == "no_lesson" for row in rows), 1)


if __name__ == "__main__":
    unittest.main()
