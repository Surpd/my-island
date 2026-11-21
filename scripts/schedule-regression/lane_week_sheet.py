"""Return actual merged Google Sheet cell coordinates without talking to Google."""
import json
import sys
from backend.services.schedule_sheet_export import build_sheet_plan, freeze_template

with open(sys.argv[1]) as private_copy:
    snapshot = json.load(private_copy)
plan = build_sheet_plan(freeze_template(dict(snapshot, teachers=[], source={})), title="Local",
                        sheet_id=1, mapping_sheet_id=2, layout="school-grid")
cells = {(c["row"], c["col"]): c for c in plan["cells"]}
classes = {c["start"]: (cells[0, c["start"]]["text"].split(" ")[0], c["width"]) for c in plan["class_columns"]}
slots = {}
for lesson in plan["mapping"]:
    for card in [lesson, *lesson["references"]]:
        row, col = card["row"], card["col"]
        day = max(i for i, start in enumerate(plan["day_rows"]) if row > start)
        times = cells[row, 0]["text"].split("–")[0]
        period = next((i for i, bell in enumerate(snapshot["payload"]["slots"]) if bell["start"] == times), -1)
        first = max(c for c in classes if c <= col)
        grade, width = classes[first]
        slot = slots.setdefault(f"{grade}/{day}/{period}", {})
        for source in lesson["source_lesson_ids"]:
            slot[source] = [col-first, col-first+cells[row, col]["width"], width]
print(json.dumps(slots))
