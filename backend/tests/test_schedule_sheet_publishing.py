import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.services.google_contract import SCHOOL_SCHEDULE_SPREADSHEET_ID
from backend.services.google_live import GoogleLiveError
from backend.services.schedule_experiments import ExperimentConflict, create_experiment, get_experiment, save_experiment
from backend.services.schedule_sheet_export import freeze_week
from backend.services.schedule_sheet_publishing import (
    ExportAuthorizationRequired, ExportPreviewConflict, export_client, export_saved_schedule,
    prepare_export, preview_saved_export, TEST_EXPORT_TARGET,
)
from backend.services.teacher_ui_colors import initialize_sqlite_schema, set_manual_color


class FakeSheets:
    def __init__(self, lost_response=False):
        self.sheets = {}
        self.values = {}
        self.writes = []
        self.targets = []
        self.lost_response = lost_response

    def spreadsheet(self, target):
        self.targets.append(target)
        return {"sheets": [{"properties": s} for s in self.sheets.values()]}

    def sheet_values(self, target, name):
        self.targets.append(target)
        return self.values.get(name, [])

    def batch_update_spreadsheet(self, target, requests):
        self.targets.append(target)
        self.writes.append(requests)
        for r in requests:
            if 'addSheet' in r:
                p = r['addSheet']['properties']
                self.sheets[p['sheetId']] = p
        for r in requests:
            if 'updateCells' in r:
                u = r['updateCells']
                title = self.sheets[u['start']['sheetId']]['title']
                self.values[f"'{title}'!A1:B1"] = [[c.get('userEnteredValue', {}).get('stringValue', '') for c in u['rows'][0]['values'][:2]]]
        if self.lost_response:
            self.lost_response = False
            raise GoogleLiveError('lost response after Google committed')


class SchedulePublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'test.sqlite3')
        self.db.initialize()
        self.group = str(self.db.create_group('7', 'class')['id'])
        self.teacher = str(self.db.create_identity('teacher', 'Учитель')['id'])
        self.payload = {'week_start': '2026-10-19', 'days': [0], 'slots': [{'start': '09:00', 'end': '09:45'}],
                        'classes': [self.group], 'lessons': [{'id': 'original', 'subject': 'Математика', 'day': 0,
                        'period': 0, 'start': '09:00', 'end': '09:45', 'classIds': [self.group], 'teacherIds': [self.teacher],
                        'room': '17', 'audience': {'kind': 'classes', 'class_ids': [self.group]}}]}
        self.draft = create_experiment(self.db, name='Тестовая неделя', source='import', payload=self.payload)

    def tearDown(self):
        self.temp.cleanup()

    def confirm(self, draft_id, revision=1, *, client=None, settings=None):
        preview = preview_saved_export(self.db, draft_id, expected_revision=revision)
        return export_saved_schedule(self.db, draft_id, expected_revision=revision,
                                     preview_token=preview["preview_token"], settings=settings, client=client)

    def test_authorization_required_is_not_reported_as_success(self):
        result = self.confirm(self.draft["id"], settings=SimpleNamespace(local_test_mode=True))
        self.assertEqual(result['status'], 'authorization_required')
        self.assertNotIn('url', result)
        with self.assertRaises(ExperimentConflict):
            prepare_export(self.db, self.draft['id'], expected_revision=2)

    def test_application_never_falls_back_to_unencrypted_cli_tokens(self):
        settings = SimpleNamespace(local_test_mode=False, google_oauth_client_id='configured',
                                   google_oauth_client_secret='configured', google_oauth_redirect_uri='https://example.test/callback',
                                   google_oauth_token_encryption_key='configured', google_sheets_spreadsheet_id=None,
                                   google_classroom_course_id=None)
        with patch.dict('os.environ', {'MY_ISLAND_SCHOOL_WORKSPACE': ''}), patch('backend.services.schedule_sheet_publishing.GoogleTokenStore.load') as token_load:
            with self.assertRaises(ExportAuthorizationRequired):
                export_client(self.db, settings)
            token_load.assert_not_called()

    def test_approved_destination_can_be_previewed_confirmed_and_reopened(self):
        approved = "synthetic-approved-export-workbook-1"
        client = FakeSheets()
        with patch.dict(os.environ, {"GOOGLE_SCHEDULE_EXPORT_SPREADSHEET_ID": approved,
                                     "GOOGLE_SCHEDULE_EXPORT_APPROVED_SPREADSHEET_IDS":
                                         f"synthetic-other-workbook, {approved}",
                                     "GOOGLE_SHEETS_SPREADSHEET_ID": ""}):
            preview = preview_saved_export(self.db, self.draft["id"], expected_revision=1)
            self.assertEqual(preview["target_url"], f"https://docs.google.com/spreadsheets/d/{approved}/edit")
            first = self.confirm(self.draft["id"], client=client)
            repeated = self.confirm(self.draft["id"], client=client)
        self.assertEqual(repeated["status"], "existing")
        self.assertEqual(repeated["url"], first["url"])
        self.assertEqual(set(client.targets), {approved})
        self.assertEqual(len(client.writes), 1)

    def test_rejects_invalid_unapproved_and_source_destinations(self):
        configured_source = "synthetic-configured-source-1"
        provenance_source = "synthetic-draft-source-1"
        draft = create_experiment(self.db, name="Источник", source="import", payload={
            **self.payload, "school_source": {"spreadsheet_id": provenance_source},
        })
        cases = [
            ("https://docs.google.com/spreadsheets/d/synthetic-url", "без ссылки"),
            ("", "без ссылки"),
            ("synthetic-unapproved-export", "ещё не разрешена"),
            (SCHOOL_SCHEDULE_SPREADSHEET_ID, "только для чтения"),
            (configured_source, "только для чтения"),
            (provenance_source, "только для чтения"),
        ]
        approved = ",".join((SCHOOL_SCHEDULE_SPREADSHEET_ID, configured_source, provenance_source))
        for target, issue in cases:
            with self.subTest(target=target), patch.dict(os.environ, {
                "GOOGLE_SCHEDULE_EXPORT_SPREADSHEET_ID": target,
                "GOOGLE_SCHEDULE_EXPORT_APPROVED_SPREADSHEET_IDS": approved,
                "GOOGLE_SHEETS_SPREADSHEET_ID": configured_source,
            }):
                with self.assertRaisesRegex(ValueError, issue):
                    preview_saved_export(self.db, draft["id"], expected_revision=1)

    def test_publish_rechecks_selected_destination_after_google_metadata_read(self):
        other = "synthetic-newly-selected-export"
        client = FakeSheets()
        original = client.spreadsheet

        def switch_target(target):
            metadata = original(target)
            os.environ["GOOGLE_SCHEDULE_EXPORT_SPREADSHEET_ID"] = other
            return metadata

        with patch.dict(os.environ, {"GOOGLE_SCHEDULE_EXPORT_SPREADSHEET_ID": TEST_EXPORT_TARGET,
                                     "GOOGLE_SCHEDULE_EXPORT_APPROVED_SPREADSHEET_IDS": other,
                                     "GOOGLE_SHEETS_SPREADSHEET_ID": ""}), patch.object(
                                         client, "spreadsheet", side_effect=switch_target):
            with self.assertRaisesRegex(ExportPreviewConflict, "Книга для выгрузок изменилась"):
                self.confirm(self.draft["id"], client=client)
        self.assertEqual(client.targets, [TEST_EXPORT_TARGET])
        self.assertEqual(client.writes, [])

    def test_stored_receipt_cannot_supply_a_different_destination_or_url(self):
        client = FakeSheets()
        other = "synthetic-approved-external-book"
        with patch.dict(os.environ, {"GOOGLE_SCHEDULE_EXPORT_SPREADSHEET_ID": TEST_EXPORT_TARGET,
                                     "GOOGLE_SCHEDULE_EXPORT_APPROVED_SPREADSHEET_IDS": other,
                                     "GOOGLE_SHEETS_SPREADSHEET_ID": ""}):
            first = self.confirm(self.draft["id"], client=client)
            with self.db.connection() as connection:
                connection.execute("UPDATE schedule_sheet_exports SET url=? WHERE export_key=?",
                                   ("https://example.invalid/", first["export_key"]))
            preview = preview_saved_export(self.db, self.draft["id"], expected_revision=1)
            self.assertEqual(preview["url"], first["url"])
            with self.db.connection() as connection:
                connection.execute("UPDATE schedule_sheet_exports SET target_id=? WHERE export_key=?",
                                   (other, first["export_key"]))
            with self.assertRaises(ExportPreviewConflict):
                export_saved_schedule(self.db, self.draft["id"], expected_revision=1,
                                      preview_token=preview["preview_token"], settings=None, client=client)
        self.assertEqual(set(client.targets), {TEST_EXPORT_TARGET})
        self.assertEqual(len(client.writes), 1)

    def test_repeat_returns_same_document_and_new_revision_gets_new_tab(self):
        client = FakeSheets()
        first = self.confirm(self.draft["id"], client=client)
        again = self.confirm(self.draft["id"], client=client)
        self.assertEqual(first['url'], again['url'])
        self.assertEqual(again['status'], 'existing')
        self.assertEqual(len(client.writes), 1)
        self.payload['lessons'][0]['room'] = '19'
        save_experiment(self.db, self.draft['id'], expected_revision=1, payload=self.payload)
        changed = self.confirm(self.draft["id"], revision=2, client=client)
        self.assertNotEqual(first['url'], changed['url'])
        self.assertEqual(len(client.sheets), 4)
        with self.db.connection() as c:
            old = self.db.execute(c, 'SELECT snapshot FROM schedule_sheet_exports WHERE export_key=?', (first['export_key'],)).fetchone()
        self.assertEqual(json.loads(old['snapshot'])['payload']['lessons'][0]['room'], '17')

    def test_lost_google_response_recovers_without_duplicate_tabs(self):
        client = FakeSheets(lost_response=True)
        result = self.confirm(self.draft["id"], client=client)
        self.assertEqual(result['status'], 'exported')
        self.assertEqual(len(client.writes), 1)
        self.assertEqual(len(client.sheets), 2)

    def test_existing_week_export_is_reused_across_layout_updates(self):
        client = FakeSheets()
        with patch("backend.services.schedule_sheet_publishing.EXPORT_POLICY", "compact-activities-v2"):
            old = self.confirm(self.draft["id"], client=client)
        preview = preview_saved_export(self.db, self.draft["id"], expected_revision=1)
        self.assertEqual(preview["status"], "existing")
        self.assertEqual(preview["url"], old["url"])
        self.assertEqual(self.confirm(self.draft["id"], client=client)["status"], "existing")
        self.assertEqual(len(client.writes), 1)

    def test_exact_revision_link_and_rename_after_lost_receipt(self):
        from backend.services.schedule_sheet_publishing import saved_export_link
        from backend.services.schedule_experiments import save_experiment

        draft_id = self.draft["id"]
        client = FakeSheets()
        first = self.confirm(draft_id, client=client)
        gid = int(first["url"].split("=")[-1])
        self.assertEqual(saved_export_link(self.db, draft_id, expected_revision=1)["url"], first["url"])
        client.sheets[gid]["title"] = "Изменено вручную"
        with self.db.connection() as conn:
            conn.execute("UPDATE schedule_sheet_exports SET status='prepared',url=NULL")
        self.assertEqual(self.confirm(draft_id, client=client)["url"], first["url"])
        self.assertEqual(len(client.writes), 1)
        save_experiment(self.db, draft_id, expected_revision=1, payload=self.payload)
        link = saved_export_link(self.db, draft_id, expected_revision=2)
        self.assertFalse(link["current"])
        self.assertEqual(link["previous_revision"], 1)
        self.assertNotIn("#gid=", link["url"])

    def test_export_title_collision_adds_a_suffix_without_touching_existing_tabs(self):
        client = FakeSheets()
        plan = prepare_export(self.db, self.draft["id"], expected_revision=1)
        client.sheets[7] = {"sheetId": 7, "title": plan["title"]}
        result = self.confirm(self.draft["id"], client=client)
        gid = int(result["url"].split("=")[-1])
        self.assertEqual(client.sheets[7]["title"], plan["title"])
        self.assertEqual(client.sheets[gid]["title"], plan["title"] + " (2)")
        self.assertEqual(self.confirm(self.draft["id"], client=client)["url"], result["url"])

    def test_group_order_survives_subject_swaps_and_empty_left_lane(self):
        from backend.services.schedule_sheet_export import build_sheet_plan, freeze_template
        families = json.loads((Path(__file__).parent / "fixtures/schedule_lane_order.json").read_text())
        for names in families:
            groups = [{"id": "class", "name": "7", "group_type": "class"}]
            groups.extend({"id": name, "name": name, "group_type": "subject"} for name in names)
            lessons = []
            for day, keys in enumerate([names, names[::-1], names[-1:]]):
                for offset, key in enumerate(keys):
                    lessons.append({"id": f"{day}-{key}", "day": day, "period": 0, "classId": "class",
                                    "groupId": key, "subject": ["История", "Литература", "Физика"][offset]})
            snapshot = freeze_template({"groups": groups, "teachers": [], "source": {},
                "payload": {"classes": ["class"], "days": [0, 1, 2], "lessons": lessons,
                            "slots": [{"start": "09:00", "end": "09:45"}]}})
            plan = build_sheet_plan(snapshot, layout="school-grid", title="Демо", sheet_id=1, mapping_sheet_id=2)
            for item in plan["mapping"]:
                self.assertEqual(item["col"], 1 if item["day"] == 2 else 1 + names.index(item["group_ids"][0]))
            solo = next(item for item in plan["mapping"] if item["day"] == 2)
            self.assertEqual(next(c["width"] for c in plan["cells"] if c["row"] == solo["row"] and c["col"] == solo["col"]), len(names))
            for day in range(3):
                self.assertEqual(len({item["row"] for item in plan["mapping"] if item["day"] == day}), 1)

    def test_school_grid_keeps_parallel_cards_and_hidden_source_map(self):
        from backend.services.schedule_sheet_export import sheets_requests
        another = str(self.db.create_group("8", "class")["id"])
        one = {**self.payload["lessons"][0], "classIds": [self.group, another],
               "note": "Возьмите тетрадь.\nMY_ISLAND:{\"internal\":true}", "needsClarification": True}
        two = {**self.payload["lessons"][0], "id": "parallel", "subject": "Практикум по физике",
               "activityType": "practice", "format": "online"}
        draft = create_experiment(self.db, name="Неделя", source="import",
                                  payload={**self.payload, "classes": [self.group, another], "lessons": [one, two]})
        plan = prepare_export(self.db, draft["id"], expected_revision=1)["plan"]
        self.assertEqual(plan["columns"], 4)
        self.assertEqual(len({m["row"] for m in plan["mapping"]}), 1)
        self.assertEqual(len(plan["mapping"]), 2)
        self.assertEqual(len(next(m for m in plan["mapping"] if m["lesson_id"] == "original")["references"]), 1)
        cells = "\n".join(c["text"] for c in plan["cells"])
        self.assertIn("Математика", cells)
        self.assertIn("Онлайн", cells)
        self.assertIn("Уточнить · Проверьте исходные данные в My Island", cells)
        requests = sheets_requests(plan)
        self.assertTrue(requests[1]["addSheet"]["properties"]["hidden"])
        visible = next(r["updateCells"] for r in requests if r.get("updateCells", {}).get("start", {}).get("sheetId") == plan["sheet_id"])
        notes = [c["note"] for r in visible["rows"] for c in r["values"] if c.get("note")]
        self.assertTrue(any("Возьмите тетрадь." in note for note in notes))
        self.assertEqual(notes, ["Возьмите тетрадь."])
        self.assertLessEqual(max(plan["row_heights"].values()), 88)

    def test_bell_times_and_colored_links_survive_a_friday_laboratory_and_optional_activity(self):
        from backend.services.schedule_sheet_export import build_sheet_plan, freeze_template, sheets_requests
        groups = [{"id": str(grade), "name": str(grade), "group_type": "class"} for grade in range(5, 12)]
        lessons = [
            {"id": "lab", "day": 4, "period": 0, "end": "10:40", "classIds": ["5", "6"],
             "subject": "Лаборатория текста", "activityType": "laboratory", "teacherId": "teacher"},
            {"id": "math", "day": 4, "period": 0, "classIds": ["5"], "subject": "Математика"},
            {"id": "art", "day": 4, "period": 1, "classIds": ["5"], "subject": "Литература"},
            {"id": "club", "day": 4, "period": 1, "classIds": ["5"], "subject": "Медиамастерская",
             "activityType": "club"},
        ]
        snapshot = freeze_template({"groups": groups, "teachers": [{"id": "teacher", "display_name": "Тест"}],
            "source": {"draft_id": "draft"}, "payload": {"classes": [g["id"] for g in groups], "days": [4],
            "lessons": lessons, "slots": [{"start": "09:00", "end": "09:45"}, {"start": "09:55", "end": "10:40"}]}})
        plan = build_sheet_plan(snapshot, layout="school-grid", title="Пятница", sheet_id=70, mapping_sheet_id=71)
        self.assertLessEqual(sum(plan["column_widths"]), 1223)
        cells = {(c["row"], c["col"]): c for c in plan["cells"]}
        friday = plan["day_rows"][0]
        self.assertEqual([cells[friday+offset, 0]["text"] for offset in (1, 2)], ["09:00–09:45", "09:55–10:40"])
        self.assertEqual(len(plan["mapping"]), len(lessons))
        lab = next(m for m in plan["mapping"] if m["lesson_id"] == "lab")
        cards = [cells[r["row"], r["col"]] for r in [lab, *lab["references"]]]
        self.assertEqual(len(cards), 4)
        self.assertTrue(all(c["bg"] == cards[0]["bg"] and c["link_row"] == lab["details_row"] and "Лаб" in c["text"]
                            for c in cards))
        self.assertTrue(any(c["text"].startswith("↳") for c in cards))
        self.assertIn("09:00–10:40", cells[lab["details_row"], 1]["text"])
        self.assertIn("Кружки и дополнительные занятия", cells[friday+3, 1]["text"])
        club = next(m for m in plan["mapping"] if m["lesson_id"] == "club")
        self.assertGreater(club["row"], friday+3)
        self.assertFalse(any(c["note"] for c in cells.values()))
        sheet = "https://docs.google.com/spreadsheets/d/test/edit"
        requests = sheets_requests(plan, spreadsheet_url=sheet, frontend_url="https://schedule.example")
        overview = next(r["updateCells"] for r in requests if r.get("updateCells", {}).get("start", {}).get("sheetId") == 70)
        native_cell = overview["rows"][lab["row"]]["values"][lab["col"]]
        self.assertEqual(native_cell["userEnteredFormat"]["textFormat"]["link"]["uri"],
                         f"{sheet}#gid=70&range=A{lab['details_row']+1}")
        details = overview["rows"][lab["details_row"]]["values"][1]
        self.assertTrue(details["userEnteredFormat"]["textFormat"]["link"]["uri"].endswith("?draft=draft&lesson=lab"))

    def test_legacy_week_with_missing_receipt_is_recovered_without_upload(self):
        client = FakeSheets()
        with patch("backend.services.schedule_sheet_publishing.EXPORT_POLICY", "compact-activities-v2"):
            old = self.confirm(self.draft["id"], client=client)
        with self.db.connection() as connection:
            connection.execute("UPDATE schedule_sheet_exports SET status='prepared',url=NULL WHERE export_key=?",
                               (old["export_key"],))
        recovered = self.confirm(self.draft["id"], client=client)
        self.assertEqual(recovered["status"], "existing")
        self.assertEqual(recovered["url"], old["url"])
        self.assertEqual(len(client.writes), 1)

    def test_confirmed_participants_are_not_sent_to_google(self):
        self.payload['lessons'][0].update(activityType='club', activityNameSnapshot='Шахматы', confirmedParticipantIds=['private-student'], eligibleGrades=[7])
        draft = create_experiment(self.db, name='Кружок', source='import', payload=self.payload)
        export = prepare_export(self.db, draft['id'], expected_revision=1)
        self.assertNotIn('private-student', json.dumps(export['plan']))
        self.assertIn('confirmed_participants_fingerprint', json.dumps(export['snapshot']))

    def test_template_preview_is_undated_and_read_only_and_exports_once(self):
        payload = {key: value for key, value in self.payload.items() if key != "week_start"}
        payload["reference_date"] = "2026-10-19"
        payload["school_source"] = {"term2_preparation": {"internal_note": "private-comparison-text"}}
        payload["lessons"][0]["audience"]["include_student_ids"] = ["private-inclusion"]
        payload["lessons"][0]["audience"]["exclude_student_ids"] = ["private-exclusion"]
        draft = create_experiment(self.db, name="Тестовый шаблон", source="import", payload=payload)
        preview = preview_saved_export(self.db, draft["id"], expected_revision=1)
        self.assertEqual(preview["kind"], "template")
        self.assertIsNone(preview["week_start"])
        self.assertIn("Шаблон", preview["title"])
        self.assertEqual(preview["summary"]["lessons"], 1)
        sample = preview["sample"]
        self.assertEqual({cell["text"] for cell in sample["cells"] if cell["row"] in {1, 2} and cell["col"] == 0},
                         {"Ред. 1", "Время"})
        self.assertFalse(any("note" in cell for cell in sample["cells"]))
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schedule_sheet_exports").fetchone()[0], 0)
        client = FakeSheets()
        result = self.confirm(draft["id"], client=client)
        self.assertEqual(result["status"], "exported")
        self.assertEqual(result["kind"], "template")
        self.assertEqual(self.confirm(draft["id"], client=client)["url"], result["url"])
        self.assertEqual(len(client.writes), 1)
        self.assertNotIn("private-inclusion", json.dumps(client.writes))
        self.assertNotIn("private-exclusion", json.dumps(client.writes))
        self.assertNotIn("private-comparison-text", json.dumps(client.writes))

    def test_full_grid_preview_includes_rows_after_the_sample_and_only_visible_cell_fields(self):
        friday = {**self.payload["lessons"][0], "day": 4, "id": "friday",
                  "subject": "География пятницы", "note": "hidden-audit-note"}
        draft = create_experiment(self.db, name="Неделя", source="import",
                                  payload={**self.payload, "days": list(range(5)),
                                           "lessons": [*self.payload["lessons"], friday]})
        preview = preview_saved_export(self.db, draft["id"], expected_revision=1)
        grid, sample = preview["grid"], preview["sample"]
        self.assertEqual(grid["rows"], preview["summary"]["rows"])
        self.assertEqual(grid["columns"], preview["summary"]["columns"])
        self.assertGreater(grid["rows"], sample["rows"])
        self.assertTrue(any(cell["row"] >= sample["rows"] for cell in grid["cells"]))
        plan = prepare_export(self.db, draft["id"], expected_revision=1)["plan"]
        self.assertEqual(len(grid["cells"]), len(plan["cells"]))
        self.assertEqual(grid["column_widths"], plan["column_widths"])
        self.assertEqual(grid["row_heights"], [plan["row_heights"][row] for row in range(plan["rows"])])
        self.assertEqual(grid["days"][-1], {"row": plan["day_rows"][-1], "label": "Пятница · 23.10"})
        visible_keys = {"row", "col", "height", "width", "text", "bg", "fg", "bold", "align", "size"}
        self.assertTrue(all(visible_keys <= cell.keys() <= visible_keys | {"link_row"}
                            for cell in grid["cells"]))
        self.assertNotIn("hidden-audit-note", json.dumps(preview))

    def test_confirm_rejects_changed_directory_and_revision_before_google(self):
        preview = preview_saved_export(self.db, self.draft["id"], expected_revision=1)
        with self.db.connection() as connection:
            connection.execute("UPDATE identities SET display_name='Другой учитель' WHERE id=?", (self.teacher,))
        client = FakeSheets()
        with self.assertRaises(ExportPreviewConflict):
            export_saved_schedule(self.db, self.draft["id"], expected_revision=1,
                                  preview_token=preview["preview_token"], settings=None, client=client)
        save_experiment(self.db, self.draft["id"], expected_revision=1, payload=self.payload)
        with self.assertRaises(ExperimentConflict):
            export_saved_schedule(self.db, self.draft["id"], expected_revision=1,
                                  preview_token=preview["preview_token"], settings=None, client=client)
        self.assertEqual(client.writes, [])

    def test_http_requires_admin_and_explicit_confirmation(self):
        admin = self.db.get_or_create_user(778823, "admin")
        with self.db.connection() as connection:
            connection.execute("INSERT INTO user_roles(user_id,role,source) VALUES (?,'admin','test')", (admin["id"],))
        challenge, _ = self.db.create_admin_login_challenge(admin["id"])
        token, _ = self.db.consume_admin_login_challenge(challenge)
        app = FastAPI()
        app.include_router(create_router(self.db))
        http = TestClient(app)
        headers = {"X-Dev-Auth": f"browser:{token}"}
        path = f"/api/admin/schedule/experiments/{self.draft['id']}/export"
        body = {"expected_revision": 1}
        writer = FakeSheets()
        with patch("backend.services.schedule_sheet_publishing.export_client", return_value=writer) as connector:
            self.assertIn(http.post(path + "/preview", json=body).status_code, {401, 403})
            preview = http.post(path + "/preview", headers=headers, json=body)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["week_start"], "2026-10-19")
            confirmation = {**body, "preview_token": preview.json()["preview_token"]}
            self.assertEqual(http.post(path, headers=headers, json=confirmation).status_code, 422)
            for invalid in (False, 1):
                self.assertEqual(http.post(path, headers=headers, json={**confirmation, "confirmed": invalid}).status_code, 422)
            stale = http.post(path, headers=headers, json={**confirmation, "preview_token": "a" * 64, "confirmed": True})
            self.assertEqual(stale.status_code, 409, stale.text)
            connector.assert_not_called()
            applied = http.post(path, headers=headers, json={**confirmation, "confirmed": True})
            self.assertEqual(applied.status_code, 200, applied.text)
            self.assertEqual(applied.json()["status"], "exported")
        self.assertEqual(len(writer.writes), 1)

    def test_consolidated_export_uses_shared_teacher_color_only_for_presentation(self):
        initialize_sqlite_schema(self.db)
        set_manual_color(self.db, "school", self.teacher, "#12ABEF", 0, "admin")
        with patch.dict('os.environ', {'MY_ISLAND_CONSOLIDATED_SCHEDULE': '1'}):
            export = prepare_export(self.db, self.draft['id'], expected_revision=1, directory_database=self.db)
        draft = get_experiment(self.db, self.draft['id'])
        with self.db.connection() as connection:
            groups = [dict(row) for row in self.db.execute(connection, "SELECT id,name,display_name,group_type,base_class_name,subject,subject_subgroup,semantic_dimension FROM groups ORDER BY id").fetchall()]
            teachers = [dict(row) for row in self.db.execute(connection, "SELECT id,display_name FROM identities WHERE kind='teacher' ORDER BY id").fetchall()]
        for row in groups + teachers:
            row['id'] = str(row['id'])
        source = {"source": {"draft_id": self.draft['id'], "name": draft['name'], "revision": 1,
                             "school_source": draft['payload'].get('school_source')},
                  "payload": draft['payload'], "groups": groups, "teachers": teachers}
        baseline = freeze_week(source, draft['payload']['week_start'])
        exported_teacher = next(row for row in export['snapshot']['teachers'] if row['id'] == self.teacher)
        self.assertEqual(exported_teacher['ui_color'], "#12ABEF")
        self.assertEqual(export['snapshot']['snapshot_id'], baseline['snapshot_id'])
        self.assertEqual(export['snapshot']['payload'], baseline['payload'])
        self.assertFalse(any('ui_color' in row for row in export['snapshot']['payload']['lessons']))
        card_color = "#" + "".join(f"{round(value*.21+255*.79):02X}" for value in (18, 171, 239))
        self.assertTrue(any(cell["bg"] == card_color for cell in export["plan"]["cells"]))


if __name__ == '__main__':
    unittest.main()
