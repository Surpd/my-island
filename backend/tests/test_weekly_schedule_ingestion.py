from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact, materialize_effective_week, read_canonical_template
from backend.services.canonical_weekly_refresh import _grid_tab, _persist_source_snapshot, _weekly_comparison_baseline, confirm_template_snapshot, preview_template_snapshot, preview_current_week, refresh_current_week
from backend.services.schedule_drafts import merge_import_changes, preview_changes, publish_draft, get_draft, save_draft
from backend.services.schedule_parser_v2 import group_schedule_rows
from backend.services.weekly_schedule_ingestion import (
    WeeklyIngestionError, build_weekly_diff, discover_weekly_tab, layout_profile,
    parse_structure, parse_weekly_lessons, source_change_keys,
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
            "source_cell": f"{chr(ord('A') + column)}{weekday + 3}", "source_column": column, "source_row": weekday + 2,
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

    def test_explicit_week_selects_numeric_cross_month_tab(self):
        chosen, candidates = discover_weekly_tab(
            [sheet("28.09-02.10", 4), sheet("Архив14-18.09", 5)],
            explicit_week_start="2026-09-28",
        )
        self.assertEqual(chosen.title, "28.09-02.10")
        self.assertEqual((chosen.week_start, chosen.week_end), ("2026-09-28", "2026-10-02"))
        self.assertEqual(len(candidates), 1)

    def test_ambiguous_and_malformed_tabs_fail_closed(self):
        with self.assertRaisesRegex(WeeklyIngestionError, "ambiguous"):
            discover_weekly_tab([sheet("14–18 сентября", 2), sheet("14-18 сентября", 3)], explicit_week_start="2026-09-14")
        with self.assertRaisesRegex(WeeklyIngestionError, "malformed"):
            discover_weekly_tab([sheet("14–99 сентября", 2)], explicit_week_start="2026-09-14")


class MergeAwareParsingTests(unittest.TestCase):
    def test_short_literature_subject_becomes_canonical_name_and_room_is_separate(self):
        rows = parsed("Литер Юля кабинет 10")
        rows[(0, "09:00", "09:45", "5")][0]["sheet_id"] = "sheet"
        semantic = parse_weekly_lessons(rows, [{"id": "teacher-yulia", "display_name": "Юля"}])
        self.assertEqual(semantic[0]["subject"], "Литература")
        self.assertEqual(semantic[0]["resolved_identity_ids"], ["teacher-yulia"])
        self.assertEqual(semantic[0]["room"].casefold(), "кабинет 10")

    def test_punctuated_english_group_keeps_subject_and_teacher_separate(self):
        rows = parsed("Англ. 3 Игорь")
        rows[(0, "09:00", "09:45", "5")][0]["sheet_id"] = "sheet"
        semantic = parse_weekly_lessons(rows, [{"id": "teacher-igor", "display_name": "Игорь"}])
        self.assertEqual(semantic[0]["subject"], "Английский")
        self.assertEqual(semantic[0]["resolved_identity_ids"], ["teacher-igor"])

    def test_subject_abbreviation_dictionary_normalizes_new_cells_and_keeps_teacher_separate(self):
        cases = [
            ("матем с практикум ДФ каб.17", "Математика", "teacher-dmitry"),
            ("русский язык ЕВ каб.5", "Русский язык", "teacher-elena"),
            ("истор. Анна", "История", "teacher-anna"),
            ("лит. Юля", "Литература", "teacher-yulia"),
            ("био Иван", "Биология", "teacher-ivan"),
            ("инфор Тарас", "Информатика", "teacher-taras"),
        ]
        teachers = [
            {"id": "teacher-dmitry", "display_name": "Дмитрий Филиппов"},
            {"id": "teacher-elena", "display_name": "Елена Викторовна"},
            {"id": "teacher-anna", "display_name": "Анна Владимировна"},
            {"id": "teacher-yulia", "display_name": "Юля"},
            {"id": "teacher-ivan", "display_name": "Иван"},
            {"id": "teacher-taras", "display_name": "Тарас"},
        ]
        for raw, subject, teacher_id in cases:
            with self.subTest(raw=raw):
                rows = parsed(raw)
                rows[(0, "09:00", "09:45", "5")][0]["sheet_id"] = "sheet"
                semantic = parse_weekly_lessons(rows, teachers)
                self.assertEqual(semantic[0]["subject"], subject)
                self.assertEqual(semantic[0]["resolved_identity_ids"], [teacher_id])
                self.assertEqual(semantic[0]["teacher_hint"], next(item["display_name"] for item in teachers if item["id"] == teacher_id))

    def test_long_subject_name_is_not_collapsed_to_its_prefix(self):
        rows = parsed("История искусства Анна")
        rows[(0, "09:00", "09:45", "5")][0]["sheet_id"] = "sheet"
        semantic = parse_weekly_lessons(rows, [{"id": "teacher-anna", "display_name": "Анна Владимировна"}])
        self.assertEqual(semantic[0]["subject"], "История искусства")

    def test_unlisted_multiword_activity_keeps_its_title_without_teacher_or_room(self):
        rows = parsed("Лаборатория Геометрия живого Родион каб.6")
        rows[(0, "09:00", "09:45", "5")][0]["sheet_id"] = "sheet"
        semantic = parse_weekly_lessons(rows, [{"id": "teacher-rodion", "display_name": "Родион"}])
        self.assertEqual(semantic[0]["subject"], "Лаборатория Геометрия живого")
        self.assertEqual(semantic[0]["resolved_identity_ids"], ["teacher-rodion"])
        self.assertEqual(semantic[0]["room"].casefold(), "каб.6")

    def test_simple_activity_uses_canonical_title_without_appended_teacher(self):
        rows = parsed("Курс по выбору Алексей каб.5")
        rows[(0, "09:00", "09:45", "5")][0]["sheet_id"] = "sheet"
        semantic = parse_weekly_lessons(rows, [{"id": "teacher-alexey", "display_name": "Алексей"}])
        self.assertEqual(semantic[0]["subject"], "Курс по выбору")
        self.assertEqual(semantic[0]["resolved_identity_ids"], ["teacher-alexey"])

    def test_no_lesson_never_resolves_or_requires_a_teacher(self):
        rows = parsed("Нет урока Анна")
        rows[(0, "09:00", "09:45", "5")][0]["sheet_id"] = "sheet"
        semantic = parse_weekly_lessons(rows, [{"id": "teacher-anna", "display_name": "Анна Владимировна"}])
        self.assertEqual(semantic[0]["activity_type"], "nonlesson")
        self.assertEqual(semantic[0]["teacher_hint"], "")
        self.assertEqual(semantic[0]["resolved_identity_ids"], [])
        self.assertEqual(semantic[0]["parse_status"], "validated")

    def test_subject_is_separated_from_hall_and_teacher_text(self):
        rows = parsed("Пластика в один зал Вадим")
        rows[(0, "09:00", "09:45", "5")][0]["sheet_id"] = "sheet"
        semantic = parse_weekly_lessons(rows, [{"id": "teacher-vadim", "display_name": "Вадим"}])
        self.assertEqual(semantic[0]["subject"], "Пластика")
        self.assertEqual(semantic[0]["resolved_identity_ids"], ["teacher-vadim"])
        self.assertIn("зал", semantic[0]["room"].lower())

    def test_english_club_is_excluded_even_without_circle_marker(self):
        rows = parsed("Английский клуб 5-7 кл")
        rows[(0, "09:00", "09:45", "5")][0]["sheet_id"] = "sheet"
        semantic = parse_weekly_lessons(rows, [])
        self.assertEqual(len(semantic), 1)
        self.assertEqual(semantic[0]["activity_type"], "extracurricular")

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

    def test_one_changed_weekly_block_creates_only_one_override(self):
        second = {**block("second", "Математика", "g2"), "grade_scope": "6"}
        canonical = {"blocks": {"base": block("base", "Русский", "g1"), "second": second}}
        template = {**parsed("Русский"), **parsed("Математика", column=2, audience="6")}
        weekly = {**parsed("Русский"), **parsed("Физика", column=2, audience="6")}
        replacement = {**block("replacement", "Физика", "g2"), "grade_scope": "6"}
        diff = build_weekly_diff(canonical, template, weekly, {"blocks": {"replacement": replacement}})
        self.assertEqual(diff["counts"], {"UNCHANGED": 1, "REPLACED": 1})
        self.assertEqual([item["block_key"] for item in diff["patches"]], ["second"])

    def test_metadata_only(self):
        result = self._diff("Русский каб.1", "Русский каб.2", template_meta={"B3": {"note": "a"}}, weekly_meta={"B3": {"note": "b"}})
        self.assertEqual(result["counts"], {"METADATA_ONLY": 1})

    def test_room_only_change_is_ignored(self):
        result = self._diff("Русский каб.1", "Русский каб.2")
        self.assertEqual(result["counts"], {"UNCHANGED": 1})
        self.assertEqual(result["patches"], [])

    def test_resolved_subject_alias_reuses_confirmed_assignment_despite_membership_gap(self):
        base = block("base", "Математика", "g1")
        weekly_assignment = block("weekly", "матем", "g1")["assignments"][0]
        for assignment in (base["assignments"][0], weekly_assignment):
            assignment["audience"] = {"type": "canonical_groups", "canonical_group_ids": ["g1"]}
            assignment.pop("canonical_group_ids", None)
        canonical = {"blocks": {"base": base}}
        template = parsed("Математика")
        weekly = parsed("Матем")
        weekly_block = {
            **block("weekly", "матем", "g1"),
            "assignments": [weekly_assignment],
            "status": "unresolved",
            "unresolved": [{"reason": "student is missing from the canonical base-class membership"}],
        }

        result = build_weekly_diff(
            canonical, template, weekly, {"blocks": {"weekly": weekly_block}},
            confirmed_mapping={"0|09:00|09:45|5": "base"},
        )

        self.assertEqual(result["counts"], {"UNCHANGED": 1})
        self.assertEqual(result["patches"], [])

    def test_no_lesson_spellings_do_not_trigger_roster_resolution_changes(self):
        canonical = {"blocks": {"base": block("base", "NO_LESSON", "g1")}}
        template = parsed("Нет урока")
        weekly = parsed("Свободны")

        result = build_weekly_diff(
            canonical, template, weekly, {"blocks": {}},
            confirmed_mapping={"0|09:00|09:45|5": "base"},
        )

        self.assertEqual(result["counts"], {"UNCHANGED": 1})
        self.assertEqual(result["patches"], [])

    def test_unresolved_real_replacement_shows_source_instead_of_copying_old_assignment(self):
        canonical = {"blocks": {"base": block("base", "Русский", "g1")}}
        weekly_block = {
            **block("weekly", "Физика", "g1"),
            "status": "unresolved",
            "unresolved": [{"reason": "Teacher is unresolved"}],
        }
        result = build_weekly_diff(
            canonical, parsed("Русский"), parsed("Физика"), {"blocks": {"weekly": weekly_block}},
            confirmed_mapping={"0|09:00|09:45|5": "base"},
        )

        patch = result["patches"][0]
        self.assertEqual(patch["resolution_state"], "UNRESOLVED")
        self.assertEqual(patch["assignments"], [])
        self.assertEqual(patch["after"]["assignments"], [])
        self.assertEqual(patch["after"]["source_text"], ["физика"])

    def test_unchanged_week_has_no_overrides(self):
        result = self._diff("Русский", "Русский")
        self.assertEqual(result["patches"], [])

    def test_effective_week_is_the_comparison_baseline_not_the_template(self):
        # The confirmed template says Russian, while the effective week already
        # contains Physics. Re-reading Physics from the sheet is a no-op.
        effective = {"blocks": {"base": block("base", "Физика", "g1")}}
        result = build_weekly_diff(
            effective,
            parsed("Русский"),
            parsed("Физика"),
            {"blocks": {"weekly": block("weekly", "Физика", "g1")}},
            confirmed_mapping={"0|09:00|09:45|5": "base"},
            baseline_is_effective=True,
        )
        self.assertEqual(result["counts"], {"UNCHANGED": 1})
        self.assertEqual(result["patches"], [])

    def test_effective_week_change_is_proposed_even_when_sheet_matches_template(self):
        effective = {"blocks": {"base": block("base", "Физика", "g1")}}
        result = build_weekly_diff(
            effective,
            parsed("Русский"),
            parsed("Русский"),
            {"blocks": {"weekly": block("weekly", "Русский", "g1")}},
            confirmed_mapping={"0|09:00|09:45|5": "base"},
            baseline_is_effective=True,
        )
        self.assertEqual(result["counts"], {"REPLACED": 1})
        self.assertEqual(result["patches"][0]["before"]["assignments"][0]["activity"], "Физика")
        self.assertEqual(result["patches"][0]["assignments"][0]["activity"], "Русский")

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

    def test_confirmed_source_mapping_keeps_manual_time_correction(self):
        canonical = {"blocks": {"manual": block("manual", "Исправленный урок", "g1", start="09:55")}}
        template = parsed("Исходный текст")
        same_week = parsed("Исходный текст")
        mapping = {"0|09:00|09:45|5": "manual"}
        unchanged = build_weekly_diff(canonical, template, same_week, {"blocks": {}}, confirmed_mapping=mapping)
        self.assertEqual(unchanged["patches"], [])
        changed_week = parsed("Другой урок")
        changed = build_weekly_diff(canonical, template, changed_week,
            {"blocks": {"changed": block("changed", "Другой урок", "g1")}}, confirmed_mapping=mapping)
        self.assertEqual(len(changed["patches"]), 1)
        self.assertEqual(changed["patches"][0]["block_key"], "manual")


class WeeklySnapshotPersistenceTests(unittest.TestCase):
    def test_comparison_uses_only_the_exact_persisted_week(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "baseline.db")
            database.initialize()
            import_canonical_artifact(database, {
                "schema_version": "canonical-schedule-bootstrap-v1",
                "version_id": "baseline-v1",
                "source_snapshot": {"id": "canonical", "fingerprint": "base"},
                "blocks": {"base": block("base", "Русский", "g1")},
            })
            template = {"blocks": {"base": block("base", "Русский", "g1")}}
            materialize_effective_week(database, "baseline-v1", "2026-09-21", patches=[{
                "block_key": "base", "change_kind": "replaced",
                "assignments": [{"activity": "Физика", "canonical_group_ids": ["g1"], "teacher_ids": []}],
            }])

            baseline, effective, _ = _weekly_comparison_baseline(database, template, "baseline-v1", "2026-09-28")
            self.assertIsNone(effective)
            self.assertEqual(baseline["blocks"]["base"]["assignments"][0]["activity"], "Русский")

            materialize_effective_week(database, "baseline-v1", "2026-09-28", patches=[{
                "block_key": "base", "change_kind": "replaced",
                "assignments": [{"activity": "Физика", "canonical_group_ids": ["g1"], "teacher_ids": []}],
            }])
            baseline, effective, preserved = _weekly_comparison_baseline(database, template, "baseline-v1", "2026-09-28")
            self.assertIsNotNone(effective)
            self.assertEqual(baseline["blocks"]["base"]["assignments"][0]["activity"], "Физика")
            self.assertEqual(preserved["base"]["assignments"][0]["activity"], "Физика")
            save_draft(database, "week", "2026-09-28", expected_revision=0,
                base_version_id="baseline-v1", payload={"changes": [{
                    "block_key": "base", "operation": "upsert", "assignment_index": 0,
                    "lesson": {"activity": "Математика", "audience": {"kind": "groups", "group_ids": ["g1"]},
                               "teacher_ids": [], "weekday": 0, "start_time": "09:00", "end_time": "09:45", "grade": "5"},
                }]})
            draft_baseline, _, _ = _weekly_comparison_baseline(database, template, "baseline-v1", "2026-09-28")
            self.assertEqual(draft_baseline["blocks"]["base"]["assignments"][0]["activity"], "Математика")

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

    def test_weekly_diff_carries_slot_context_and_resolver_reason(self):
        key = (2, "11:50", "12:35", "8")
        base = {
            "block_key": "canonical-8-wed-3", "weekday": key[0],
            "slot": {"start": key[1], "end": key[2]}, "grade_scope": key[3],
            "assignments": [{"activity": "Русский", "canonical_group_ids": ["g8"], "teacher_ids": ["t1"]}],
        }
        old_rows = {key: [{"source_cell": "D8", "source_column": 3, "raw_text": "Русский ЕВ", "audience": "8", "merged_audiences": []}]}
        new_rows = {key: [{"source_cell": "D8", "source_column": 3, "raw_text": "Русский неизвестный", "audience": "8", "merged_audiences": [], "lesson_date": "2026-09-23"}]}
        weekly_block = {**base, "status": "unresolved", "unresolved": [{"reason": "Teacher is unresolved"}], "assignments": []}

        result = build_weekly_diff(
            {"blocks": {base["block_key"]: base}}, old_rows, new_rows,
            {"blocks": {"weekly": weekly_block}},
        )

        patch = result["patches"][0]
        self.assertEqual(patch["weekday"], 2)
        self.assertEqual(patch["slot"], {"start": "11:50", "end": "12:35"})
        self.assertEqual(patch["grade_scope"], "8")
        self.assertEqual(patch["lesson_date"], "2026-09-23")
        self.assertEqual(patch["weekly_source_cells"], ["D8"])
        self.assertEqual(patch["resolution_details"], ["Teacher is unresolved"])
        self.assertEqual(patch["assignments"], [])

    def test_preview_is_read_only_and_matches_apply_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            client = FakeGoogleClient()
            confirm_template_snapshot(database, spreadsheet_id="sheet", spreadsheet_title="Расписание",
                template_tab=_grid_tab(client, "sheet", "2026/27 шаблон", "1"), expected_version_id="v2-preview")
            before = table_counts(database)
            with patch("backend.services.canonical_weekly_refresh._client", return_value=(client, "operator@example.com")):
                with patch("backend.services.canonical_weekly_refresh.build_bootstrap_canonical", side_effect=AssertionError("unchanged source was re-resolved")):
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

    def test_refresh_leaves_unresolved_weekly_change_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            client = FakeGoogleClient()
            confirm_template_snapshot(database, spreadsheet_id="sheet", spreadsheet_title="Расписание",
                template_tab=_grid_tab(client, "sheet", "2026/27 шаблон", "1"), expected_version_id="v2-preview")
            original = client.sheet_grid_range
            def changed_grid(spreadsheet_id: str, title: str, range_name: str) -> dict:
                result = original(spreadsheet_id, title, range_name)
                if title == "21–25 сентября":
                    result["sheets"][0]["data"][0]["rowData"][2]["values"][1]["formattedValue"] = "Английский группа 999"
                return result
            client.sheet_grid_range = changed_grid
            with patch("backend.services.canonical_weekly_refresh._client", return_value=(client, "operator@example.com")):
                preview = preview_current_week(database, self._settings(), "2026-09-21")
                applied = refresh_current_week(database, self._settings(), "2026-09-21")
            self.assertGreater(preview["overlay_patch_count"], 0)
            self.assertEqual(applied["refresh"]["applied_patch_count"], 0)
            self.assertEqual(applied["refresh"]["pending_review_count"], preview["overlay_patch_count"])

    def test_confirmed_template_survives_row_insertion_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            tab = {"sheet_id": "1", "title": "2026/27 шаблон", "values": matrix("Русский", "Математика", ""), "merges": [], "structured_cells": []}
            first = confirm_template_snapshot(database, spreadsheet_id="sheet", spreadsheet_title="Расписание",
                template_tab=tab, expected_version_id="v2-preview")
            again = confirm_template_snapshot(database, spreadsheet_id="sheet", spreadsheet_title="Расписание",
                template_tab=tab, expected_version_id="v2-preview")
            shifted = {**tab, "values": [*tab["values"][:2], ["", "", "", ""], *tab["values"][2:]]}
            preview = preview_template_snapshot(database, spreadsheet_id="sheet", template_tab=shifted,
                expected_version_id="v2-preview")
            self.assertEqual(first["source_snapshot"]["id"], again["source_snapshot"]["id"])
            self.assertTrue(again["source_snapshot"]["idempotent"])
            self.assertEqual(preview["changed_source_blocks"], [])
            self.assertEqual(preview["overlay_patch_count"], 0)

    def test_reordered_source_rows_do_not_change_logical_blocks(self):
        original = [*matrix("Русский", "", ""), ["09:55-10:40", "Математика", "", ""]]
        reordered = [*original[:2], original[3], original[2]]
        first = parse_structure(original, sheet_id="1", title="template", week_start="2026-09-21", merges=[])
        second = parse_structure(reordered, sheet_id="1", title="template", week_start="2026-09-21", merges=[])
        self.assertEqual(source_change_keys(first, second), set())

    def test_equal_text_in_different_slots_keeps_distinct_identity(self):
        original = [*matrix("Русский", "", ""), ["09:55-10:40", "Русский", "", ""]]
        changed = [*original[:3], ["09:55-10:40", "Математика", "", ""]]
        first = parse_structure(original, sheet_id="1", title="template", week_start="2026-09-21", merges=[])
        second = parse_structure(changed, sheet_id="1", title="template", week_start="2026-09-21", merges=[])
        self.assertEqual(source_change_keys(first, second), {(0, "09:55", "10:40", "5")})

    def test_confirmation_maps_manual_time_correction_by_source_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manual.db")
            database.initialize()
            import_canonical_artifact(database, {
                "schema_version": "canonical-schedule-bootstrap-v1",
                "version_id": "manual-v1", "status": "approved_with_exceptions",
                "source_snapshot": {"id": "canonical", "fingerprint": "base"},
                "blocks": {"manual": block("manual", "Русский", "g1", start="09:55")},
            })
            tab = {"sheet_id": "1", "title": "2026/27 шаблон",
                   "values": matrix("Русский", "", ""), "merges": [], "structured_cells": []}
            confirmation = confirm_template_snapshot(database, spreadsheet_id="sheet",
                spreadsheet_title="Расписание", template_tab=tab, expected_version_id="manual-v1")
            preview = preview_template_snapshot(database, spreadsheet_id="sheet",
                template_tab=tab, expected_version_id="manual-v1")
            self.assertEqual(confirmation["mapped_block_count"], 1)
            self.assertEqual(preview["overlay_patch_count"], 0)

    def test_one_template_source_change_only_re_resolves_one_block(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            tab = {"sheet_id": "1", "title": "2026/27 шаблон", "values": matrix("Русский", "Математика", ""), "merges": [], "structured_cells": []}
            confirm_template_snapshot(database, spreadsheet_id="sheet", spreadsheet_title="Расписание",
                template_tab=tab, expected_version_id="v2-preview")
            changed = {**tab, "values": matrix("Русский", "Физика", "")}
            preview = preview_template_snapshot(database, spreadsheet_id="sheet", template_tab=changed,
                expected_version_id="v2-preview")
            self.assertEqual(preview["changed_source_blocks"], ["0|09:00|09:45|6"])
            self.assertEqual(preview["overlay_patch_count"], 1)
            self.assertEqual(preview["warnings"][0]["block_key"], "source|0|09:00|09:45|6")

    def test_clear_template_change_publish_and_rebaseline(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "flow.db")
            database.initialize()
            student = database.create_identity("student", "One", "5A")
            group = database.create_group("5A", "class")
            database.create_membership(group["id"], student["id"], "student", "test")
            import_canonical_artifact(database, {"schema_version": "test-v1", "version_id": "base-v1",
                "source_snapshot": {"id": "base", "fingerprint": "base"},
                "blocks": {"base": block("base", "Русский", str(group["id"]))}}, status="approved_baseline")
            tab = {"sheet_id": "1", "title": "2026/27 шаблон", "values": matrix("Русский", "", ""), "merges": [], "structured_cells": []}
            confirm_template_snapshot(database, spreadsheet_id="sheet", spreadsheet_title="Расписание", template_tab=tab, expected_version_id="base-v1")
            changed = {**tab, "values": matrix("Математика", "", "")}
            preview = preview_template_snapshot(database, spreadsheet_id="sheet", template_tab=changed, expected_version_id="base-v1")
            self.assertEqual(preview["overlay_patch_count"], 1)
            self.assertEqual(preview["resolution_counts"], {"AUTO_RESOLVED": 1})
            self.assertEqual(preview["overlay_patches"][0]["before"]["assignments"][0]["activity"], "Русский")
            self.assertEqual(preview["overlay_patches"][0]["after"]["assignments"][0]["activity"], "Математика")
            draft = merge_import_changes(database, "base-v1", expected_revision=0,
                proposed_changes=preview_changes(preview), scope_kind="template",
                source_context={"source": "google_sheet", "fingerprint": preview["source_fingerprint"]})
            self.assertEqual(len(draft["payload"]["changes"]), 1)
            published = publish_draft(database, "template", "base-v1", expected_revision=draft["revision"])
            self.assertEqual(read_canonical_template(database, published["published_id"])["blocks"]["base"]["assignments"][0]["activity"], "Математика")
            self.assertFalse(get_draft(database, "template", "base-v1")["has_changes"])
            confirm_template_snapshot(database, spreadsheet_id="sheet", spreadsheet_title="Расписание", template_tab=changed,
                expected_version_id=published["published_id"], expected_source_fingerprint=preview["source_fingerprint"])
            same = preview_template_snapshot(database, spreadsheet_id="sheet", template_tab=changed, expected_version_id=published["published_id"])
            self.assertEqual(same["overlay_patch_count"], 0)

    def test_time_move_keeps_manual_mapping_without_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "move.db")
            database.initialize()
            import_canonical_artifact(database, {"schema_version": "test-v1", "version_id": "manual-v1",
                "source_snapshot": {"id": "base", "fingerprint": "base"},
                "blocks": {"manual": block("manual", "Русский", "g1", start="09:55")}}, status="approved_baseline")
            tab = {"sheet_id": "1", "title": "2026/27 шаблон", "values": matrix("Русский", "", ""), "merges": [], "structured_cells": []}
            confirm_template_snapshot(database, spreadsheet_id="sheet", spreadsheet_title="Расписание", template_tab=tab, expected_version_id="manual-v1")
            moved = {**tab, "values": [*tab["values"][:2], ["09:55-10:40", "Русский", "", ""]]}
            preview = preview_template_snapshot(database, spreadsheet_id="sheet", template_tab=moved, expected_version_id="manual-v1")
            self.assertEqual(preview["overlay_patch_count"], 1)
            self.assertEqual(preview["overlay_patches"][0]["block_key"], "manual")
            self.assertEqual(preview["overlay_patches"][0]["change_classification"], "MOVED")

    def test_two_clear_source_changes_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "two.db")
            database.initialize()
            for grade in (5, 6):
                student = database.create_identity("student", f"Student {grade}", f"{grade}A")
                group = database.create_group(f"{grade}A", "class")
                database.create_membership(group["id"], student["id"], "student", "test")
            import_canonical_artifact(database, {"schema_version": "test-v1", "version_id": "two-v1",
                "source_snapshot": {"id": "base", "fingerprint": "base"}, "blocks": {}}, status="approved_baseline")
            tab = {"sheet_id": "1", "title": "2026/27 шаблон", "values": matrix("Русский", "История", ""), "merges": [], "structured_cells": []}
            confirm_template_snapshot(database, spreadsheet_id="sheet", spreadsheet_title="Расписание", template_tab=tab, expected_version_id="two-v1")
            changed = {**tab, "values": matrix("Математика", "Литература", "")}
            preview = preview_template_snapshot(database, spreadsheet_id="sheet", template_tab=changed, expected_version_id="two-v1")
            self.assertEqual(preview["overlay_patch_count"], 2)
            self.assertEqual(preview["resolution_counts"], {"AUTO_RESOLVED": 2})

    def test_unresolved_teacher_is_suggestion_not_auto_resolved(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "teacher.db")
            database.initialize()
            student = database.create_identity("student", "One", "5A")
            teacher = database.create_identity("teacher", "Teacher One")
            group = database.create_group("5A", "class")
            database.create_membership(group["id"], student["id"], "student", "test")
            old_block = block("base", "Русский", str(group["id"]))
            old_block["assignments"][0]["teacher_ids"] = [str(teacher["id"])]
            import_canonical_artifact(database, {"schema_version": "test-v1", "version_id": "teacher-v1",
                "source_snapshot": {"id": "base", "fingerprint": "base"}, "blocks": {"base": old_block}}, status="approved_baseline")
            tab = {"sheet_id": "1", "title": "2026/27 шаблон", "values": matrix("Русский", "", ""), "merges": [], "structured_cells": []}
            confirm_template_snapshot(database, spreadsheet_id="sheet", spreadsheet_title="Расписание", template_tab=tab, expected_version_id="teacher-v1")
            preview = preview_template_snapshot(database, spreadsheet_id="sheet", template_tab={**tab, "values": matrix("Математика", "", "")}, expected_version_id="teacher-v1")
            self.assertEqual(preview["resolution_counts"], {"NEEDS_CONFIRMATION": 1})
            self.assertTrue(preview_changes(preview)[0]["review_required"])

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
