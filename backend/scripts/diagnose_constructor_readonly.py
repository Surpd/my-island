"""Read-only aggregate diagnostics for the saved school constructor draft.

Reads the captured draft snapshot and the current school source,
using transaction-read-only PostgreSQL connections. Reports contain counts and
hashes only: no student/teacher names, IDs, lesson titles, or room values.

Example initial capture:
  python -m backend.scripts.diagnose_constructor_readonly --report data/schedule-constructor-diagnostics-initial.json

Compare a later capture with the initial state:
  python -m backend.scripts.diagnose_constructor_readonly --compare data/schedule-constructor-diagnostics-initial.json --report data/schedule-constructor-diagnostics-final.json
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from backend.services.school_experiment_workspace import EXPECTED_PROJECT, school_source
from backend.services.schedule_experiments import _lesson_audience, experiment_audience_preview


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "schedule-constructor-before-20261030.json"
STAGE = "startup"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _payload_fingerprint(payload: Mapping[str, Any]) -> str:
    return _sha(payload)


def _read_snapshot() -> dict[str, Any]:
    if not SNAPSHOT.is_file():
        raise FileNotFoundError("The expected pre-change constructor snapshot is missing")
    value = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("drafts"), list) or not value["drafts"]:
        raise ValueError("The constructor snapshot has no draft records")
    return value


def _read_current_active_draft(source: Any, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    selection = snapshot.get("selection") or []
    selected_id = next((str(row.get("draft_id")) for row in selection
                        if row.get("scope") == "school" and row.get("draft_id")), None)
    if not selected_id:
        return {"available": False, "reason": "snapshot_has_no_active_school_draft_id"}
    with source.connection() as connection:
        assert connection.execute("SELECT current_setting('transaction_read_only') AS mode").fetchone()["mode"] == "on"
        exists = source.execute(connection, "SELECT to_regclass('public.schedule_experiment_drafts') AS name").fetchone()
        if not exists or not exists["name"]:
            return {"available": False, "reason": "source_draft_table_missing"}
        row = source.execute(connection, "SELECT revision,payload FROM schedule_experiment_drafts WHERE id=?",
                             (selected_id,)).fetchone()
    if not row:
        return {"available": False, "reason": "selected_draft_not_found_in_source"}
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {"available": True, "revision": int(row["revision"]), "payload": payload,
            "payload_sha256": _payload_fingerprint(payload)}


def _canonical_group_metadata(source: Any) -> dict[str, dict[str, Any]]:
    with source.connection() as connection:
        assert connection.execute("SELECT current_setting('transaction_read_only') AS mode").fetchone()["mode"] == "on"
        rows = source.execute(connection, """SELECT id,name,display_name,base_class_name,group_type
            FROM groups WHERE canonical IS TRUE""").fetchall()
    return {str(row["id"]): dict(row) for row in rows}


def _grade(value: Any) -> str | None:
    text = str(value or "").strip()
    technical = re.match(r"^grade(\d{1,2}):base:", text, re.IGNORECASE)
    if technical:
        return technical.group(1)
    match = re.match(r"^(\d{1,2})(?!\d)", text)
    return match.group(1) if match else None


def _lesson_grade(lesson: Mapping[str, Any], groups: Mapping[str, Mapping[str, Any]]) -> str | None:
    rule = _lesson_audience(lesson)
    grade = _grade(rule.get("grade") or lesson.get("grade_scope"))
    if grade:
        return grade
    ids = list(rule.get("class_ids") or []) + list(rule.get("group_ids") or [])
    ids += list(lesson.get("classIds") or [])
    if lesson.get("classId"):
        ids.append(lesson["classId"])
    grades = set()
    for raw in ids:
        key = str(raw)
        value = _grade(key)
        group = groups.get(key)
        if group:
            value = value or _grade(group.get("base_class_name")) or _grade(group.get("display_name")) or _grade(group.get("name"))
        if value:
            grades.add(value)
    return next(iter(grades)) if len(grades) == 1 else None


def _classify(lesson: Mapping[str, Any]) -> str:
    rule = _lesson_audience(lesson)
    kind = str(rule.get("kind") or "")
    if lesson.get("specialMark") == "lunch":
        return "lunch"
    if kind in {"available_slot", "legacy_remaining"} or lesson.get("audienceMode") == "remainder":
        return "remaining"
    if kind in {"classes", "groups"}:
        return "class"
    return "other"


def _reason_category(reason: Any) -> str:
    text = str(reason or "")
    categories = (
        ("Не найдены подходящие canonical группы", "invalid_canonical_group_reference"),
        ("Ученики не найдены", "missing_student_reference"),
        ("Не выбран исходный класс или параллель", "missing_source_class"),
        ("Не выбраны группы или классы", "empty_group_or_class_selection"),
        ("Неизвестен состав групп или классов", "unknown_group_or_class_roster"),
        ("Неизвестен состав групп разбиения", "unknown_partition_roster"),
        ("В исходном остатке не указано разбиение", "missing_remaining_partition"),
        ("У занятия не указана аудитория", "missing_audience"),
        ("Не задан корректный временной интервал", "invalid_time_interval"),
        ("Состав пересекающегося занятия неизвестен", "overlapping_audience_unresolved"),
        ("Пересекаются исходные аудитории двух занятий", "overlapping_remaining_audiences"),
    )
    return next((label for prefix, label in categories if text.startswith(prefix)), "other_unresolved_reason")


def _canonical_clock(lesson: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    start, end = lesson.get("start"), lesson.get("end")
    if not start or not end:
        slots = payload.get("slots") or []
        try:
            slot = slots[int(lesson.get("period"))]
            start, end = start or slot.get("start"), end or slot.get("end")
        except (IndexError, TypeError, ValueError):
            pass
    return f"{start or '?'}-{end or '?'}"


def _count_range(values: list[int | None]) -> dict[str, int | None]:
    known = [value for value in values if value is not None]
    return {"known": len(known), "unknown": len(values) - len(known),
            "sum": sum(known) if known else None,
            "min": min(known) if known else None, "max": max(known) if known else None}


def _audience_summary(payload: Mapping[str, Any], source: Any, groups: Mapping[str, Mapping[str, Any]], monday_grade: str) -> dict[str, Any]:
    preview = experiment_audience_preview(source, payload)
    lessons = payload.get("lessons") or []
    statuses: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    needs_recalculation = 0
    no_proposal = 0
    proposed_nonzero = 0
    reasons_by_status: dict[str, Counter[str]] = {}
    targeted: dict[str, Counter[str]] = {key: Counter() for key in ("lunch", "remaining", "class")}
    targeted_intervals: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, lesson in enumerate(lessons):
        lesson_id = str(lesson.get("id") or index)
        result = preview["lessons"].get(lesson_id) or {}
        status = str(result.get("status") or "missing")
        statuses[status] += 1
        kinds[str(_lesson_audience(lesson).get("kind") or "unresolved")] += 1
        needs_recalculation += int(bool(result.get("needs_recalculation")))
        no_proposal += int(result.get("proposed_student_ids") is None)
        proposed_nonzero += int(bool(result.get("proposed_count") or 0))
        if result.get("reason"):
            reasons_by_status.setdefault(status, Counter())[_reason_category(result["reason"])] += 1
        raw_day = lesson.get("day", lesson.get("weekday"))
        try:
            is_monday = int(raw_day) == 0
        except (TypeError, ValueError):
            is_monday = False
        if is_monday and _lesson_grade(lesson, groups) == monday_grade:
            category = _classify(lesson)
            if category in targeted:
                targeted[category][status] += 1
                interval = (_canonical_clock(lesson, payload), category, status)
                bucket = targeted_intervals.setdefault(interval, {"lessons": 0, "source_count": [], "count": [],
                                                                    "excluded_count": [], "proposed_count": []})
                bucket["lessons"] += 1
                for field in ("source_count", "count", "excluded_count", "proposed_count"):
                    value = result.get(field)
                    bucket[field].append(int(value) if isinstance(value, (int, float)) else None)
    return {"lesson_count": len(lessons), "audience_status_counts": dict(sorted(statuses.items())),
            "audience_kind_counts": dict(sorted(kinds.items())), "needs_recalculation": needs_recalculation,
            "without_proposed_roster": no_proposal, "nonzero_proposed_roster_count": proposed_nonzero,
            "unresolved_reason_category_counts": {status: dict(sorted(counts.items()))
                                                  for status, counts in sorted(reasons_by_status.items())},
            "monday_grade_counts": {category: dict(sorted(counts.items()))
                                    for category, counts in targeted.items()},
            "monday_grade_interval_counts": [
                {"interval": key[0], "category": key[1], "status": key[2], "lessons": value["lessons"],
                 **{field: _count_range(value[field]) for field in ("source_count", "count", "excluded_count", "proposed_count")}}
                for key, value in sorted(targeted_intervals.items())]}


def _table_snapshot(source: Any) -> dict[str, Any]:
    with source.connection() as connection:
        assert connection.execute("SELECT current_setting('transaction_read_only') AS mode").fetchone()["mode"] == "on"
        table_names = {str(row["table_name"]) for row in source.execute(connection, """SELECT table_name
            FROM information_schema.tables WHERE table_schema=current_schema()
              AND table_name IN ('schedule_publication_heads','schedule_constructor_publications')""").fetchall()}
        heads: list[dict[str, Any]] = []
        publications: list[dict[str, Any]] = []
        if "schedule_publication_heads" in table_names:
            heads = [dict(row) for row in source.execute(connection, """SELECT scope_key,published_id,version_id,revision,updated_at
                FROM schedule_publication_heads ORDER BY scope_key""").fetchall()]
        if "schedule_constructor_publications" in table_names:
            publications = [dict(row) for row in source.execute(connection, """SELECT id,draft_id,draft_revision,scope_kind,scope_key,
                base_version_id,previous_published_id,published_id,version_id,payload_fingerprint,actor_user_id,created_at,metadata
                FROM schedule_constructor_publications ORDER BY scope_key,created_at,id""").fetchall()]
    return {"tables_present": sorted(table_names), "head_count": len(heads),
            "heads_sha256": _sha(heads), "head_rows_sha256": [_sha(row) for row in heads],
            "publication_count": len(publications), "publications_sha256": _sha(publications),
            "publication_rows_sha256": [_sha(row) for row in publications]}


def _canonical_state(source: Any) -> dict[str, Any]:
    with source.connection() as connection:
        assert connection.execute("SELECT current_setting('transaction_read_only') AS mode").fetchone()["mode"] == "on"
        versions = [dict(row) for row in source.execute(connection, """SELECT version_id,source_snapshot_id,source_fingerprint,status,
            parent_version_id,artifact_schema_version,metadata,created_at,approved_at
            FROM canonical_schedule_versions ORDER BY version_id""").fetchall()]
        blocks = [dict(row) for row in source.execute(connection, """SELECT version_id,block_key,weekday,start_time,end_time,grade_scope,
            status,mode,structural_fingerprint,source_provenance,evidence,unresolved
            FROM canonical_schedule_blocks ORDER BY version_id,weekday,start_time,block_key""").fetchall()]
        weeks = [dict(row) for row in source.execute(connection, """SELECT effective_week_id,version_id,week_start,status,
            overlay_source_snapshot_id,provenance,created_at,revision_at
            FROM canonical_effective_weeks ORDER BY week_start,effective_week_id""").fetchall()]
        effective_blocks = [dict(row) for row in source.execute(connection, """SELECT effective_week_id,canonical_block_id,block_key,
            change_kind,patch FROM canonical_effective_blocks ORDER BY effective_week_id,block_key""").fetchall()]
    blocks_by_week: dict[str, list[dict[str, Any]]] = {}
    for block in effective_blocks:
        blocks_by_week.setdefault(str(block["effective_week_id"]), []).append(block)
    week_summaries = [{"week_start": str(week["week_start"]), "status": str(week["status"]),
                       "block_count": len(blocks_by_week.get(str(week["effective_week_id"]), [])),
                       "blocks_sha256": _sha(blocks_by_week.get(str(week["effective_week_id"]), [])),
                       "week_sha256": _sha(week)} for week in weeks]
    latest = max(week_summaries, key=lambda item: item["week_start"], default=None)
    return {"canonical_version_count": len(versions),
            "canonical_version_status_counts": dict(sorted(Counter(str(row["status"]) for row in versions).items())),
            "canonical_versions_sha256": _sha(versions), "canonical_block_count": len(blocks),
            "canonical_blocks_sha256": _sha(blocks), "effective_week_count": len(weeks),
            "effective_weeks_sha256": _sha(weeks), "effective_block_count": len(effective_blocks),
            "effective_blocks_sha256": _sha(effective_blocks), "effective_week_summaries": week_summaries,
            "latest_materialized_week": latest["week_start"] if latest else None}


def _projection_checks(source: Any, week_start: str | None) -> dict[str, Any]:
    from backend.services.canonical_schedule import project_student, project_teacher

    with source.connection() as connection:
        assert connection.execute("SELECT current_setting('transaction_read_only') AS mode").fetchone()["mode"] == "on"
        student = source.execute(connection, """SELECT id FROM identities WHERE kind='student' AND status='active'
            AND class_name ~ '^9' ORDER BY id LIMIT 1""").fetchone()
        teacher = source.execute(connection, """SELECT i.id FROM identities i WHERE i.kind='teacher' AND i.status='active'
            AND EXISTS (SELECT 1 FROM teacher_assignments a WHERE a.teacher_identity_id=i.id AND a.active IS TRUE)
            ORDER BY i.id LIMIT 1""").fetchone()
    output: dict[str, Any] = {"week_start": week_start}
    if week_start is None:
        return {**output, "status": "no_materialized_week"}
    for kind, row, projector in (("student", student, project_student), ("teacher", teacher, project_teacher)):
        if not row:
            output[kind] = {"available": False}
            continue
        result = projector(source, str(row["id"]), week_start)
        output[kind] = {"available": True, "status": result.get("status"),
                        "item_count": len(result.get("items") or []), "issue_count": len(result.get("issues") or [])}
    return output


def _saved_source_state(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    heads = snapshot.get("publication_heads") or []
    publications = snapshot.get("publications") or []
    return {"head_count": len(heads), "heads_sha256": _sha(heads),
            "publication_count": len(publications), "publications_sha256": _sha(publications)}


def main() -> None:
    global STAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "data" / "schedule-constructor-diagnostics-initial.json")
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--week-start", help="Monday to use for projections; defaults to latest materialized canonical week")
    parser.add_argument("--grade", default="9")
    args = parser.parse_args()
    if args.week_start:
        try:
            week = date.fromisoformat(args.week_start)
        except ValueError as error:
            parser.error("week-start must be an ISO date")
        if week.weekday() != 0:
            parser.error("week-start must be Monday")

    STAGE = "snapshot"
    snapshot = _read_snapshot()
    saved_drafts = snapshot["drafts"]
    before = saved_drafts[0]
    if not isinstance(before.get("payload"), dict):
        raise ValueError("Snapshot draft payload is not an object")
    STAGE = "school_source"
    source = school_source()
    parsed = urlparse(source.database_url)
    if parsed.hostname != f"db.{EXPECTED_PROJECT}.supabase.co" and not (parsed.hostname or "").endswith(".pooler.supabase.com"):
        raise RuntimeError("Approved school source host guard failed")
    if not parsed.username or not parsed.username.endswith(f".{EXPECTED_PROJECT}"):
        raise RuntimeError("Approved school source project guard failed")
    STAGE = "canonical_groups"
    groups = _canonical_group_metadata(source)
    STAGE = "current_active_draft"
    current_draft = _read_current_active_draft(source, snapshot)
    STAGE = "canonical_state"
    canonical_state = _canonical_state(source)
    projection_week = args.week_start or canonical_state["latest_materialized_week"]

    STAGE = "snapshot_audience_preview"
    snapshot_audiences = _audience_summary(before["payload"], source, groups, args.grade)
    STAGE = "publication_snapshot"
    publications = _table_snapshot(source)
    STAGE = "projections"
    projections = _projection_checks(source, projection_week)
    report: dict[str, Any] = {
        "read_only": True,
        "source_project": EXPECTED_PROJECT,
        "snapshot": {"file": SNAPSHOT.name, "draft_count": len(saved_drafts),
                     "draft_revision": int(before.get("revision") or 0),
                     "payload_sha256": _payload_fingerprint(before["payload"])},
        "current_active_draft": {key: value for key, value in current_draft.items() if key != "payload"},
        "snapshot_audiences": snapshot_audiences,
        "school_source_publications": publications,
        "snapshot_publications": _saved_source_state(snapshot),
        "canonical_state": canonical_state,
        "projections": projections,
    }
    if current_draft.get("available") and isinstance(current_draft.get("payload"), dict):
        report["snapshot_matches_current_draft"] = (
            report["snapshot"]["draft_revision"] == current_draft["revision"]
            and report["snapshot"]["payload_sha256"] == current_draft["payload_sha256"]
        )
        STAGE = "current_audience_preview"
        report["current_active_audiences"] = _audience_summary(current_draft["payload"], source, groups, args.grade)
    else:
        report["current_active_audiences"] = {"available": False, "reason": current_draft.get("reason")}

    if args.compare:
        previous = json.loads(args.compare.read_text(encoding="utf-8"))
        before_source = previous.get("school_source_publications") or {}
        now_source = report["school_source_publications"]
        before_draft = previous.get("current_active_draft") or {}
        now_draft = report["current_active_draft"]
        report["comparison"] = {
            "compared_with": args.compare.name,
            "draft_unchanged": (before_draft.get("revision"), before_draft.get("payload_sha256"))
                == (now_draft.get("revision"), now_draft.get("payload_sha256")),
            "publication_heads_unchanged": (before_source.get("head_count"), before_source.get("heads_sha256"))
                == (now_source.get("head_count"), now_source.get("heads_sha256")),
            "publications_unchanged": (before_source.get("publication_count"), before_source.get("publications_sha256"))
                == (now_source.get("publication_count"), now_source.get("publications_sha256")),
            "canonical_versions_unchanged": (previous.get("canonical_state") or {}).get("canonical_versions_sha256") == canonical_state.get("canonical_versions_sha256"),
            "canonical_blocks_unchanged": (previous.get("canonical_state") or {}).get("canonical_blocks_sha256") == canonical_state.get("canonical_blocks_sha256"),
            "effective_weeks_unchanged": (previous.get("canonical_state") or {}).get("effective_weeks_sha256") == canonical_state.get("effective_weeks_sha256"),
            "effective_blocks_unchanged": (previous.get("canonical_state") or {}).get("effective_blocks_sha256") == canonical_state.get("effective_blocks_sha256"),
        }

    STAGE = "write_report"
    data_root = (ROOT / "data").resolve()
    resolved_report = args.report.resolve()
    if resolved_report != data_root and data_root not in resolved_report.parents:
        parser.error("report output must stay inside the ignored data directory")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    resolved_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Suppress SQL exception details because they can include source values.
        print(json.dumps({"diagnostic_failed": type(error).__name__, "stage": STAGE,
                          "sqlstate": getattr(error, "sqlstate", None)}), flush=True)
        raise SystemExit(1)
