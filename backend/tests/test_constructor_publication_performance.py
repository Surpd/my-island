"""Bounded preview work on a synthetic 357-row school week.

Set MY_ISLAND_REPORT_PERF=1 to print the measured stages for the PR report.
The fixture is disposable and contains no school data.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.database import Database
from backend.services import canonical_schedule as canonical
from backend.services import constructor_publication as publication
from backend.services import schedule_experiments
from backend.services.constructor_publication import initialize_publication_schema
from backend.services.schedule_experiments import create_experiment


class ConstructorPublicationPerformanceTests(unittest.TestCase):
    def test_preview_357_row_week_bounds_personal_projection_and_sql_work(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "preview.db")
            database.initialize()
            initialize_publication_schema(database)
            classes, subgroups, teachers = {}, {}, []
            for grade in range(5, 12):
                class_group = database.create_group(f"{grade}A", "class", base_class_name=str(grade))
                classes[grade] = class_group
                subgroups[grade] = [database.create_group(f"Math {grade}-{lane}", "instructional",
                    base_class_name=str(grade), subject="Math", subject_subgroup=lane) for lane in ("A", "B")]
                for index in range(25):
                    student = database.create_identity("student", f"Synthetic {grade}-{index}", f"{grade}A")
                    database.create_membership(class_group["id"], student["id"], "student", "perf-fixture")
                    database.create_membership(subgroups[grade][0 if index < 13 else 1]["id"], student["id"],
                                               "student", "perf-fixture")
            teachers = [database.create_identity("teacher", f"Synthetic teacher {index}") for index in range(35)]
            slots = []
            for period in range(11):
                start = 8 * 60 + period * 50
                slots.append({"start": f"{start // 60:02d}:{start % 60:02d}",
                              "end": f"{(start + 45) // 60:02d}:{(start + 45) % 60:02d}"})
            lessons = []
            for class_index, (grade, class_group) in enumerate(classes.items()):
                made = 0
                for day in range(5):
                    for period in range(11):
                        if made >= 47:
                            break
                        teacher = teachers[(class_index * 5 + period) % len(teachers)]
                        lessons.append({"id": f"g{grade}-d{day}-p{period}", "day": day, "period": period,
                            "subject": f"Subject {period + 1}", "classId": str(class_group["id"]),
                            "classIds": [str(class_group["id"])],
                            "audience": {"kind": "classes", "class_ids": [str(class_group["id"])]},
                            "teacherId": str(teacher["id"]), "room": f"{200 + class_index * 11 + period}"})
                        made += 1
                    if made >= 47:
                        break
                if grade in {5, 6}:
                    for lane in range(4):
                        lessons.append({"id": f"g{grade}-elective-{lane}", "day": 4, "period": 9,
                            "subject": f"Choice direction {lane + 1}", "activityType": "elective",
                            "blockId": "elective-5-6-perf", "blockName": "Курсы по выбору",
                            "classId": str(class_group["id"]), "classIds": [str(class_group["id"])],
                            "eligibleGrades": [5, 6], "teacherId": str(teachers[(grade + lane) % 35]["id"]),
                            "room": f"E-{lane}"})
                else:
                    for period in range(4):
                        day = 4
                        teacher = teachers[(class_index * 5 + period + 11) % len(teachers)]
                        lessons.append({"id": f"g{grade}-extra-{period}", "day": day, "period": 7 + period,
                            "subject": f"Extra {period + 1}", "classId": str(class_group["id"]),
                            "classIds": [str(class_group["id"])],
                            "audience": {"kind": "classes", "class_ids": [str(class_group["id"])]},
                            "teacherId": str(teacher["id"]), "room": f"X-{grade}-{period}"})
            self.assertEqual(len(lessons), 357)
            draft = create_experiment(database, name="performance-357", source="import", payload={
                "reference_date": "2026-11-09", "days": list(range(5)),
                "classes": [str(group["id"]) for group in classes.values()], "slots": slots, "lessons": lessons})

            sql_statements: list[str] = []
            connections = 0
            original_connection = database.connection

            @contextmanager
            def traced_connection():
                nonlocal connections
                connections += 1
                with original_connection() as connection:
                    connection.set_trace_callback(sql_statements.append)
                    yield connection

            database.connection = traced_connection
            projection_calls = 0
            projection_seconds = 0.0
            original_project = canonical._project_student_block

            def measured_project(*args, **kwargs):
                nonlocal projection_calls, projection_seconds
                projection_calls += 1
                started = time.perf_counter()
                result = original_project(*args, **kwargs)
                projection_seconds += time.perf_counter() - started
                return result

            stages = {"projection_mismatches_ms": 0.0, "audience_preview_ms": 0.0}
            original_mismatches = publication._projection_mismatches

            def measured_mismatches(*args, **kwargs):
                started = time.perf_counter()
                result = original_mismatches(*args, **kwargs)
                stages["projection_mismatches_ms"] += (time.perf_counter() - started) * 1000
                return result

            original_audience_preview = schedule_experiments.experiment_audience_preview

            def measured_audience_preview(*args, **kwargs):
                started = time.perf_counter()
                result = original_audience_preview(*args, **kwargs)
                stages["audience_preview_ms"] += (time.perf_counter() - started) * 1000
                return result

            started = time.perf_counter()
            with patch.object(canonical, "_project_student_block", side_effect=measured_project), \
                 patch.object(publication, "_projection_mismatches", side_effect=measured_mismatches), \
                 patch.object(schedule_experiments, "experiment_audience_preview", side_effect=measured_audience_preview):
                result = publication.preview_publication(database, draft["id"], scope_kind="template",
                    expected_revision=draft["revision"])
            elapsed_ms = (time.perf_counter() - started) * 1000

            self.assertTrue(result["can_publish"], result["blockers"])
            self.assertEqual(result["summary"]["source_lessons"], 357)
            self.assertEqual(connections, 1, "publication preview must reuse one fresh database connection")
            self.assertLessEqual(len(sql_statements), 15, f"unexpected DB round-trips: {len(sql_statements)}")
            self.assertLess(projection_calls, 1000,
                            f"preview projected each pupil independently ({projection_calls} calls)")
            if os.getenv("MY_ISLAND_REPORT_PERF") == "1":
                print(json.dumps({"dataset": "synthetic 357 rows, 7 grades, 175 students, 35 teachers, split-subgroup memberships",
                    "elapsed_ms": round(elapsed_ms, 1), "sqlite_statements": len(sql_statements), "connections": connections,
                    "personal_projection_calls": projection_calls,
                    "personal_projection_ms": round(projection_seconds * 1000, 1), **stages}, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
