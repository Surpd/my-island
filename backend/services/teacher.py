from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def journal_view(rows: list[dict[str, Any]]) -> dict[str, Any]:
    assessments: dict[Any, dict[str, Any]] = {}
    students: dict[str, dict[str, Any]] = {}
    numeric_scores: list[float] = []
    status_counts: Counter[str] = Counter()
    assessment_scores: dict[Any, list[float]] = defaultdict(list)
    for row in rows:
        assessment = assessments.setdefault(row["assessment_id"], {
            "id": row["assessment_id"], "title": row["title"], "date": row.get("assessed_on"),
            "weight": row.get("weight"), "max_score": row.get("max_score"), "results": [],
        })
        result = {
            "journal_student_id": row.get("journal_student_id"),
            "identity_id": row.get("identity_id"), "student_name": row["student_name"],
            "score": row.get("numeric_score"), "status": row.get("status"),
        }
        assessment["results"].append(result)
        student = students.setdefault(row["student_name"], {
            "journal_student_id": row.get("journal_student_id"),
            "identity_id": row.get("identity_id"),
            "name": row["student_name"],
            "linked_to_app": row.get("identity_id") is not None,
            "history": [],
        })
        student["history"].append({
            "assessment_id": row["assessment_id"], "title": row["title"], "date": row.get("assessed_on"),
            "score": row.get("numeric_score"), "status": row.get("status"), "weight": row.get("weight"), "max_score": row.get("max_score"),
        })
        if row.get("numeric_score") is not None:
            score = float(row["numeric_score"])
            numeric_scores.append(score)
            assessment_scores[row["assessment_id"]].append(score)
        elif row.get("status"):
            status_counts[str(row["status"])] += 1
    for student in students.values():
        scores = [float(item["score"]) for item in student["history"] if item.get("score") is not None]
        student["average"] = round(sum(scores) / len(scores), 2) if scores else None
    analytics = {
        "average": round(sum(numeric_scores) / len(numeric_scores), 2) if numeric_scores else None,
        "result_count": len(rows),
        "numeric_count": len(numeric_scores),
        "status_distribution": dict(status_counts),
        "assessment_averages": [
            {"assessment_id": key, "average": round(sum(scores) / len(scores), 2), "count": len(scores)}
            for key, scores in assessment_scores.items() if scores
        ],
    }
    return {"assessments": list(assessments.values()), "students": list(students.values()), "analytics": analytics}
