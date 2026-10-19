"""Large canonical template regression with PostgreSQL-like SQLite row values."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import time
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact
from backend.services.schedule_experiments import (
    create_experiment,
    create_experiment_week,
    duplicate_experiment,
    get_experiment,
    list_experiments,
    open_school_canonical_template,
    save_experiment,
)


class _TypedSQLiteRow(Mapping):
    """SQLite row mapping that materializes SQL TIME fields as native time values."""

    def __init__(self, cursor: sqlite3.Cursor, values: tuple[object, ...]) -> None:
        self._keys = tuple(column[0] for column in cursor.description)
        self._values = dict(zip(self._keys, values))
        for key in ("start_time", "end_time"):
            value = self._values.get(key)
            if isinstance(value, str) and value:
                hour, minute, *seconds = map(int, value.split(":"))
                self._values[key] = time(hour, minute, seconds[0] if seconds else 0)

    def __getitem__(self, key):
        if isinstance(key, int):
            key = self._keys[key]
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


class _NativeTimeSQLiteDatabase(Database):
    """Use real SQLite SQL operations while exposing PostgreSQL's native TIME type."""

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = _TypedSQLiteRow
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class LargeSchoolTemplateImportTests(unittest.TestCase):
    def test_large_native_time_canonical_copy_survives_json_save_duplicate_and_week(self):
        with tempfile.TemporaryDirectory() as temp:
            database = _NativeTimeSQLiteDatabase(Path(temp) / "large-template.db")
            database.initialize()
            classes = [database.create_group(f"{grade}A", "class") for grade in range(5, 14)]
            subgroups = [database.create_group(f"{grade}A · группа", "instructional") for grade in range(5, 14)]
            class_ids = [str(group["id"]) for group in classes]
            subgroup_ids = [str(group["id"]) for group in subgroups]
            slots = [
                ("09:00", "09:45"), ("09:55", "10:40"), ("10:55", "11:40"),
                ("11:50", "12:35"), ("12:45", "13:30"), ("13:40", "14:25"),
                ("14:35", "15:20"), ("15:30", "16:15"), ("16:20", "17:05"),
            ]
            blocks = {}
            expected_ids = set()
            for block_index in range(265):
                weekday = block_index % 5
                start, end = slots[(block_index // 5) % len(slots)]
                assignment_count = 2 if block_index < 121 else 1
                assignments = []
                for assignment_index in range(assignment_count):
                    ordinal = len(expected_ids)
                    block_key = f"school-block-{block_index:03d}"
                    expected_ids.add(f"{block_key}:{assignment_index}")
                    class_index = ordinal % len(class_ids)
                    # Preserve a representative 327 single-group / 59 residual mix.
                    residual = ordinal >= 327
                    group_ids = [] if residual else [
                        subgroup_ids[class_index] if ordinal % 7 == 0 else class_ids[class_index]
                    ]
                    assignments.append({
                        "activity": f"Предмет {ordinal:03d}",
                        "role": "residual" if residual else "primary",
                        "audience": {"type": "remaining" if residual else "canonical_groups",
                                     "canonical_group_ids": group_ids},
                        "teacher_ids": [],
                        "metadata": {
                            "room": str(100 + ordinal % 40),
                            "audience_rule": ({"kind": "remaining", "class_group_id": class_ids[class_index],
                                               "partition_group_ids": [subgroup_ids[class_index]]}
                                              if residual else {"kind": "groups", "group_ids": group_ids}),
                            "source_annotation": {"source_ordinal": ordinal, "source_cells": [f"cell-{ordinal}"]},
                        },
                    })
                blocks[f"school-block-{block_index:03d}"] = {
                    "weekday": weekday, "slot": {"start": start, "end": end},
                    "grade_scope": str(5 + block_index % 9), "status": "resolved",
                    "mode": "SINGLE", "assignments": assignments,
                }

            artifact = {
                "schema_version": "large-template-test-v1", "version_id": "large-school-template-v1",
                "source_snapshot": {"id": "isolated-large-test", "fingerprint": "large-test-fingerprint"},
                "blocks": blocks,
            }
            imported = import_canonical_artifact(database, artifact, status="approved_baseline")
            self.assertEqual(imported["blocks"], 265)
            self.assertEqual(imported["assignments"], 386)

            template = open_school_canonical_template(database)
            payload = template["payload"]
            self.assertFalse(template.get("already_exists", False))
            self.assertEqual({lesson["id"] for lesson in payload["lessons"]}, expected_ids)
            self.assertEqual(len(payload["lessons"]), 386)
            self.assertEqual(payload["transfer_result"]["transferred"] +
                             payload["transfer_result"]["needs_clarification"] +
                             payload["transfer_result"]["import_errors"], 386)
            sample = payload["lessons"][0]
            self.assertIsInstance(sample["canonical"]["block"]["start_time"], str)
            self.assertIn("source_annotation", sample["canonical"]["assignment"]["metadata"])
            self.assertEqual(payload["school_source"]["role"], "primary_school_template")
            self.assertEqual(payload["school_source"]["primary_draft_id"], template["id"])

            repeated = open_school_canonical_template(database)
            self.assertEqual(repeated["id"], template["id"])
            self.assertEqual(repeated["revision"], 1)
            self.assertTrue(repeated["already_exists"])
            self.assertEqual(len(list_experiments(database)), 1)

            edited_payload = {**payload, "column_width_mode": "manual", "column_widths": {"grade:5": 181}}
            saved = save_experiment(database, template["id"], expected_revision=1,
                                    name="My edited school template", payload=edited_payload)
            reloaded = get_experiment(database, template["id"])
            self.assertEqual(reloaded["payload"], saved["payload"])
            self.assertEqual({lesson["id"] for lesson in reloaded["payload"]["lessons"]}, expected_ids)
            self.assertEqual(reloaded["revision"], 2)
            reopened = open_school_canonical_template(database)
            self.assertEqual(reopened["id"], template["id"])
            self.assertEqual(reopened["revision"], 2)
            self.assertEqual(reopened["name"], "My edited school template")
            self.assertEqual(reopened["payload"]["column_widths"], {"grade:5": 181})

            copied = duplicate_experiment(database, template["id"])
            self.assertEqual(copied["payload"], reloaded["payload"])
            week = create_experiment_week(database, template["id"], week_start="2026-10-19", expected_revision=2)
            self.assertEqual(len(week["payload"]["lessons"]), 386)
            self.assertEqual(week["payload"]["source_template"], {"id": template["id"], "revision": 2})
            self.assertNotIn("week_start", reloaded["payload"])
            self.assertNotIn("week_start", copied["payload"])
            self.assertEqual(copied["payload"]["school_source"]["primary_draft_id"], template["id"])
            self.assertNotEqual(copied["id"], copied["payload"]["school_source"]["primary_draft_id"])
            final_open = open_school_canonical_template(database)
            self.assertEqual(final_open["id"], template["id"])
            self.assertEqual(final_open["revision"], 2)
            self.assertEqual(len(list_experiments(database)), 3)

            with self.assertRaisesRegex(TypeError, "not JSON serializable"):
                create_experiment(database, name="Unsupported value", source="import", payload={
                    "days": [0], "slots": [{"start": "09:00", "end": "09:45"}],
                    "lessons": [{"id": "unsupported", "day": 0, "period": 0,
                                 "subject": "Test", "unexpected": object()}],
                })


if __name__ == "__main__":
    unittest.main()
