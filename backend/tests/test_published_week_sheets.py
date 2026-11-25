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
        delayed = delivery._batch(job, delivery._plan(self.database, job), self.client.weekly_spreadsheet(job["target_id"]), self.client)
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
