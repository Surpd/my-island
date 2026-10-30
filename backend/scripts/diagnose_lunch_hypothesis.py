"""Read-only in-memory lunch-audience hypothesis for the captured school draft."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from backend.services.school_experiment_workspace import EXPECTED_PROJECT, school_class_label, school_source
from backend.services.schedule_experiments import experiment_audience_preview
from backend.scripts.diagnose_constructor_readonly import (
    ROOT, SNAPSHOT, _canonical_clock, _canonical_group_metadata, _count_range,
    _lesson_grade, _read_snapshot, _read_current_active_draft,
)


def _label_code(value: Any) -> str | None:
    """Read grade and section from administrative metadata, never technical keys."""
    label = school_class_label(str(value or ""))
    label = re.sub(r"\s+класс$", "", label, flags=re.IGNORECASE)
    text = re.sub(r"[\s\-–—]", "", label).casefold()
    match = re.fullmatch(r"9([aа]|[dд])", text)
    if not match:
        return None
    letter = match.group(1)
    return "9A" if letter in {"a", "а"} else "9D"


def _school_classes(source: Any) -> dict[str, list[str]]:
    with source.connection() as connection:
        assert connection.execute("SELECT current_setting('transaction_read_only') AS mode").fetchone()["mode"] == "on"
        rows = source.execute(connection, """SELECT id,display_name,base_class_name FROM groups
            WHERE canonical IS TRUE AND group_type='class'""").fetchall()
    found: dict[str, list[str]] = {"9A": [], "9D": []}
    for row in rows:
        code = _label_code(row["base_class_name"]) or _label_code(row["display_name"])
        if code:
            found[code].append(str(row["id"]))
    return found


def _active_membership_counts(source: Any, class_ids: list[str], reference_date: str) -> dict[str, int]:
    with source.connection() as connection:
        assert connection.execute("SELECT current_setting('transaction_read_only') AS mode").fetchone()["mode"] == "on"
        rows = source.execute(connection, """SELECT m.group_id,COUNT(DISTINCT m.identity_id) AS n
            FROM memberships m JOIN identities i ON i.id=m.identity_id JOIN groups g ON g.id=m.group_id
            WHERE m.group_id IN (""" + ",".join("?" for _ in class_ids) + """)
              AND m.active IS TRUE AND m.member_role='student' AND i.kind='student' AND i.status='active'
              AND g.canonical IS TRUE AND (m.valid_from IS NULL OR m.valid_from <= ?)
              AND (m.valid_until IS NULL OR m.valid_until >= ?)
            GROUP BY m.group_id""", tuple(class_ids) + (reference_date, reference_date)).fetchall()
    return {str(row["group_id"]): int(row["n"]) for row in rows}


def _result_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {"available": False}
    return {"available": True, "status": result.get("status"), "source_count": result.get("source_count"),
            "potential_count": result.get("potential_count"), "excluded_count": result.get("excluded_count"),
            "proposed_count": result.get("proposed_count"), "count": result.get("count"),
            "blocker_count": len(result.get("blockers") or []),
            "nonzero_proposed_roster": bool(result.get("proposed_count") or 0)}


def _aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"marker_count": len(results),
            "status_counts": {status: sum(result.get("status") == status for result in results)
                              for status in sorted({str(result.get("status") or "missing") for result in results})},
            **{key: _count_range([int(result[key]) if isinstance(result.get(key), (int, float)) else None
                                  for result in results])
               for key in ("source_count", "potential_count", "excluded_count", "proposed_count", "count")},
            "blocker_count": sum(len(result.get("blockers") or []) for result in results),
            "nonzero_proposed_roster_markers": sum(bool(result.get("proposed_count") or 0) for result in results)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "data" / "schedule-constructor-lunch-hypothesis.json")
    args = parser.parse_args()

    snapshot = _read_snapshot()
    source = school_source()
    parsed = urlparse(source.database_url)
    if parsed.hostname != f"db.{EXPECTED_PROJECT}.supabase.co" and not (parsed.hostname or "").endswith(".pooler.supabase.com"):
        raise RuntimeError("Approved school source host guard failed")
    if not parsed.username or not parsed.username.endswith(f".{EXPECTED_PROJECT}"):
        raise RuntimeError("Approved school source project guard failed")

    draft = _read_current_active_draft(source, snapshot)
    if not draft.get("available"):
        raise ValueError("The active captured school draft is unavailable in the approved source")
    payload = draft["payload"]
    reference = str(payload.get("reference_date") or payload.get("week_start") or date.today().isoformat())[:10]
    groups = _school_classes(source)
    resolution = {key: {"matches": len(values)} for key, values in groups.items()}
    selected = groups["9A"] + groups["9D"]
    if any(len(groups[key]) != 1 for key in ("9A", "9D")):
        report = {"read_only": True, "source_project": EXPECTED_PROJECT, "draft_revision": draft["revision"],
                  "class_resolution": resolution, "status": "ambiguous_canonical_class_metadata"}
    else:
        membership_counts = _active_membership_counts(source, selected, reference)
        lessons = payload.get("lessons") or []
        slots = payload.get("slots") or []
        period = next((index for index, slot in enumerate(slots)
                       if str(slot.get("start") or "") == "11:50" and str(slot.get("end") or "") == "12:35"), None)
        if period is None:
            report = {"read_only": True, "source_project": EXPECTED_PROJECT, "draft_revision": draft["revision"],
                      "class_resolution": resolution, "membership_summary": {"matched_classes": len(selected),
                          "classes_with_active_students": sum(value > 0 for value in membership_counts.values()),
                          "active_student_memberships": sum(membership_counts.values())},
                      "status": "target_bell_slot_not_found"}
        else:
            baseline = experiment_audience_preview(source, payload)["lessons"]
            group_metadata = _canonical_group_metadata(source)
            historical = []
            for index, lesson in enumerate(lessons):
                try:
                    is_monday = int(lesson.get("day", lesson.get("weekday"))) == 0
                except (TypeError, ValueError):
                    is_monday = False
                if (is_monday and lesson.get("specialMark") == "lunch"
                        and _canonical_clock(lesson, payload) == "11:50-12:35"
                        and _lesson_grade(lesson, group_metadata) == "9"):
                    historical.append(baseline.get(str(lesson.get("id") or index)) or {})
            hypothetical_payload = deepcopy(payload)
            hypothetical_payload["lessons"].append({
                "id": "diagnostic-hypothetical-lunch-11-50", "day": 0, "period": period,
                "start": "11:50", "end": "12:35", "subject": "Обед", "specialMark": "lunch",
                "classIds": selected, "classId": selected[0],
                "audience": {"kind": "available_slot", "grade": "9", "class_ids": selected},
            })
            hypothetical = experiment_audience_preview(source, hypothetical_payload)["lessons"]["diagnostic-hypothetical-lunch-11-50"]
            report = {"read_only": True, "source_project": EXPECTED_PROJECT, "draft_revision": draft["revision"],
                      "draft_payload_sha256": draft["payload_sha256"], "reference_date": reference,
                      "class_resolution": resolution,
                      "membership_summary": {"matched_classes": len(selected),
                          "classes_with_active_students": sum(value > 0 for value in membership_counts.values()),
                          "active_student_memberships": sum(membership_counts.values())},
                      "interval": "11:50-12:35", "historical_unscoped_lunch": _aggregate_results(historical),
                      "hypothetical_explicit_available_slot_lunch": _result_summary(hypothetical),
                      "source_unchanged": True, "draft_copy_only": True}
    data_root = (ROOT / "data").resolve()
    report_path = args.report.resolve()
    if report_path != data_root and data_root not in report_path.parents:
        parser.error("report output must stay inside the ignored data directory")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"diagnostic_failed": type(error).__name__, "sqlstate": getattr(error, "sqlstate", None)}), flush=True)
        raise SystemExit(1)
