from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact, materialize_effective_week
from backend.services.canonical_weekly_refresh import _persist_source_snapshot, preview_current_week, refresh_current_week
from backend.services.schedule_parser_v2 import group_schedule_rows
from backend.services.weekly_schedule_ingestion import (
    WeeklyIngestionError, build_weekly_diff, discover_weekly_tab, layout_profile,
    parse_structure, parse_weekly_lessons,
)


def sheet(title: str, sheet_id: int) -> dict:
    return {"properties": {"title": title, "sheetId": sheet_id}}


def matrix(*activities: str) -> list[list[object]]:
    return [
        ["", "5", "6", "7"],
        ["", "Пн", "Пн", "Пн"],
        ["09:00-09:45", *activities],
    ]


def parsed(activity: str, *, weekday: int = 0, start: str = "09:00", column: int = 1, audience: str = "5") -> dict:
    return {
        (weekday, start, "09:45", audience): [{
            "source_cell": f"B{weekday + 3}", "source_column": column, "source_row": weekday + 2,
            "raw_text": activity, "audience": audience, "merged_audiences": [], "merge_data": None,
        }]
    }


def block(key: str, activity: str, group_id: str, *, weekday: int = 0, start: str = "09:00") -> dict:
    return {"block_key": key, "weekday": weekday, "slot": {"start": start, "end": "09:45"}, "grade_scope": "5", "derived_from": {"source_cells": ["B3"]}, "assignments": [{"activity": activity, "canonical_group_ids": [group_id], "teacher_ids": []}]}


def grid_rows(classes: tuple[str, str, str] = ("5", "6", "7")) -> list[dict]:
    rows = [
        ["", *classes],
        ["", "Пн", "Пн", "Пн"],
        ["09:00-09:45", "Классный час", "", "Физика"],
    ]
    return [{"values": [{"formattedValue": value} if value else {} for value in row]} for row in rows]


