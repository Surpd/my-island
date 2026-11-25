"""Synthetic Sheets transport with atomic batches, stable gids and consumable named ranges."""
import copy
import os
from unittest import TestCase, mock

from backend.services import published_week_sheets as delivery
from backend.services.constructor_publication import list_publications
from backend.services.google_live import GoogleApiError
from backend.tests import test_constructor_publication as fixtures


class FakeSheets:
    def __init__(self):
        self.sheets = {7: {"properties": {"sheetId": 7, "title": "User tab", "gridProperties": {"rowCount": 20, "columnCount": 10}}}}
        self.rows = {7: [{"values": [{"userEnteredValue": {"stringValue": "Keep"}}]}]}
        self.markers = []
        self.history = []
        self.after_apply = False
        self.fail_status = None

    def weekly_spreadsheet(self, target):
        return copy.deepcopy({"sheets": list(self.sheets.values()), "namedRanges": self.markers})

    def sheet_grid_range(self, target, title, a1):
        sid = next(k for k, v in self.sheets.items() if v["properties"]["title"] == title)
        return copy.deepcopy({"sheets": [{**self.sheets[sid], "data": [{"rowData": self.rows[sid]}]}]})

    def batch_update_spreadsheet(self, target, batch):
        if self.fail_status:
            raise GoogleApiError(self.fail_status, {})
        state = copy.deepcopy((self.sheets, self.rows, self.markers))
        try:
            for request in batch:
                kind, action = next(iter(request.items()))
                if kind == "addSheet":
                    sid = action["properties"]["sheetId"]
                    if sid in self.sheets:
                        raise ValueError("existing gid")
                    self.sheets[sid] = copy.deepcopy(action)
                    self.rows[sid] = []
                elif kind == "updateCells":
                    if "rows" in action:
                        sid = action["start"]["sheetId"]
                        rows = self.rows[sid]
                        for y, row in enumerate(action["rows"]):
                            if y >= len(rows):
                                rows.append({"values": []})
                            rows[y]["values"][:len(row["values"])] = copy.deepcopy(row["values"])
                    else:
                        rect = action["range"]
                        for row in self.rows[rect["sheetId"]][:rect["endRowIndex"]]:
                            for cell in row["values"][:rect["endColumnIndex"]]:
                                cell.clear()
                elif kind == "mergeCells":
                    rect = action["range"]
                    self.sheets[rect["sheetId"]].setdefault("merges", []).append(rect)
                elif kind == "unmergeCells":
                    rect = action["range"]
                    self.sheets[rect["sheetId"]]["merges"] = [
                        item for item in self.sheets[rect["sheetId"]].get("merges", []) if not delivery._intersects(rect, item)]
                elif kind == "addNamedRange":
                    self.markers.append({**action["namedRange"], "namedRangeId": f"range-{len(self.history)}"})
                elif kind == "deleteNamedRange":
                    selected = next((m for m in self.markers if m["namedRangeId"] == action["namedRangeId"]), None)
                    if selected is None:
                        raise ValueError("predecessor already consumed")
                    self.markers.remove(selected)
                elif kind == "appendDimension":
                    key = "rowCount" if action["dimension"] == "ROWS" else "columnCount"
                    self.sheets[action["sheetId"]]["properties"]["gridProperties"][key] += action["length"]
        except Exception:
            self.sheets, self.rows, self.markers = state
            raise
        self.history.append(copy.deepcopy(batch))
        if self.after_apply:
            self.after_apply = False
            raise TimeoutError("Response lost after atomic commit")


