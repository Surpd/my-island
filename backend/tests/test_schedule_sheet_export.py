import copy
import json
import unittest

from backend.services.schedule_sheet_export import freeze_week, build_sheet_plan, sheets_requests


class ScheduleSheetExportTests(unittest.TestCase):
    def source(self):
        return {"source": {"draft_id": "template", "revision": 3},
                "groups": [{"id": "c5", "name": "5", "group_type": "class"},
                           {"id": "c6", "name": "6", "group_type": "class"},
                           {"id": "g", "display_name": "Английский · группа 1", "group_type": "instructional"}],
                "teachers": [{"id": "t", "display_name": "Юлия"}],
                "payload": {"classes": ["c6", "c5"], "days": [0, 1, 2, 3, 4],
                            "slots": [{"start": "09:00", "end": "09:45"}, {"start": "09:55", "end": "10:40"}],
                            "lessons": [self.lesson("a", ["c5", "c6"]),
                                        self.lesson("b", ["c5"], start="09:20", end="10:40"),
                                        self.lesson("c", ["c6"], day=1, specialMark="lunch", subject="Обед")]}}

    def lesson(self, lid, classes, **kwargs):
        return {"id": lid, "day": 0, "start": "09:00", "end": "09:45", "classIds": classes,
                "teacherIds": ["t"], "groupIds": ["g"], "room": "17", "subject": "Английский язык", **kwargs}

    def plan(self, source=None):
        return build_sheet_plan(freeze_week(source or self.source(), "2026-10-19"),
                                title="Новая неделя", sheet_id=101, mapping_sheet_id=102)

    def test_detached_week_and_stable_identity(self):
        source = self.source()
        source["payload"]["lessons"][0]["audience_snapshot"] = {"student_ids": ["private"]}
        frozen = freeze_week(source, "2026-10-19")
        repeated = freeze_week(source, "2026-10-19")
        self.assertEqual(frozen, repeated)
        self.assertNotIn("private", json.dumps(frozen))
        source["payload"]["lessons"][0]["subject"] = "Changed template"
        source["teachers"][0]["display_name"] = "Changed teacher"
        self.assertEqual(frozen["payload"]["lessons"][0]["subject"], "Английский язык")
        self.assertEqual(frozen["teachers"][0]["display_name"], "Юлия")
        self.assertNotEqual(frozen["snapshot_id"], freeze_week(source, "2026-10-19")["snapshot_id"])

    def test_parallel_and_long_lessons_have_no_overwrites(self):
        plan = self.plan()
        occupied = set()
        for cell in plan["cells"]:
            for row in range(cell["row"], cell["row"] + cell["height"]):
                for col in range(cell["col"], cell["col"] + cell["width"]):
                    self.assertNotIn((row, col), occupied)
                    occupied.add((row, col))
        self.assertEqual({m["lesson_id"] for m in plan["mapping"]}, {"a", "b", "c"})
        self.assertTrue(any(c["height"] > 1 for c in plan["cells"] if "MY_ISLAND:" in c["note"]))
        self.assertEqual(plan["class_columns"][0]["label"], "5")
        self.assertTrue(any(c["text"] == "09:20–09:45" for c in plan["cells"]))

    def test_adjacent_shared_class_lesson_is_one_merged_placement(self):
        source = self.source()
        source["payload"]["lessons"] = source["payload"]["lessons"][:1]
        plan = self.plan(source)
        self.assertEqual(len(plan["mapping"]), 1)
        self.assertEqual(plan["mapping"][0]["class_ids"], ["c5", "c6"])
        cell = next(c for c in plan["cells"] if c["note"].startswith("MY_ISLAND:"))
        self.assertEqual(cell["width"], 2)
        self.assertIn("Юлия", cell["text"])
        self.assertIn("группа 1", cell["text"])
        self.assertIn("каб. 17", cell["text"])

    def test_requests_only_create_new_tabs_and_store_snapshot(self):
        plan = self.plan()
        requests = sheets_requests(plan)
        self.assertEqual([list(r)[0] for r in requests[:2]], ["addSheet", "addSheet"])
        self.assertTrue(requests[1]["addSheet"]["properties"]["hidden"])
        self.assertTrue(all(len(r) == 1 for r in requests))
        for request in requests:
            if "mergeCells" in request:
                merged = request["mergeCells"]["range"]
                self.assertFalse(merged["startColumnIndex"] < 1 < merged["endColumnIndex"])
                self.assertFalse(merged["startRowIndex"] < 2 < merged["endRowIndex"])
        self.assertFalse(any(set(r) & {"copyPaste", "duplicateSheet", "deleteSheet"} for r in requests))
        notes = [c["note"] for c in plan["cells"] if c["note"].startswith("MY_ISLAND:")]
        self.assertEqual(len(notes), len(plan["mapping"]))

    def test_invalid_sources_fail_before_export(self):
        source = self.source()
        source["payload"]["lessons"][0]["classIds"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "Unresolved class"):
            self.plan(source)
        source = self.source()
        source["payload"]["lessons"].append(copy.deepcopy(source["payload"]["lessons"][0]))
        with self.assertRaisesRegex(ValueError, "unique"):
            self.plan(source)
        with self.assertRaisesRegex(ValueError, "Monday"):
            freeze_week(self.source(), "2026-10-20")

    def compact(self, source=None):
        return build_sheet_plan(freeze_week(source or self.source(), "2026-10-19"), title="Компактный",
                                sheet_id=201, mapping_sheet_id=202, layout="compact-v2")

    def test_compact_merges_administrative_classes_without_losing_distinct_ids(self):
        source = self.source()
        source["groups"][:2] = [{"id": "c5", "name": "9-А", "group_type": "class"},
                                {"id": "c6", "name": "9-Д", "group_type": "class"}]
        source["payload"]["lessons"] = [
            self.lesson("math-a", ["c5"], subject="Математика"),
            self.lesson("social", ["c5", "c6"], subject="Обществознание"),
            self.lesson("physics", ["c5", "c6"], subject="Физика")]
        plan = self.compact(source)
        self.assertEqual(plan["columns"], 2)  # time + one grade, not two classes
        self.assertEqual(plan["class_columns"][0]["label"], "9")
        self.assertEqual(len(plan["mapping"]), 3)
        self.assertEqual({m["lesson_id"] for m in plan["mapping"]}, {"math-a", "social", "physics"})
        math = next(c for c in plan["cells"] if '"lesson_id":"math-a"' in c["note"])
        self.assertIn("9-А", math["text"])
        social = next(c for c in plan["cells"] if '"lesson_id":"social"' in c["note"])
        self.assertNotIn("9-А", social["text"])

    def test_compact_keeps_same_subject_and_overlapping_audiences_as_different_lessons(self):
        source = self.source()
        source["payload"]["lessons"] = [self.lesson("one", ["c5", "c6"]), self.lesson("two", ["c5"])]
        plan = self.compact(source)
        self.assertEqual({m["lesson_id"] for m in plan["mapping"]}, {"one", "two"})
        self.assertEqual(sum(5 in m["grade_numbers"] for m in plan["mapping"]), 2)
        self.assertEqual(sum(6 in m["grade_numbers"] for m in plan["mapping"]), 1)

    def test_compact_shared_adjacent_grades_merge_but_nonadjacent_grades_do_not(self):
        source = self.source()
        source["payload"]["lessons"] = source["payload"]["lessons"][:1]
        plan = self.compact(source)
        self.assertEqual(len(plan["mapping"]), 1)
        self.assertEqual(plan["mapping"][0]["grade_numbers"], [5, 6])
        source["groups"].append({"id": "c7", "name": "7", "group_type": "class"})
        source["payload"]["classes"].append("c7")
        source["payload"]["lessons"][0]["classIds"] = ["c5", "c7"]
        plan = self.compact(source)
        self.assertEqual(len(plan["mapping"]), 2)
        self.assertEqual({tuple(m["grade_numbers"]) for m in plan["mapping"]}, {(5,), (7,)})

    def test_compact_long_lessons_notes_and_coverage(self):
        source = self.source()
        source["payload"]["lessons"][1].update(needsClarification=True, clarificationReason="Проверить аудиторию")
        plan = self.compact(source)
        occupied = set()
        for cell in plan["cells"]:
            for r in range(cell["row"], cell["row"]+cell["height"]):
                for c in range(cell["col"], cell["col"]+cell["width"]):
                    self.assertNotIn((r, c), occupied)
                    occupied.add((r, c))
        self.assertEqual(plan["snapshot_id"], freeze_week(source, "2026-10-19")["snapshot_id"])
        for grade in [5]:
            segments = sorted((m["segment_start"], m["segment_end"]) for m in plan["mapping"]
                              if m["lesson_id"] == "b" and grade in m["grade_numbers"])
            self.assertEqual(segments[0][0], "09:20")
            self.assertEqual(segments[-1][1], "10:40")
            self.assertTrue(all(a[1] == b[0] for a, b in zip(segments, segments[1:])))
        notes = [c["note"] for c in plan["cells"] if '"lesson_id":"b"' in c["note"]]
        self.assertTrue(all("Требует уточнения: Проверить аудиторию" in n for n in notes))
        self.assertTrue(all("Английский · группа 1" in n for n in notes))

    def test_compact_requests_fit_laptop_and_preserve_add_only_contract(self):
        plan = self.compact()
        requests = sheets_requests(plan)
        self.assertLessEqual(sum(plan["column_widths"]), 1120)
        self.assertEqual(requests[2]["updateDimensionProperties"]["properties"]["pixelSize"], 80)
        self.assertEqual(requests[3]["updateDimensionProperties"]["properties"]["pixelSize"], 148)
        self.assertTrue(any(r.get("updateBorders", {}).get("top", {}).get("style") == "SOLID_MEDIUM" for r in requests))
        for request in requests:
            if "mergeCells" in request:
                merged = request["mergeCells"]["range"]
                self.assertFalse(merged["startColumnIndex"] < 1 < merged["endColumnIndex"])
                self.assertFalse(merged["startRowIndex"] < 2 < merged["endRowIndex"])
        self.assertEqual([r["addSheet"]["properties"]["sheetId"] for r in requests if "addSheet" in r], [201, 202])

    def readable(self, source=None, evidence=None):
        return build_sheet_plan(freeze_week(source or self.source(), "2026-10-19"), title="v3",
                                sheet_id=301, mapping_sheet_id=302, layout="readable-v3",
                                audience_evidence=evidence)

    def test_v3_single_grade_parallel_lessons_are_horizontal(self):
        source = self.source()
        source["groups"][:2] = [{"id": "c5", "name": "9-Ан", "group_type": "class"},
                                {"id": "c6", "name": "9-Д", "group_type": "class"}]
        source["payload"]["lessons"] = [self.lesson("a", ["c5"]), self.lesson("b", ["c5", "c6"]),
                                        self.lesson("c", ["c5", "c6"])]
        plan = self.readable(source)
        cards = [c for c in plan["cells"] if c["note"].startswith("MY_ISLAND:")]
        self.assertEqual(len(cards), 3)
        self.assertEqual(len({c["row"] for c in cards}), 1)
        self.assertEqual(len({c["col"] for c in cards}), 3)

    def test_v3_group_audience_is_not_an_administrative_class_restriction(self):
        source = self.source()
        cid = "b021cc32-b132-4842-9cf3-54d7960112ac"
        source["groups"][:2] = [{"id": cid, "name": "9-А", "group_type": "class"},
                                {"id": "c6", "name": "9-Д", "group_type": "class"}]
        source["groups"][2].update(name="grade9-math-A", display_name="Математика · группа A · 9 класс")
        source["payload"]["classes"] = [cid, "c6"]
        source["payload"]["lessons"] = [
            self.lesson("a", [cid], subject="Математика", audience={"kind": "groups", "group_ids": ["g"]}),
            self.lesson("b", [cid], subject="Классный час", audience={"kind": "classes", "class_ids": [cid]})]
        before = copy.deepcopy(source)
        plan = self.readable(source)
        cards = {json.loads(c["note"].splitlines()[0][10:])["lesson_id"]: c for c in plan["cells"] if c["note"].startswith("MY_ISLAND:")}
        self.assertTrue(cards["a"]["text"].startswith("Математика A"))
        self.assertNotIn("9-", cards["a"]["text"])
        self.assertIn("9-Ан", cards["b"]["text"])
        self.assertEqual(source, before)
        self.assertEqual(plan["snapshot"]["groups"][0]["name"], "9-А")

    def test_v3_clubs_are_opt_in_not_a_late_time_heuristic(self):
        source = self.source()
        source["payload"]["lessons"] = [
            self.lesson("club", ["c5"], subject="⚪АНИМАЦИЯ"),
            self.lesson("late", ["c5"], start="16:20", end="17:05", subject="Физика ЕГЭ"),
            self.lesson("unknown", ["c6"], subject="⚪НЕПОДТВЕРЖДЁННОЕ")]
        plan = self.readable(source)
        by_id = {m["lesson_id"]: m for m in plan["mapping"]}
        self.assertEqual(by_id["club"]["activity_type"], "optional_activity")
        self.assertEqual(by_id["club"]["participation"], "opt_in")
        self.assertEqual(by_id["club"]["section"], "clubs")
        self.assertEqual(by_id["late"]["section"], "schedule")
        self.assertEqual(by_id["unknown"]["activity_type"], "unspecified")
        self.assertEqual(by_id["unknown"]["section"], "activity_review")
        self.assertEqual({m["lesson_id"] for m in plan["mapping"]}, {"club", "late", "unknown"})

    def test_v3_cached_scope_is_versioned_and_never_changes_source(self):
        source = self.source()
        source["source"]["school_source"] = {"canonical_version_id": "v1"}
        with self.assertRaisesRegex(ValueError, "another canonical"):
            self.readable(source, {"evidence_source": {"canonical_version_id": "v2"}})
        source["payload"]["lessons"] = [self.lesson("a", ["c5"], audience={"kind": "groups", "group_ids": ["g"]})]
        plan = self.readable(source, {"evidence_source": {"canonical_version_id": "v1", "authoritative": False},
                                     "group_class_ids": {"g": ["c6"]}})
        self.assertEqual(plan["presentation_context"]["resolved_audiences"]["a"]["grade_numbers"], [5, 6])
        self.assertEqual(plan["snapshot"]["payload"]["lessons"][0]["classIds"], ["c5"])
        self.assertEqual(plan["summary"]["audience_scope_differences"], 1)

    def test_v3_long_lesson_coverage_and_add_only_requests(self):
        plan = self.readable()
        requests = sheets_requests(plan)
        self.assertLessEqual(sum(plan["column_widths"]), 1280)
        self.assertTrue(plan["gutters"])
        self.assertTrue(all(plan["column_widths"][c] == 8 for c in plan["gutters"]))
        self.assertEqual({m["lesson_id"] for m in plan["mapping"]}, {"a", "b", "c"})
        self.assertTrue(all(c["size"] >= 9 for c in plan["cells"]))
        for r in requests:
            if "mergeCells" in r:
                g = r["mergeCells"]["range"]
                self.assertFalse(g["startColumnIndex"] < 1 < g["endColumnIndex"])
                self.assertFalse(g["startRowIndex"] < 2 < g["endRowIndex"])
            self.assertFalse(set(r) & {"copyPaste", "deleteSheet", "duplicateSheet"})

    def test_v3_preserves_annotations_and_chunks_presentation_metadata(self):
        source = self.source()
        plan = build_sheet_plan(freeze_week(source, "2026-10-19"), title="v3", sheet_id=301,
                                mapping_sheet_id=302, layout="readable-v3",
                                existing_notes={"a": ["Сохранить это уточнение"]})
        notes = [c["note"] for c in plan["cells"] if '"lesson_id":"a"' in c["note"]]
        self.assertTrue(all("Сохранить это уточнение" in note for note in notes))
        plan["presentation_context"]["test_large_payload"] = "я"*51000
        requests = sheets_requests(plan)
        hidden = next(r["updateCells"] for r in requests if r.get("updateCells", {}).get("start", {}).get("sheetId") == 302)
        chunks = [r["values"][1]["userEnteredValue"]["stringValue"] for r in hidden["rows"]
                  if r["values"][0]["userEnteredValue"]["stringValue"].startswith("presentation_context:")]
        self.assertTrue(all(len(chunk) <= 25000 for chunk in chunks))
        self.assertEqual(json.loads("".join(chunks)), plan["presentation_context"])


