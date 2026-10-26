"""Read-only school checks; emit aggregates, never names, IDs or result values.

Run only after verifying .project-identity.json against the connected project.
This command does not authenticate a browser or substitute for API/UI checks.
"""
from __future__ import annotations

import argparse
import json

from backend.services.school_experiment_workspace import school_source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week-start", default="2026-09-21")
    args = parser.parse_args()
    from datetime import date, timedelta
    from backend.services.canonical_schedule import project_student_range, project_teacher_range

    start = date.fromisoformat(args.week_start)
    if start.weekday() != 0:
        parser.error("week-start must be Monday")
    finish = (start + timedelta(days=6)).isoformat()
    source = school_source()
    students = source.list_preview_people("student")
    teachers = source.list_preview_people("teacher")
    with source.connection() as connection:
        assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        accountless = {str(row["id"]) for row in connection.execute("""SELECT i.id FROM identities i
            WHERE i.kind='student' AND i.status='active'
              AND NOT EXISTS (SELECT 1 FROM users u WHERE u.identity_id=i.id)
              AND NOT EXISTS (SELECT 1 FROM account_identity_links l WHERE l.identity_id=i.id AND l.status='confirmed')""").fetchall()}
    print(json.dumps({"read_only": True, "student_catalog": len(students), "teacher_catalog": len(teachers)}), flush=True)
    cases = []
    for marker in "ABC":
        person = next((person for person in students if str(person.get("class_name") or "").startswith("9")
                       and any(f"группа {marker} " in group["name"] and "Математика" in group["name"]
                               for group in person["groups"])), None)
        if person:
            cases.append((f"grade9_math_{marker}", person))
    other = next((person for person in students if str(person.get("class_name") or "").startswith("7")), None)
    if other:
        cases.append(("other_grade", other))
    selected = {person["identity_id"] for _, person in cases}
    peer = next((person for person in students if person["identity_id"] in accountless
                 and person["identity_id"] not in selected), None)
    if peer:
        cases.append(("without_telegram", peer))
    print(json.dumps({"distinct_student_cases": len({person["identity_id"] for _, person in cases})}), flush=True)
    for label, person in cases:
        identity = person["identity_id"]
        profile = source.get_people_library_person(identity)
        journals = source.student_journals(identity)
        diagnostics = source.preview_journal_diagnostics(identity)
        schedule = project_student_range(source, identity, start.isoformat(), finish)
        assessment_scope_valid = all(
            {str(result["assessment_id"]) for result in item["results"]}
            <= {str(assessment["id"]) for assessment in item["assessments"]}
            for item in journals
        )
        print(json.dumps({"case": label, "profile_matches": bool(profile and str(profile["id"]) == identity
                              and profile["display_name"] == person["display_name"]),
                          "groups": len(person["groups"]), "accountless": identity in accountless,
                          "journals": len(journals), "results": sum(len(item["results"]) for item in journals),
                          "mapping_state": diagnostics["mapping_state"], "assessment_scope_valid": assessment_scope_valid,
                          "schedule_state": schedule["status"], "schedule_items": len(schedule["items"]),
                          "schedule_issues": len(schedule["issues"])}), flush=True)
    selected_teachers = sorted(teachers, key=lambda person: len(person["groups"]), reverse=True)[:2]
    for index, person in enumerate(selected_teachers):
        identity = person["identity_id"]
        principal = f"identity:{identity}"
        groups = source.list_teacher_groups(principal)
        journals = source.teacher_journal_sources(principal)
        schedule = project_teacher_range(source, identity, start.isoformat(), finish)
        assigned = {str(group["id"]) for group in groups}
        outsider = next((group["id"] for student in students for group in student["groups"]
                         if str(group["id"]) not in assigned), None)
        print(json.dumps({"case": f"teacher_{index + 1}", "groups": len(groups), "journals": len(journals),
                          "foreign_group_denied": outsider is not None and not source.teacher_can_access_group(principal, outsider),
                          "schedule_state": schedule["status"], "schedule_items": len(schedule["items"]),
                          "schedule_issues": len(schedule["issues"])}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Database exception strings may contain source values. Keep the CLI's
        # report safe for sharing while retaining a useful diagnostic category.
        print(json.dumps({"smoke_failed": type(error).__name__, "sqlstate": getattr(error, "sqlstate", None)}), flush=True)
        raise SystemExit(1)
