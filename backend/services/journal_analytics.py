"""Aggregate the current, scoped journal matrix without reinterpreting grades."""
from collections import Counter
from datetime import date


def journal_analytics(detail: dict, start: date | None, end: date | None) -> dict:
    source = detail["source"]
    students = detail["students"]
    works = []
    undated = 0
    for assessment in detail["assessments"]:
        day = str(assessment["assessed_on"] or "")[:10]
        if not day:
            undated += 1
            if start or end:
                continue
        if (start and day < start.isoformat()) or (end and day > end.isoformat()):
            continue
        counts = Counter()
        for student in students:
            if assessment["group_marker"] not in {"all", student["group_marker"]}:
                continue
            counts["eligible_cells"] += 1
            cell = student["results"].get(str(assessment["id"])) or {}
            if cell.get("numeric_score") is not None:
                counts["numeric_results"] += 1
            elif cell.get("status"):
                counts["status_only"] += 1
            else:
                counts["empty_cells"] += 1
        works.append({
            "assessment_id": assessment["id"], "assessed_on": day or None,
            "category": assessment["category"] or None, "group_marker": assessment["group_marker"],
            **{k: counts[k] for k in ("eligible_cells", "numeric_results", "status_only", "empty_cells")},
        })

    totals = {k: sum(work[k] for work in works)
              for k in ("eligible_cells", "numeric_results", "status_only", "empty_cells")}
    eligible = totals["eligible_cells"]
    summaries = Counter(item["summary_type"] for student in students for item in student["summaries"]
                        if item["numeric_value"] is not None or item["text_value"])
    return {
        "source": {k: source[k] for k in ("id", "grade", "subject", "academic_year", "term_number",
                                           "period_status", "status", "last_synced_at", "last_error_code")},
        "roster": {
            "total_rows": len(students),
            "team_rows": sum(s["entry_kind"] == "team" for s in students),
            "unlinked_individual_rows": sum(s["entry_kind"] == "individual" and s["identity_id"] is None
                                            for s in students),
        },
        "period_summary_cells": dict(summaries),
        "undated_assessments": undated,
        "totals": {**totals, "numeric_coverage": totals["numeric_results"] / eligible if eligible else None},
        "works": works,
    }
