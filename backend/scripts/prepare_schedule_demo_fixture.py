"""Create one persistent demo week in the marked local manual-test SQLite only."""
from __future__ import annotations

import copy
import json

from backend.database import Database
from backend.scripts.local_test_env import DB_PATH, MARKER, MARKER_CONTENT, PROFILE
from backend.services.schedule_experiments import (
    create_experiment, experiment_audience_preview, get_experiment, list_experiments,
)


NAME = "ДЕМО · Полная тестовая неделя"
REQUIRED_IDS = {
    "e2e-english-56-a", "e2e-english-56-b", "e2e-digital-78",
    "e2e-event", *(f"e2e-elective-{index}" for index in range(1, 5)),
}


def main() -> None:
    if PROFILE != "manual" or not DB_PATH.is_file() or not MARKER.is_file() or MARKER.read_text(encoding="utf-8") != MARKER_CONTENT:
        raise RuntimeError("Demo creation requires the marked manual .local-test database")
    database = Database(str(DB_PATH), None)
    drafts = list_experiments(database)
    existing = [draft for draft in drafts if draft["name"] == NAME]
    if existing:
        if len(existing) != 1 or existing[0]["draft_kind"] != "demo":
            raise RuntimeError("A conflicting demo name already exists; no data was changed")
        print(json.dumps({"id": existing[0]["id"], "name": NAME, "already_exists": True}, ensure_ascii=False))
        return

    with database.connection() as connection:
        group_names = {str(row["id"]): str(row["name"]) for row in database.execute(connection, "SELECT id,name FROM groups").fetchall()}
    candidates = []
    for summary in drafts:
        if summary["draft_kind"] != "test" or not summary["name"].startswith("E2E filled week "):
            continue
        draft = get_experiment(database, summary["id"])
        lessons = draft["payload"].get("lessons") or []
        ids = {str(lesson.get("id") or "") for lesson in lessons}
        used_groups = {group_names.get(str(group_id), "") for lesson in lessons
                       for group_id in (lesson.get("groupIds") or [lesson.get("groupId")]) if group_id}
        math = all(f"Math 9-{letter}" in used_groups for letter in "ABC")
        if REQUIRED_IDS <= ids and math and draft["payload"].get("days") == [0, 1, 2, 3, 4]:
            candidates.append((summary["updated_at"], draft))
    if not candidates:
        raise RuntimeError("No complete synthetic source contains Math A/B/C, English, electives, digital track and event")
    source = max(candidates, key=lambda pair: pair[0])[1]
    payload = copy.deepcopy(source["payload"])
    class_ids = [next((group_id for group_id, name in group_names.items() if name == grade), "") for grade in ("5", "6")]
    if not all(class_ids):
        raise RuntimeError("Synthetic grade 5 and 6 classes are missing")
    source_teacher = next((lesson.get("teacherId") for lesson in payload["lessons"] if lesson.get("id") == "e2e-overlap-conflict-b"), "")
    payload["lessons"].append({
        "id": "demo-available-56", "day": 0, "period": 0,
        "classId": class_ids[0], "classIds": class_ids,
        "subject": "Русский язык · все свободные", "teacherId": source_teacher or "",
        "teacherIds": [source_teacher] if source_teacher else [],
        "groupId": "", "groupIds": [], "room": "Демо 56",
        "audience": {"kind": "available_slot", "class_ids": class_ids},
        "audienceMode": "canonical", "kind": "lesson",
    })
    payload["draft_kind"] = "demo"
    payload["fixture_version"] = "full-week-2026-10-v1"
    payload["reference_date"] = "2026-10-13"
    preview = experiment_audience_preview(database, payload)["lessons"]["demo-available-56"]
    if preview["status"] != "ok" or preview["count"] < 1 or preview["excluded_count"] < 1:
        raise RuntimeError(f"Dynamic remainder is not demonstrable: {preview}")
    created = create_experiment(database, name=NAME, source="empty", payload=payload)
    print(json.dumps({"id": created["id"], "name": NAME, "source_id": source["id"],
                      "lessons": len(payload["lessons"]), "dynamic_count": preview["count"],
                      "dynamic_excluded": preview["excluded_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