class FakeGoogleClient:
    def __init__(self, *, weekly_classes: tuple[str, str, str] = ("5", "6", "7")):
        self.weekly_classes = weekly_classes

    def spreadsheet(self, _spreadsheet_id: str) -> dict:
        return {
            "properties": {"title": "Расписание"},
            "sheets": [sheet("2026/27 шаблон", 1), sheet("14–18 сентября", 2), sheet("21–25 сентября", 3)],
        }

    def sheet_grid_range(self, _spreadsheet_id: str, title: str, _range_name: str) -> dict:
        classes = self.weekly_classes if title == "21–25 сентября" else ("5", "6", "7")
        return {"sheets": [{
            "data": [{"rowData": grid_rows(classes)}],
            "merges": [{"startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 1, "endColumnIndex": 3}],
        }]}


def table_counts(database: Database) -> dict[str, int]:
    tables = (
        "school_sync_runs", "school_source_snapshots", "school_source_records",
        "canonical_effective_weeks", "canonical_effective_blocks",
    )
    with database.connection() as connection:
        return {
            table: int(database.execute(connection, f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
            for table in tables
        }


class WeeklyTabDiscoveryTests(unittest.TestCase):
    def test_selects_week_containing_target_date(self):
        chosen, candidates = discover_weekly_tab(
            [sheet("2026/27 шаблон", 1), sheet("14–18 сентября", 2), sheet("21-25 сентября", 3)],
            target_date=date(2026, 9, 22),
        )
        self.assertEqual(chosen.title, "21-25 сентября")
        self.assertEqual(chosen.week_start, "2026-09-21")
        self.assertEqual(len(candidates), 2)

    def test_explicit_week_selects_requested_tab(self):
        chosen, _ = discover_weekly_tab(
            [sheet("14–18 сентября", 2), sheet("21–25 сентября", 3)], explicit_week_start="2026-09-14",
        )
        self.assertEqual(chosen.sheet_id, "2")

    def test_ambiguous_and_malformed_tabs_fail_closed(self):
        with self.assertRaisesRegex(WeeklyIngestionError, "ambiguous"):
            discover_weekly_tab([sheet("14–18 сентября", 2), sheet("14-18 сентября", 3)], explicit_week_start="2026-09-14")
        with self.assertRaisesRegex(WeeklyIngestionError, "malformed"):
            discover_weekly_tab([sheet("14–99 сентября", 2)], explicit_week_start="2026-09-14")


class MergeAwareParsingTests(unittest.TestCase):
    def test_two_audience_merge_is_one_source_item(self):
        rows = parse_structure(matrix("Русский", "", "Физика"), sheet_id="1", title="21–25 сентября", week_start="2026-09-21", merges=[{"startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 1, "endColumnIndex": 3}])
        merged = rows[(0, "09:00", "09:45", "5,6")]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["merged_audiences"], ["5", "6"])
        self.assertEqual(merged[0]["merge_data"]["endColumnIndex"], 3)

    def test_three_audience_merge_is_one_source_item(self):
        rows = parse_structure(matrix("Классный час", "", ""), sheet_id="1", title="21–25 сентября", week_start="2026-09-21", merges=[{"startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 1, "endColumnIndex": 4}])
        self.assertEqual(len(rows[(0, "09:00", "09:45", "5,6,7")]), 1)
        semantic_rows = group_schedule_rows(parse_weekly_lessons(rows, []))
        self.assertEqual(len(semantic_rows), 1)
        self.assertEqual(semantic_rows[0].grade_scope, "5,6,7")

    def test_merge_and_parallel_lesson_remain_independent(self):
        rows = parse_structure(matrix("Русский", "", "Физика"), sheet_id="1", title="21–25 сентября", week_start="2026-09-21", merges=[{"startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 1, "endColumnIndex": 3}])
        self.assertEqual(rows[(0, "09:00", "09:45", "5,6")][0]["raw_text"], "Русский")
        self.assertEqual(rows[(0, "09:00", "09:45", "7")][0]["raw_text"], "Физика")

    def test_layout_drift_is_controlled(self):
        with self.assertRaisesRegex(WeeklyIngestionError, "class-header"):
            layout_profile([["", "предмет"], ["09:00-09:45", "Русский"]])


class SharedDiffTests(unittest.TestCase):
    def _diff(self, template_activity: str, weekly_activity: str | None, *, base_group: str = "g1", weekly_group: str = "g1", weekly_day: int = 0, weekly_start: str = "09:00", template_meta=None, weekly_meta=None):
        canonical_block = block("base", template_activity, base_group)
        canonical = {"blocks": {"base": canonical_block}}
        template = parsed(template_activity)
        weekly = {} if weekly_activity is None else parsed(weekly_activity, weekday=weekly_day, start=weekly_start)
        weekly_block = block("weekly", weekly_activity or "", weekly_group, weekday=weekly_day, start=weekly_start)
        artifact = {"blocks": {} if weekly_activity is None else {"weekly": weekly_block}}
        return build_weekly_diff(canonical, template, weekly, artifact, template_meta, weekly_meta)

    def test_cancelled_and_replaced(self):
        self.assertEqual(self._diff("Русский", None)["counts"], {"CANCELLED": 1})
        self.assertEqual(self._diff("Русский", "Физика")["counts"], {"REPLACED": 1})

    def test_metadata_only(self):
        result = self._diff("Русский каб.1", "Русский каб.2", template_meta={"B3": {"note": "a"}}, weekly_meta={"B3": {"note": "b"}})
        self.assertEqual(result["counts"], {"METADATA_ONLY": 1})

    def test_semantic_audience_change(self):
        result = self._diff("Русский", "Русский новая группа", weekly_group="g2")
        self.assertEqual(result["counts"], {"SEMANTIC_AUDIENCE_CHANGE": 1})

    def test_unique_cancelled_and_added_pair_is_moved(self):
        result = self._diff("Русский", "Русский", weekly_day=1, weekly_start="10:00")
        self.assertEqual(result["counts"], {"MOVED": 1})
        self.assertEqual(result["patches"][0]["moved_to"], (1, "10:00", "09:45", "5"))

    def test_unmatched_weekly_block_is_added(self):
        weekly = parsed("Физика")
        artifact = {"blocks": {"weekly": block("weekly", "Физика", "g1")}}
        result = build_weekly_diff({"blocks": {}}, {}, weekly, artifact)
        self.assertEqual(result["counts"], {"ADDED": 1})


class WeeklySnapshotPersistenceTests(unittest.TestCase):
    def test_snapshot_is_idempotent_and_linked_to_effective_week(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "weekly.db")
            database.initialize()
            raw = {"sheet_title": "21–25 сентября", "values": matrix("Русский", "", "")}
            structural = {"layout": {"class_header_row": 0}, "rows": {"0|09:00|09:45|5": []}}
            kwargs = dict(database=database, spreadsheet_id="sheet", spreadsheet_title="Расписание", weekly_tab={"sheet_id": "22", "title": "21–25 сентября"}, week_start="2026-09-21", week_end="2026-09-25", fingerprint="fp-1", raw_payload=raw, structural_payload=structural)
            first = _persist_source_snapshot(**kwargs)
            second = _persist_source_snapshot(**kwargs)
            self.assertFalse(first["idempotent"])
            self.assertTrue(second["idempotent"])
            self.assertEqual(first["id"], second["id"])
            artifact = {"schema_version": "canonical-schedule-bootstrap-v1", "version_id": "v2", "status": "approved_with_exceptions", "source_snapshot": {"id": "canonical", "fingerprint": "base"}, "blocks": {}}
            import_canonical_artifact(database, artifact)
            effective = materialize_effective_week(database, "v2", "2026-09-21", overlay_source_snapshot_id=first["id"], overlay_fingerprint="fp-1")
            with database.connection() as connection:
                row = database.execute(connection, "SELECT overlay_source_snapshot_id FROM canonical_effective_weeks WHERE effective_week_id=?", (effective["effective_week_id"],)).fetchone()
                snapshot = database.execute(connection, "SELECT raw_payload,structural_payload FROM school_source_snapshots WHERE id=?", (first["id"],)).fetchone()
            self.assertEqual(str(row["overlay_source_snapshot_id"]), first["id"])
            self.assertIsNotNone(snapshot["raw_payload"])
            self.assertIsNotNone(snapshot["structural_payload"])


class WeeklyRefreshPreviewTests(unittest.TestCase):
    def _database(self, directory: str) -> Database:
        database = Database(Path(directory) / "preview.db")
        database.initialize()
        import_canonical_artifact(database, {
            "schema_version": "canonical-schedule-bootstrap-v1",
            "version_id": "v2-preview",
            "status": "approved_with_exceptions",
            "source_snapshot": {"id": "canonical", "fingerprint": "base"},
            "blocks": {},
        })
        return database

    def _settings(self) -> SimpleNamespace:
        return SimpleNamespace(google_sheets_spreadsheet_id="sheet")

    def test_preview_is_read_only_and_matches_apply_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            before = table_counts(database)
            with patch("backend.services.canonical_weekly_refresh._client", return_value=(FakeGoogleClient(), "operator@example.com")):
                preview = preview_current_week(database, self._settings(), "2026-09-21")
                after_preview = table_counts(database)
                applied = refresh_current_week(database, self._settings(), "2026-09-21")

            self.assertEqual(preview["status"], "preview")
            self.assertEqual(preview["writes_performed"], 0)
            self.assertEqual(before, after_preview)
            self.assertEqual(preview["selected_tab"]["title"], "21–25 сентября")
            self.assertEqual(preview["week_start"], "2026-09-21")
            self.assertEqual(preview["week_end"], "2026-09-25")
            self.assertEqual(preview["merge_count"], 1)
            self.assertEqual(preview["source_fingerprint"], applied["refresh"]["source_fingerprint"])
            self.assertEqual(preview["diff_counts"], applied["refresh"]["diff_counts"])
            self.assertEqual(preview["overlay_patch_count"], applied["refresh"]["patch_count"])
            self.assertEqual(preview["effective_block_count"], applied["refresh"]["effective_block_count"])

    def test_layout_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            before = table_counts(database)
            client = FakeGoogleClient(weekly_classes=("5", "6", "8"))
            with patch("backend.services.canonical_weekly_refresh._client", return_value=(client, "operator@example.com")):
                with self.assertRaisesRegex(WeeklyIngestionError, "layout drift"):
                    preview_current_week(database, self._settings(), "2026-09-21")
            self.assertEqual(before, table_counts(database))


if __name__ == "__main__":
    unittest.main()