class PublishedWeekSheetsTests(TestCase):
    setUp = fixtures.ConstructorPublicationTests.setUp
    tearDown = fixtures.ConstructorPublicationTests.tearDown
    draft = fixtures.ConstructorPublicationTests.draft
    lesson = fixtures.ConstructorPublicationTests.lesson
    publish = fixtures.ConstructorPublicationTests.publish
    week = "2026-10-19"

    def start(self):
        flag = mock.patch.dict(os.environ, {"MY_ISLAND_PUBLISHED_WEEK_SHEETS": "1"})
        flag.start()
        self.addCleanup(flag.stop)
        self.client = FakeSheets()
        template = self.draft()
        self.publish(template)
        return self.release("Released first", with_deferred=True)

    def release(self, title, with_deferred=False):
        lessons = [self.lesson(subject=title)]
        if with_deferred:
            lessons.append(self.lesson(id="held", subject="Deferred private", teacherId="missing", day=1))
        return self.publish(self.draft(lessons), "week", week_start=self.week)

    def tick(self, at=100):
        return delivery.deliver_one(self.database, lambda: self.client, now=at)

    def job(self, publication):
        with self.database.connection() as conn:
            return dict(self.database.execute(conn, """SELECT j.*,b.sheet_id FROM published_week_sheet_jobs j
                JOIN published_week_sheet_bindings b USING(target_id,week_start) WHERE publication_id=?""", (publication["publication_id"],)).fetchone())

    def test_lost_response_retry_and_republish_preserve_gid_user_tab_and_cells(self):
        first = self.start()
        self.assertTrue(first["sheet_delivery_queued"])
        self.client.after_apply = True
        self.assertTrue(self.tick())
        self.assertEqual(self.job(first)["state"], "retry")
        self.assertFalse(self.tick(104))
        self.assertTrue(self.tick(105))
        self.assertEqual(self.job(first)["state"], "delivered")
        self.assertEqual(len(self.client.history), 1)
        sid = self.job(first)["sheet_id"]
        self.assertIn("Released first", str(self.client.rows[sid]))
        self.assertNotIn("Deferred private", str(self.client.rows[sid]))
        self.client.rows[sid][0]["values"].extend([{}, {"userEnteredValue": {"stringValue": "User cell"}}])
        self.client.sheets[sid]["properties"]["gridProperties"]["columnCount"] += 2
        second = self.release("Released second")
        self.assertTrue(self.tick(120))
        self.assertEqual(self.job(second)["state"], "delivered")
        self.assertEqual(set(self.client.sheets), {7, sid})
        self.assertIn("Keep", str(self.client.rows[7]))
        self.assertIn("User cell", str(self.client.rows[sid]))
        self.assertIn("Released second", str(self.client.rows[sid]))
        self.assertNotIn("Released first", str(self.client.rows[sid]))
        self.assertEqual(list_publications(self.database)["items"][0]["sheet_delivery"]["delivered_revision"], 2)

    def test_delayed_stale_batch_cannot_replace_newer_remote_revision(self):
        self.start()
        self.tick()
        second = self.release("Second")
        job = self.job(second)
        delayed, _ = delivery._batch(job, delivery._plan(self.database, job), self.client.weekly_spreadsheet(job["target_id"]), self.client)
        third = self.release("Third")
        self.tick()
        self.assertEqual(self.job(second)["state"], "superseded")
        self.assertEqual(self.job(third)["state"], "delivered")
        with self.assertRaises(ValueError):
            self.client.batch_update_spreadsheet(job["target_id"], delayed)
        self.assertIn("Third", str(self.client.rows[job["sheet_id"]]))

    def test_transient_errors_back_off_and_foreign_or_user_edits_block(self):
        first = self.start()
        for status, at in ((429, 100), (503, 105)):
            self.client.fail_status = status
            self.tick(at)
            self.assertEqual(self.job(first)["state"], "retry")
        self.client.fail_status = None
        self.tick(115)
        self.assertEqual(self.job(first)["state"], "delivered")
        sid = self.job(first)["sheet_id"]
        second = self.release("Changed")
        self.client.rows[sid][0]["values"][0]["note"] = "User note"
        self.tick(200)
        self.assertEqual(self.job(second)["error_code"], "managed_cells_edited")
        self.assertEqual(self.client.rows[sid][0]["values"][0]["note"], "User note")
        third = self.release("New")
        self.client.markers.clear()
        self.tick(300)
        self.assertEqual(self.job(third)["error_code"], "ownership_mismatch")
        self.assertEqual(len(self.client.history), 1)

    def test_manual_format_edit_in_managed_cells_blocks_replacement(self):
        first = self.start()
        self.tick()
        sid = self.job(first)["sheet_id"]
        self.client.rows[sid][0]["values"][0]["userEnteredFormat"]["numberFormat"] = {"type": "NUMBER"}
        second = self.release("Should not overwrite")
        self.tick(110)
        self.assertEqual(self.job(second)["error_code"], "managed_cells_edited")
        self.assertEqual(len(self.client.history), 1)

    def test_manual_border_edit_after_delivery_blocks_replacement(self):
        first = self.start()
        self.tick()
        sid = self.job(first)["sheet_id"]
        self.client.rows[sid][0]["values"][0]["userEnteredFormat"]["borders"] = {"top": {"style": "DASHED"}}
        second = self.release("Should not overwrite")
        self.tick(110)
        self.assertEqual(self.job(second)["error_code"], "managed_cells_edited")
        self.assertEqual(len(self.client.history), 1)

    def test_legacy_marker_is_upgraded_only_when_current_layout_is_proven(self):
        first = self.start()
        self.tick()
        job = self.job(first)
        sid = job["sheet_id"]
        marker = self.client.markers[0]
        sha = delivery._contents(self.client.rows[sid])
        marker["name"] = marker["name"].split("_V2_")[0] + "_" + sha
        plan = delivery._plan(self.database, job)
        requests, signature = delivery._batch(job, plan, self.client.weekly_spreadsheet(job["target_id"]), self.client)
        self.assertIsNone(signature)
        self.assertEqual([next(iter(item)) for item in requests], ["deleteNamedRange", "addNamedRange"])
        marker["name"] = marker["name"].replace("_R1_", "_R0_")
        with self.assertRaisesRegex(delivery.DeliveryConflict, "unverified_legacy_layout"):
            delivery._batch(job, plan, self.client.weekly_spreadsheet(job["target_id"]), self.client)

    def test_color_style_readback_matches_rendered_color(self):
        old = {"backgroundColor": {"red": .2, "green": .3},
               "textFormat": {"foregroundColor": {}, "bold": False}}
        echoed = {"backgroundColorStyle": {"rgbColor": {"red": .200000004, "green": .3, "blue": 0}},
                  "textFormat": {"foregroundColorStyle": {"rgbColor": {"red": 0, "green": 0}}}}
        self.assertEqual(delivery._normalize_format(old), delivery._normalize_format(echoed))

    def test_restore_updates_the_same_weekly_gid_and_keeps_unrelated_tabs(self):
        first = self.start()
        self.tick()
        second = self.release("Second")
        self.tick(110)
        from backend.services.constructor_publication import preview_restore, restore_publication
        review = preview_restore(self.database, first["publication_id"], expected_head_revision=2)
        third = restore_publication(self.database, first["publication_id"], expected_head_revision=2,
                                    preview_token=review["preview_token"], confirmed=True)
        self.tick(120)
        self.assertEqual(self.job(third)["state"], "delivered")
        sid = self.job(first)["sheet_id"]
        self.assertEqual(self.job(second)["sheet_id"], sid)
        self.assertEqual(self.job(third)["sheet_id"], sid)
        self.assertEqual(set(self.client.sheets), {7, sid})
        self.assertIn("Released first", str(self.client.rows[sid]))

    def test_matching_title_keeps_history_and_foreign_gid_blocks(self):
        first = self.start()
        sid = self.job(first)["sheet_id"]
        historical = self.client.sheets[7]["properties"]
        historical["title"] = "Неделя " + self.week
        self.client.sheets[sid] = {"properties": {**historical, "sheetId": sid, "title": "Чужие данные"}}
        self.client.rows[sid] = [{"values": [{"userEnteredValue": {"stringValue": "Private template"}}]}]
        self.tick()
        self.assertEqual(self.job(first)["error_code"], "ownership_mismatch")
        self.assertEqual(self.client.history, [])
        del self.client.sheets[sid]
        del self.client.rows[sid]
        second = self.release("Safe new tab")
        self.tick(110)
        self.assertEqual(self.job(second)["state"], "delivered")
        self.assertEqual(historical["title"], "Неделя " + self.week)
        self.assertEqual(self.client.rows[7][0]["values"][0]["userEnteredValue"]["stringValue"], "Keep")
        self.assertNotEqual(self.client.sheets[sid]["properties"]["title"], historical["title"])

    def test_broken_publication_pointer_prevents_delivery(self):
        first = self.start()
        with self.database.connection() as conn:
            conn.execute("UPDATE schedule_publication_heads SET published_id='missing' WHERE scope_key=?", ("week:" + self.week,))
        self.tick()
        self.assertEqual(self.job(first)["error_code"], "published_head_mismatch")
        self.assertEqual(self.client.history, [])
    def test_smaller_week_clears_every_previous_managed_cell(self):
        self.start()
        wide = self.publish(self.draft([self.lesson(), self.lesson(id="extra", day=1, subject="Removed Tuesday")]),
                            "week", week_start=self.week)
        self.tick()
        sid = self.job(wide)["sheet_id"]
        self.assertIn("Removed Tuesday", str(self.client.rows[sid]))
        self.release("Only Monday")
        self.tick(120)
        self.assertNotIn("Removed Tuesday", str(self.client.rows[sid]))
        updates = [r["updateCells"] for r in self.client.history[-1] if "updateCells" in r]
        self.assertTrue(any(r.get("range") and not r.get("rows") for r in updates))

    def test_retry_endpoint_rechecks_school_admin_and_current_head(self):
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient
        from backend.handlers.constructor_publication_api import create_constructor_publication_router
        first = self.start()
        self.client.fail_status = 503
        self.tick()
        def school_admin(value):
            if value != "synthetic-school-admin":
                raise HTTPException(403)
            return {"id": "test-admin"}
        app = FastAPI()
        app.include_router(create_constructor_publication_router(lambda: (self.database, self.database), school_admin))
        http = TestClient(app)
        path = f"/api/admin/schedule/constructor/publication/{first['publication_id']}/sheet-retry"
        headers = {"X-School-Admin-Session": "synthetic-school-admin"}
        with mock.patch.dict(os.environ, {"MY_ISLAND_CONSOLIDATED_SCHEDULE": "1"}):
            self.assertEqual(http.post(path).status_code, 403)
            self.assertEqual(http.post(path, headers=headers).json(), {"state":"pending"})
        self.release("Superseding edition")
        with mock.patch.dict(os.environ, {"MY_ISLAND_CONSOLIDATED_SCHEDULE": "1"}):
            self.assertEqual(http.post(path, headers=headers).status_code, 409)
