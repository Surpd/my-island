"""One-time, verified editor-template transfer. Default is read-only dry run.

Uses the approved local connection without logging credentials. No canonical or
directory writes; original payload and revision are retained without cleaning.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from backend.database import Database
from backend.services.school_experiment_workspace import school_source


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def references(payload):
    result = {"groups": set(), "teachers": set(), "students": set()}
    group_keys = {"classes", "classId", "classIds", "groupId", "groupIds", "class_ids", "group_ids", "partition_group_ids", "class_group_id"}
    teacher_keys = {"teacherId", "teacherIds", "teacher_ids"}
    student_keys = {"student_ids", "studentIds", "confirmedParticipantIds", "confirmed_participant_ids", "excluded_student_ids"}

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                category = "groups" if key in group_keys else "teachers" if key in teacher_keys else "students" if key in student_keys else None
                if category:
                    values = item if isinstance(item, list) else [item]
                    result[category].update(str(v) for v in values if v and isinstance(v, (str, int)))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(payload)
    return result


def validate(connection, database, source):
    payload = source["payload"]
    lessons = payload.get("lessons") or []
    ids = [item.get("id") for item in lessons]
    errors = []
    if source.get("id") != "4dc886ce-db12-4faf-bbc7-e614a2fce673" or source.get("revision") != 45:
        errors.append("Original draft ID/revision differs")
    if len(ids) != 386 or len(set(ids)) != 386 or not all(ids):
        errors.append("Expected exactly 386 unique original IDs")
    if sum(item.get("specialMark") == "lunch" for item in lessons) != 49:
        errors.append("Expected 49 original lunch marks")
    if payload.get("import_review", {}).get("status") != "source_led_applied":
        errors.append("Expected original source-led editor payload")
    if payload.get("draft_kind") != "school" or payload.get("week_start") or payload.get("source_template"):
        errors.append("Source is not a school template")
    provenance = payload.get("school_source", {})
    if (provenance.get("sheet_id") != "638874869" or provenance.get("sheet_title") != "2026/27 шаблон"
            or provenance.get("spreadsheet_id") != "1j5dI2VbT0RoW2A5ZkCpMYVdt7cSY4ZjNxQaMWtu--Fo"):
        errors.append("Original Google provenance differs")
    refs = references(payload)
    groups = {str(r["id"]): dict(r) for r in database.execute(connection, "SELECT id,name,display_name,group_type,canonical,archived_at FROM groups").fetchall()}
    identities = {str(r["id"]): str(r["kind"]) for r in database.execute(connection, "SELECT id,kind FROM identities").fetchall()}
    for category in refs:
        known = set(groups) if category == "groups" else {key for key, kind in identities.items() if kind == ("teacher" if category == "teachers" else "student")}
        missing = sorted(refs[category] - known)
        if missing:
            errors.append({"missing_" + category: missing})
    class_refs = set(map(str, payload.get("classes", [])))
    invalid_classes = sorted(key for key in class_refs if key in groups and (groups[key]["group_type"] != "class" or not groups[key]["canonical"]))
    if invalid_classes:
        errors.append({"invalid_classes": invalid_classes})
    archived = sorted(key for key in refs["groups"] if key in groups and groups[key]["archived_at"])
    if archived:
        errors.append({"archived_groups": archived})
    return {"valid": not errors, "errors": errors,
            "references": {key: len(value) for key, value in refs.items()},
            "records": len(ids), "lunch": 49, "other_records": len(ids)-49,
            "marks": dict(Counter(item.get("specialMark", "lesson") for item in lessons)),
            "semantic_payload_sha256": digest(payload)}


def restore(database, source, *, expected_sha256, apply=False):
    if digest(source["payload"]) != expected_sha256:
        raise ValueError("Source payload checksum differs from reviewed snapshot")
    with database.connection() as connection:
        if apply and database.database_url:
            if database.autocommit:
                raise ValueError("Restoration requires an atomic transaction")
            database.execute(connection, "SELECT pg_advisory_xact_lock(hashtextextended(?,0))", ("my-island:primary-school-template",))
        report = validate(connection, database, source)
        if not report["valid"] or not apply:
            return report
        selected = database.execute(connection, "SELECT * FROM school_template_selection WHERE scope='school'").fetchone()
        if selected:
            if (selected["source_draft_id"] == source["id"] and int(selected["source_revision"]) == source["revision"]
                    and selected["source_payload_sha256"] == expected_sha256):
                return {**report, "draft_id": selected["draft_id"], "already_restored": True}
            raise ValueError("A different active selection exists; refusing to overwrite it")
        new_id = str(uuid4())
        encoded = json.dumps(source["payload"], ensure_ascii=False, separators=(",", ":"))
        value_sql = "?::jsonb" if database.database_url else "?"
        database.execute(connection, f"""INSERT INTO schedule_experiment_drafts
            (id,name,base_version_id,revision,payload) VALUES (?,?,NULL,?,{value_sql})""",
            (new_id, source["name"], source["revision"], encoded))
        database.execute(connection, """INSERT INTO school_template_selection
            (scope,draft_id,source_draft_id,source_revision,source_payload_sha256)
            VALUES ('school',?,?,?,?)""", (new_id, source["id"], source["revision"], expected_sha256))
        saved = database.execute(connection, "SELECT payload FROM schedule_experiment_drafts WHERE id=?", (new_id,)).fetchone()["payload"]
        if isinstance(saved, str):
            saved = json.loads(saved)
        if saved != source["payload"]:
            raise ValueError("Stored payload differs: transaction rolled back")
        return {**report, "draft_id": new_id, "revision": source["revision"], "already_restored": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    approved = school_source()  # Validates the approved host/project; default DB is transaction-read-only.
    database = Database(database_url=approved.database_url) if args.apply else approved
    report = restore(database, source, expected_sha256=args.expected_sha256, apply=args.apply)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
