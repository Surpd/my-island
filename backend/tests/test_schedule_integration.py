import copy
import json
import os
import tempfile
import unittest
from uuid import uuid4

from backend.database import Database
from backend.services.schedule_pipeline import _baseline_for_date_range, _date_for_weekday, _json, classify_tab, diff_template_week, parse_date_range, parse_schedule_matrix, recalculate_current_schedule, reconcile_current_schedule, refresh_schedule_pipeline, schedule_reconciliation_needs_refresh, schedule_reconciliation_view


def cell(value: str, color: dict | None = None) -> dict:
    payload = {"formattedValue": value}
    if color:
        payload["effectiveFormat"] = {"backgroundColor": color}
    return payload


def matrix(*, weekly: bool, changed_teacher: bool = False, room: str = "каб.1") -> list[list[object]]:
    red = {"red": 0.8, "green": 0.4, "blue": 0.4}
    return [
        ["", "9-А", "", "9-Д"],
        ["07.09" if weekly else "", "Пн", "", "Пн"],
        ["9:00 - 9:45", cell(("Физ Иван" if changed_teacher else "Матем ДФ") + f"\n{room}", red), "", cell("Матем ДФ\nкаб.2", red)],
        ["9:55 - 10:40", cell("Русский ДФ\nкаб.1", red), "", cell("Русский ДФ\nкаб.2", red)],
        ["10:55 - 11:40", cell("История ДФ\nкаб.1", red), "", cell("История ДФ\nкаб.2", red)],
        ["11:50 - 12:35", cell("Литература ДФ\nкаб.1", red), "", cell("🥨", {"red": 0.98, "green": 0.75, "blue": 0.56})],
    ]


