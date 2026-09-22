"""Read-only reliability checks for canonical student schedule projections."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from backend.database import Database
from backend.services.canonical_schedule import (
    StudentProjectionContext,
    _assignment_group_ids,
    _assignments,
    _date_in_period,
    _grade,
    _student_memberships,
    build_student_projection_context,
    project_student,
)


def _static_issue(code: str, reason: str, **evidence: Any) -> dict[str, Any]:
    return {"code": code, "reason": reason, **evidence}


def _directory_issues(context: StudentProjectionContext, week_start: str) -> list[dict[str, Any]]:
    week_end = (date.fromisoformat(week_start) + timedelta(days=4)).isoformat()
    issues: list[dict[str, Any]] = []
    for membership in context.memberships:
        if not bool(membership.get("active")):
            continue
        if not _date_in_period(week_end, membership.get("valid_from"), None) or not _date_in_period(week_start, None, membership.get("valid_until")):
            continue
        invalid = []
        if membership.get("identity_kind") != "student":
            invalid.append("identity is missing or is not a student")
        if membership.get("identity_status") != "active":
            invalid.append("identity is not active")
        if not bool(membership.get("group_canonical")):
            invalid.append("group is missing or non-canonical")
        if invalid:
            student = context.students.get(str(membership.get("identity_id")), {})
            group = context.groups.get(str(membership.get("group_id")), {})
            issues.append(_static_issue(
                "STALE_OR_INVALID_MEMBERSHIP",
                "; ".join(invalid),
                membership_ids=[str(membership.get("id"))],
                student_id=str(membership.get("identity_id")),
                student_name=student.get("display_name"),
                class_name=student.get("class_name"),
                group_ids=[str(membership.get("group_id"))],
                group_names=[str(group.get("display_name") or group.get("name") or membership.get("group_id"))],
                valid_from=membership.get("valid_from"),
                valid_until=membership.get("valid_until"),
                final_state="EXCLUDED_FROM_MATCHING",
            ))
    return issues


def _block_integrity_issues(database: Database, context: StudentProjectionContext, week_start: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for block in context.blocks:
        patch = block.get("patch") if isinstance(block.get("patch"), Mapping) else {}
        assignments = [] if block.get("change_kind") == "cancelled" else _assignments(database, block.get("canonical_block_id"), patch)
        baseline_assignments = _assignments(database, block.get("canonical_block_id")) if block.get("canonical_block_id") is not None else []
        if block.get("change_kind") == "moved" and isinstance(patch.get("moved_to"), (list, tuple)):
            moved_to = list(patch["moved_to"])
            actual = [block.get("weekday"), block.get("start_time"), block.get("end_time"), str(block.get("grade_scope") or "")]
            expected = [moved_to[0], moved_to[1], moved_to[2], str(moved_to[3])] if len(moved_to) >= 4 else moved_to
            if actual != expected:
                issues.append(_static_issue("WRONG_EFFECTIVE_SLOT", "materialized moved block does not match moved_to", block_key=block.get("block_key"), canonical_block_id=block.get("canonical_block_id"), expected_slot=expected, effective_slot=actual, source_cells=[]))
        if block.get("change_kind") in {"replaced", "moved", "added", "weekly_only"}:
            for index, assignment in enumerate(assignments):
                activity = str(assignment.get("activity") or "")
                role = str(assignment.get("assignment_kind") or assignment.get("role") or assignment.get("kind") or "primary")
                group_ids = _assignment_group_ids(assignment)
                if activity != "NO_LESSON" and role not in {"default", "residual", "final_state", "final_unassigned", "explicit_no_lesson"} and not group_ids:
                    issues.append(_static_issue("WEEKLY_REPLACEMENT_LOST_AUDIENCE", "weekly assignment has no canonical audience", block_key=block.get("block_key"), canonical_block_id=block.get("canonical_block_id"), assignment_ids=[str(assignment.get("id") or f"patch:{index}")], source_cells=assignment.get("source_cells") or [], activity=activity, final_state="UNRESOLVED"))
                if activity != "NO_LESSON" and not assignment.get("teacher_ids"):
                    source_cells = {str(value) for value in assignment.get("source_cells") or []}
                    candidates = [
                        candidate for candidate in baseline_assignments
                        if candidate.get("teacher_ids")
                        and (
                            (source_cells and source_cells.intersection(str(value) for value in candidate.get("source_cells") or []))
                            or (_assignment_group_ids(candidate) == group_ids and str(candidate.get("activity") or "") == activity)
                        )
                    ]
                    if len(candidates) == 1:
                        issues.append(_static_issue("WEEKLY_REPLACEMENT_LOST_TEACHER", "weekly assignment lost a uniquely matching baseline teacher", block_key=block.get("block_key"), canonical_block_id=block.get("canonical_block_id"), assignment_ids=[str(assignment.get("id") or f"patch:{index}")], source_cells=assignment.get("source_cells") or [], activity=activity, baseline_teacher_ids=[str(value) for value in candidates[0].get("teacher_ids") or []], final_state="ACTIVITY"))
    return issues


def validate_student_projections(
    database: Database,
    week_start: str,
    *,
    grades: Iterable[str] | None = None,
    context: StudentProjectionContext | None = None,
) -> dict[str, Any]:
    """Validate every active student with evidence-rich, machine-readable issues."""
    selected_grades = {str(value) for value in grades or []}
    context = context or build_student_projection_context(database, week_start)
    if not context:
        return {"week_start": week_start, "status": "canonical_not_materialized", "metrics": {}, "issues": [], "students": []}

    issues = _directory_issues(context, week_start) + _block_integrity_issues(database, context, week_start)
    state_counts: Counter[str] = Counter()
    grade_states: defaultdict[str, Counter[str]] = defaultdict(Counter)
    student_results: list[dict[str, Any]] = []
    slot_claims: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    audience_outcomes: defaultdict[tuple[str, str, str, tuple[str, ...]], list[tuple[str, str, str | None]]] = defaultdict(list)

    for student_id, student in context.students.items():
        if student.get("status") != "active":
            continue
        grade = _grade(student.get("class_name"))
        if selected_grades and grade not in selected_grades:
            continue
        projection = project_student(database, student_id, week_start, context=context)
        student_results.append({
            "student_id": student_id,
            "student_name": student.get("display_name"),
            "class_name": student.get("class_name"),
            "states": dict(Counter(str(item.get("state") or "UNRESOLVED") for item in projection.get("items") or [])),
            "issue_codes": sorted({str(item.get("code") or "UNCLASSIFIED") for item in projection.get("issues") or []}),
        })
        issues.extend(projection.get("issues") or [])
        for item in projection.get("items") or []:
            state = str(item.get("state") or "UNRESOLVED")
            state_counts[state] += 1
            grade_states[grade][state] += 1
            if state == "ACTIVITY":
                slot_claims[(student_id, str(item.get("date")), str(item.get("start_time")))].append(item)
            trace = item.get("trace") if isinstance(item.get("trace"), Mapping) else {}
            referenced_groups = sorted({str(group_id) for assignment in trace.get("assignments") or [] for group_id in assignment.get("audience_group_ids") or []})
            signature = tuple(sorted(_student_memberships(context, student_id, str(item.get("date"))).intersection(referenced_groups)))
            for group_id in signature:
                audience_outcomes[(str(item.get("block_key")), str(item.get("date")), group_id, signature)].append((student_id, state, item.get("activity")))

    for (student_id, day, start_time), items in slot_claims.items():
        activities = {str(item.get("activity")) for item in items}
        if len(activities) > 1:
            student = context.students.get(student_id, {})
            issues.append(_static_issue("MULTIPLE_ACTIVITY_CLAIMS", "student has multiple projected activities in one effective slot", student_id=student_id, student_name=student.get("display_name"), class_name=student.get("class_name"), date=day, start_time=start_time, block_keys=sorted(str(item.get("block_key")) for item in items), activities=sorted(activities), final_state="UNRESOLVED"))

    for (block_key, day, group_id, signature), outcomes in audience_outcomes.items():
        distinct = {(state, activity) for _, state, activity in outcomes}
        if len(distinct) > 1:
            group = context.groups.get(group_id, {})
            issues.append(_static_issue("AUDIENCE_PROJECTION_DIVERGENCE", "students with the same applicable block memberships received different outcomes", block_key=block_key, date=day, group_ids=[group_id], group_names=[str(group.get("display_name") or group.get("name") or group_id)], membership_signature=list(signature), outcomes=[{"student_id": value[0], "state": value[1], "activity": value[2]} for value in outcomes], final_state="UNRESOLVED"))

    issue_counts = Counter(str(issue.get("code") or "UNCLASSIFIED") for issue in issues)
    return {
        "week_start": week_start,
        "effective_week_id": context.effective.get("effective_week_id"),
        "canonical_version_id": context.effective.get("version_id"),
        "status": "validated",
        "metrics": {
            "students": len(student_results),
            "student_slots": sum(state_counts.values()),
            "states": dict(state_counts),
            "issues": len(issues),
            "issues_by_code": dict(issue_counts),
            "multiple_activity_claims": issue_counts.get("MULTIPLE_ACTIVITY_CLAIMS", 0),
            "incompatible_membership_overlaps": issue_counts.get("INCOMPATIBLE_MEMBERSHIP_OVERLAP", 0),
            "unexpected_no_lesson": issue_counts.get("UNEXPECTED_NO_LESSON", 0),
        },
        "grade_metrics": {grade: dict(counts) for grade, counts in sorted(grade_states.items())},
        "issues": issues,
        "students": student_results,
        "comparison": {"mode": "source_effective_consistency", "legacy_comparison": "not_used_without_a_valid_equivalent baseline"},
        "limitations": ["When semantic_dimension is absent, exclusivity falls back to group role/type plus grade and subject; unknown instructional subjects are not asserted mutually exclusive."],
    }
