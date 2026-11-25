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



def journal_overview(database, *, grade=None, subject=None, term_number=None, academic_year=None,
                     group_marker=None, start=None, end=None):
    """Read coverage for the selected sources in one database snapshot."""
    filters = ["excluded_from_journals IS NOT TRUE"]
    params = []
    for column, value in (("grade", grade), ("subject", subject), ("term_number", term_number),
                          ("academic_year", academic_year)):
        if value is not None:
            filters.append(column + "=?")
            params.append(value)
    marker_filter = ""
    if group_marker is not None:
        marker_filter = " AND student.group_marker=?"
        params.append(group_marker)
    work_filters = ["assessment.active IS TRUE"]
    for operand, value in ((">=", start), ("<=", end)):
        if value:
            work_filters.append("assessment.assessed_on" + operand + "?")
            params.append(value.isoformat())
    if group_marker is not None:
        work_filters.append("assessment.group_marker IN ('all',?)")
        params.append(group_marker)
    query = f"""
        WITH sources AS (
            SELECT id,grade,subject,academic_year,term_number,period_status,status,last_synced_at,last_error_code
            FROM journal_sources WHERE {' AND '.join(filters)}
        ), roster AS (
            SELECT student.id, student.source_id, student.group_marker
            FROM journal_students student JOIN sources ON sources.id=student.source_id
            WHERE student.active IS TRUE{marker_filter}
        ), roster_sizes AS (
            SELECT source_id, group_marker, count(*) AS members FROM roster GROUP BY source_id,group_marker
        ), works AS (
            SELECT assessment.id,assessment.source_id,assessment.group_marker
            FROM journal_assessments assessment JOIN sources ON sources.id=assessment.source_id
            WHERE {' AND '.join(work_filters)}
        ), eligible AS (
            SELECT works.source_id, sum(roster_sizes.members) AS cells FROM works
            JOIN roster_sizes ON roster_sizes.source_id=works.source_id
              AND (works.group_marker='all' OR works.group_marker=roster_sizes.group_marker)
            GROUP BY works.source_id
        ), filled AS (
            SELECT works.source_id, count(result.numeric_score) AS numeric_results,
              count(CASE WHEN result.numeric_score IS NULL AND result.status IS NOT NULL AND result.status<>'' THEN 1 END) AS status_only
            FROM journal_results result JOIN works ON result.assessment_id=works.id
            JOIN roster ON roster.id=result.journal_student_id AND roster.source_id=works.source_id
              AND (works.group_marker='all' OR works.group_marker=roster.group_marker)
            WHERE result.is_current IS TRUE GROUP BY works.source_id
        )
        SELECT sources.*, eligible.cells, filled.numeric_results, filled.status_only
        FROM sources LEFT JOIN eligible ON eligible.source_id=sources.id
        LEFT JOIN filled ON filled.source_id=sources.id ORDER BY grade,subject,academic_year,term_number,id
    """
    with database.connection() as connection:
        rows = database.execute(connection, query, tuple(params)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        observed = bool(item["last_synced_at"] or item["status"] in {"ready", "empty"})
        cells = int(item.pop("cells") or 0)
        scores = int(item["numeric_results"] or 0)
        statuses = int(item["status_only"] or 0)
        item.update(eligible_cells=cells if observed else None, numeric_results=scores if observed else None,
                    status_only=statuses if observed else None, empty_cells=cells-scores-statuses if observed else None,
                    numeric_coverage=scores/cells if observed and cells else None)
        items.append(item)
    return {"source_count": len(items), "items": items}