class FinalCompactTests(unittest.TestCase):
    source = ScheduleSheetExportTests.source
    lesson = ScheduleSheetExportTests.lesson

    def final(self, source, notes=None):
        return build_sheet_plan(freeze_week(source, "2026-10-19"), title="final", sheet_id=401,
                                mapping_sheet_id=402, layout="final-compact", existing_notes=notes)

    def test_final_enriches_duplicates_preserving_every_source_and_note(self):
        source = self.source()
        audience = {"kind": "groups", "group_ids": ["g"]}
        source["payload"]["lessons"] = [self.lesson("a", ["c5"], audience=audience),
                                        self.lesson("b", ["c6"], audience=audience, teacherIds=[])]
        original = copy.deepcopy(source)
        plan = self.final(source, {"a": ["note-a"], "b": ["note-b"]})
        self.assertEqual(source, original)
        self.assertEqual(plan["summary"]["logical_occurrences"], 1)
        self.assertEqual(plan["summary"]["source_ids"], 2)
        mapping = plan["mapping"][0]
        self.assertEqual(mapping["source_lesson_ids"], ["a", "b"])
        self.assertEqual(mapping["grade_numbers"], [5, 6])
        card = next(c for c in plan["cells"] if c["note"].startswith("MY_ISLAND:"))
        for text in ["note-a", "note-b", "Юлия"]:
            self.assertIn(text, card["note"])
        hidden = next(r["updateCells"] for r in sheets_requests(plan)
                      if r.get("updateCells", {}).get("start", {}).get("sheetId") == 402)
        for row in hidden["rows"][6:8]:
            aliases = json.loads(row["values"][2]["userEnteredValue"]["stringValue"])
            self.assertEqual(aliases[0]["source_lesson_ids"], ["a", "b"])

    def test_final_blank_assignment_cannot_bridge_conflicting_records(self):
        source = self.source()
        audience = {"kind": "groups", "group_ids": ["g"]}
        source["payload"]["lessons"] = [self.lesson("a", ["c5"], audience=audience, room="17"),
                                        self.lesson("b", ["c5"], audience=audience, room="", teacherIds=[]),
                                        self.lesson("c", ["c5"], audience=audience, room="18")]
        plan = self.final(source)
        self.assertEqual(plan["summary"]["logical_occurrences"], 3)
        self.assertEqual(plan["summary"]["ambiguous_duplicate_clusters"], 1)
        self.assertTrue(all(m["needs_clarification"] for m in plan["mapping"]))
        source["payload"]["lessons"][2]["audience"] = {"kind": "classes", "class_ids": ["c5"]}
        plan = self.final(source)
        self.assertEqual(plan["summary"]["logical_occurrences"], 2)

    def test_final_uses_bell_slots_without_fabricated_break_rows(self):
        plan = self.final(self.source())
        cards = [c for c in plan["cells"] if c["note"].startswith("MY_ISLAND:")]
        self.assertEqual(len({c["row"] for c in cards if '"lesson_id":"b"' in c["note"]}), 2)
        self.assertTrue(all(len(c["text"].splitlines()) <= 2 for c in cards))
        self.assertFalse(any(c["text"] == "09:45–09:55" for c in plan["cells"]))
        self.assertTrue(all(c["height"] == 1 for c in cards))
        self.assertEqual(plan["day_metrics"][0]["rows"], 3)


if __name__ == "__main__":
    unittest.main()