class ScheduleIntegrationTests(unittest.TestCase):
    def test_postgres_json_serializes_resolved_uuid_values(self):
        identity_id = uuid4()
        payload = _json(Database(database_url="postgresql://unused"), {"resolved_identity_ids": [identity_id]})
        self.assertEqual(json.loads(payload.dumps(payload.obj)), {"resolved_identity_ids": [str(identity_id)]})

    def test_russian_title_and_tab_classification(self):
        self.assertEqual(parse_date_range("7-11 Сентября 2026"), ("2026-09-07", "2026-09-11"))
        self.assertEqual(parse_date_range("14 - 18.09", 2026), ("2026-09-14", "2026-09-18"))
        self.assertEqual(classify_tab("7-11 Сентября"), "weekly")
        self.assertEqual(classify_tab("2026/27 Шаблон"), "template")

    def test_partial_week_compares_only_dates_covered_by_tab(self):
        baseline = [{"slot_key": "mon", "weekday": 0}, {"slot_key": "wed", "weekday": 2}, {"slot_key": "sat", "weekday": 5}]
        covered = _baseline_for_date_range(baseline, ("2026-09-02", "2026-09-04"))
        self.assertEqual([item["slot_key"] for item in covered], ["wed"])
        self.assertEqual([item["change"] for item in diff_template_week(covered, [])], ["CANCELLED"])
        self.assertEqual(_date_for_weekday(("2026-09-02", "2026-09-04"), 2), "2026-09-02")

    def test_persistent_teacher_mapping_reconciles_current_and_future_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "schedule.db")); database.initialize()
            with database.connection() as connection:
                teacher_id = connection.execute("INSERT INTO identities(kind,display_name,status) VALUES ('teacher','Андрей','active') RETURNING id").fetchone()[0]
                connection.execute("INSERT INTO groups(name,display_name,group_type,canonical) VALUES ('9-А','9-А','class',1)")
            template = {"sheet_id": 10, "title": "2026/27 шаблон", "values": matrix(weekly=False), "merges": []}
            weekly = {"sheet_id": 20, "title": "7-11 Сентября", "values": matrix(weekly=True), "merges": []}
            weekly["values"][2][1]["formattedValue"] = "Матем АнК\nкаб.1"
            refresh_schedule_pipeline(database, "spreadsheet", "Расписание 2026/27", [template, weekly])
            self.assertFalse(schedule_reconciliation_needs_refresh(database))
            group = next(item for item in schedule_reconciliation_view(database, "2026-09-07")["issue_groups"] if item["external_key"] == "АнК")
            self.assertEqual(group["mapping_type"], "identity")
            database.save_schedule_v1_mapping("identity", "АнК", target_id=teacher_id)
            reconcile_current_schedule(database)
            reconciled = next(item for item in database.schedule_v1_lessons(week_start="2026-09-07") if item["source_cell"] == "B3")
            self.assertEqual(reconciled["resolution_status"], "RESOLVED")
            changed = copy.deepcopy(weekly); changed["values"][2][1]["formattedValue"] = "Матем АнК\nкаб.2"
            refresh_schedule_pipeline(database, "spreadsheet", "Расписание 2026/27", [template, changed])
            future = next(item for item in database.schedule_v1_lessons(week_start="2026-09-07") if item["source_cell"] == "B3")
            self.assertEqual(future["teacher_id"], str(teacher_id))

    def test_recalculate_reuses_raw_snapshot_and_applies_business_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "schedule.db")); database.initialize()
            template = {"sheet_id": 10, "title": "2026/27 шаблон", "values": matrix(weekly=False), "merges": []}
            weekly = {"sheet_id": 20, "title": "7-11 Сентября", "values": matrix(weekly=True), "merges": []}
            weekly["values"][2][1]["formattedValue"] = "Курс по выбору: Внутренний курс\nЛюбой преподаватель"
            weekly["values"][3][1]["formattedValue"] = "Кружок робототехники\nЛюбой преподаватель"
            first = refresh_schedule_pipeline(database, "spreadsheet", "Расписание 2026/27", [template, weekly])
            recalculated = recalculate_current_schedule(database)
            self.assertFalse(recalculated["created_snapshot"])
            self.assertEqual(recalculated["recalculated_snapshot_id"], first["snapshot_id"])
            self.assertEqual(database.schedule_v1_lessons(week_start="2026-09-07")[0]["subject"], "Курс по выбору")
            self.assertFalse(any("Кружок" in str(item.get("subject")) for item in database.schedule_v1_lessons(week_start="2026-09-07")))

    def test_real_matrix_shape_parses_blank_owned_column_and_template_weekday(self):
        tab = {"sheet_id": 17, "title": "2026/27 шаблон", "values": matrix(weekly=False),
               "merges": [{"startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 1, "endColumnIndex": 3}]}
        lessons = parse_schedule_matrix(tab, "Расписание 2026/27")
        self.assertEqual(len(lessons), 8)
        self.assertIsNone(lessons[0]["lesson_date"])
        self.assertEqual(lessons[0]["weekday"], 0)
        self.assertEqual(lessons[0]["audience"], "9-А")
        self.assertEqual(lessons[0]["merged_audiences"], ["9-А"])
        self.assertEqual(lessons[0]["source_cell"], "B3")
        self.assertTrue(lessons[0]["source_color"])

    def test_snapshot_resolution_color_conflict_baseline_diff_and_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "schedule.db")); database.initialize()
            with database.connection() as connection:
                connection.execute("INSERT INTO identities(kind,display_name,status) VALUES ('teacher','Дмитрий Федорович','active')")
                connection.execute("INSERT INTO identities(kind,display_name,status) VALUES ('teacher','Иван Петрович','active')")
                connection.execute("INSERT INTO groups(name,display_name,group_type,canonical) VALUES ('9-А','9-А','class',1)")
                connection.execute("INSERT INTO groups(name,display_name,group_type,canonical) VALUES ('9-Д','9-Д','class',1)")
            with database.connection() as connection:
                before = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("identities", "groups", "memberships", "teacher_assignments"))
            template = {"sheet_id": 10, "title": "2026/27 шаблон", "values": matrix(weekly=False), "merges": []}
            weekly = {"sheet_id": 20, "title": "7-11 Сентября", "values": matrix(weekly=True, changed_teacher=True), "merges": []}
            first = refresh_schedule_pipeline(database, "spreadsheet", "Расписание 2026/27", [template, weekly])
            self.assertEqual(first["summary"]["parsed_weekly_lessons"], 8)
            overview = database.schedule_v1_overview()
            self.assertEqual(overview["summary"]["lessons"], 7)
            self.assertGreaterEqual(overview["summary"]["same"], 1)
            lessons = database.schedule_v1_lessons(week_start="2026-09-07")
            replaced = next(item for item in lessons if item["source_cell"] == "B3")
            self.assertEqual(replaced["diff_status"], "REPLACED")
            self.assertEqual(replaced["resolution_status"], "CONFLICT")
            self.assertIn("color", replaced["issue_reason"].casefold())
            with database.connection() as connection:
                after = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("identities", "groups", "memberships", "teacher_assignments"))
            self.assertEqual(after, before)
            unchanged = refresh_schedule_pipeline(database, "spreadsheet", "Расписание 2026/27", [template, weekly])
            self.assertEqual(unchanged["status"], "unchanged")
            self.assertEqual(unchanged["snapshot_id"], first["snapshot_id"])
            noted_weekly = copy.deepcopy(weekly)
            noted_weekly["values"][3][1]["note"] = "Проверено завучем"
            noted = refresh_schedule_pipeline(database, "spreadsheet", "Расписание 2026/27", [template, noted_weekly])
            with database.connection() as connection:
                noted_changes = connection.execute("SELECT COUNT(*) FROM school_source_records WHERE snapshot_id=? AND change_kind='changed'", (noted["snapshot_id"],)).fetchone()[0]
            self.assertNotEqual(noted["snapshot_id"], first["snapshot_id"])
            self.assertEqual(noted_changes, 1)
            changed_weekly = copy.deepcopy(noted_weekly)
            changed_weekly["values"][2][1]["formattedValue"] = "Физ Иван\nкаб.9"
            changed = refresh_schedule_pipeline(database, "spreadsheet", "Расписание 2026/27", [template, changed_weekly])
            with database.connection() as connection:
                count = connection.execute("SELECT COUNT(*) FROM school_source_records WHERE snapshot_id=? AND change_kind='changed'", (changed["snapshot_id"],)).fetchone()[0]
            self.assertEqual(count, 1)
            renamed = {**changed_weekly, "title": "7 - 11 сентября"}
            renamed_result = refresh_schedule_pipeline(database, "spreadsheet", "Расписание 2026/27", [template, renamed])
            with database.connection() as connection:
                weekly_tabs = connection.execute("SELECT COUNT(*) FROM schedule_source_tabs WHERE classification='weekly'").fetchone()[0]
                changed_cells = connection.execute("SELECT COUNT(*) FROM school_source_records WHERE snapshot_id=? AND change_kind='changed'", (renamed_result["snapshot_id"],)).fetchone()[0]
            self.assertEqual(weekly_tabs, 1)
            self.assertEqual(changed_cells, 0)

    def test_unknown_schedule_like_tab_creates_issue_but_no_lessons(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "schedule.db")); database.initialize()
            result = refresh_schedule_pipeline(database, "spreadsheet", "Расписание 2026/27", [{"sheet_id": 99, "title": "Новая неделя", "values": matrix(weekly=False), "merges": []}])
            self.assertEqual(result["summary"]["parsed_weekly_lessons"], 0)
            self.assertEqual(database.schedule_v1_lessons(), [])
            self.assertEqual(len(database.schedule_v1_issues()), 1)

    def test_baseline_diff(self):
        result = diff_template_week(
            [{"slot_key": "a", "subject": "Математика", "teacher_hint": "ДФ", "room": "17", "modifiers": {}}],
            [{"slot_key": "a", "subject": "Математика", "teacher_hint": "ДФ", "room": "6", "modifiers": {}}, {"slot_key": "b", "subject": "Экскурсия", "activity_type": "special_event"}],
        )
        self.assertEqual([item["change"] for item in result], ["MODIFIED", "SPECIAL_EVENT"])


if __name__ == "__main__":
    unittest.main()
