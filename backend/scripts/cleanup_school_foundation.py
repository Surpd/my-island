"""Dry-run and apply the one-time School Data Foundation cleanup.

The script is intentionally conservative: it only removes currently unlinked
Telegram accounts after an inventory guard, and archives legacy schedule
snapshots/sync runs. It never identifies people by name or Telegram id.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import psycopg
from dotenv import load_dotenv


def _inventory(connection: psycopg.Connection) -> dict[str, object]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, role, created_at FROM public.users WHERE identity_id IS NULL ORDER BY created_at, id")
        unmatched = cursor.fetchall()
        cursor.execute(
            """SELECT 'identity_claims.reviewed_by', count(*) FROM public.identity_claims
               WHERE reviewed_by IN (SELECT id FROM public.users WHERE identity_id IS NULL)
               UNION ALL SELECT 'audit_log.actor_user_id', count(*) FROM public.audit_log
               WHERE actor_user_id IN (SELECT id FROM public.users WHERE identity_id IS NULL)
               UNION ALL SELECT 'identity_claims.user_id', count(*) FROM public.identity_claims
               WHERE user_id IN (SELECT id FROM public.users WHERE identity_id IS NULL)
               UNION ALL SELECT 'announcements.author_user_id', count(*) FROM public.announcements
               WHERE author_user_id IN (SELECT id FROM public.users WHERE identity_id IS NULL)
               UNION ALL SELECT 'teacher_assignments.created_by', count(*) FROM public.teacher_assignments
               WHERE created_by IN (SELECT id FROM public.users WHERE identity_id IS NULL)
               UNION ALL SELECT 'homeroom_assignments.created_by', count(*) FROM public.homeroom_assignments
               WHERE created_by IN (SELECT id FROM public.users WHERE identity_id IS NULL)
               UNION ALL SELECT 'membership_overrides.created_by', count(*) FROM public.membership_overrides
               WHERE created_by IN (SELECT id FROM public.users WHERE identity_id IS NULL)
               UNION ALL SELECT 'journal_group_mappings.created_by', count(*) FROM public.journal_group_mappings
               WHERE created_by IN (SELECT id FROM public.users WHERE identity_id IS NULL)
               UNION ALL SELECT 'student_preview.actor_user_id', count(*) FROM public.student_preview_sessions
               WHERE actor_user_id IN (SELECT id FROM public.users WHERE identity_id IS NULL)
               UNION ALL SELECT 'student_preview.target_user_id', count(*) FROM public.student_preview_sessions
               WHERE target_user_id IN (SELECT id FROM public.users WHERE identity_id IS NULL)"""
        )
        dependencies = {name: count for name, count in cursor.fetchall() if count}
        counts = {}
        for table in ("schedule_entries", "group_schedule_audiences", "schedule_syncs"):
            cursor.execute(f"SELECT count(*) FROM public.{table}")
            counts[table] = cursor.fetchone()[0]
    return {"unmatched": unmatched, "dependencies": dependencies, "schedule": counts}


def _print_inventory(inventory: dict[str, object]) -> None:
    unmatched = inventory["unmatched"]
    print(f"unlinked_accounts={len(unmatched)}")
    print("unlinked_roles=" + str(Counter(row[1] for row in unmatched)))
    print("allowed_dependency_cleanup=" + str(inventory["dependencies"]))
    print("schedule_cleanup=" + str(inventory["schedule"]))


def _backfill_group_metadata(connection: psycopg.Connection) -> None:
    values = [
        ("Математика · группа A · 9 класс", "Математика", None, "A", None, "school_structure", "grade9:math:A", "grade9-math-A", "subject_group"),
        ("Математика · группа B · 9 класс", "Математика", None, "B", None, "school_structure", "grade9:math:B", "grade9-math-B", "subject_group"),
        ("Математика · группа C · 9 класс", "Математика", None, "C", None, "school_structure", "grade9:math:C", "grade9-math-C", "subject_group"),
        ("9-Д · базовый класс", None, "9-Д", None, None, "admin_override", "confirmed_student_membership", "9-Д", "class"),
        ("9-Д · Информатика · ОГЭ", "Информатика", "9-Д", None, "ОГЭ", "admin_override", "confirmed_student_membership", "9-Д / Информатика ОГЭ", "exam_track"),
    ]
    with connection.cursor() as cursor:
        for display_name, subject, base_class, subgroup, exam_track, source, source_ref, name, group_type in values:
            cursor.execute(
                """UPDATE public.groups
                   SET display_name=%s, subject=%s, base_class_name=%s, subject_subgroup=%s,
                       exam_track=%s, provenance_source=%s, provenance_ref=%s
                 WHERE name=%s AND group_type=%s""",
                (display_name, subject, base_class, subgroup, exam_track, source, source_ref, name, group_type),
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="apply the verified cleanup in one transaction")
    parser.add_argument("--scope", choices=("accounts", "schedule", "both"), default="both")
    args = parser.parse_args()
    load_dotenv(".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    with psycopg.connect(database_url) as connection:
        inventory = _inventory(connection)
        _print_inventory(inventory)
        unmatched = inventory["unmatched"]
        dependencies = inventory["dependencies"]
        if args.scope in {"accounts", "both"} and len(unmatched) != 2:
            raise SystemExit("Refusing account cleanup: expected exactly two unlinked legacy/test accounts")
        if args.scope == "schedule" and unmatched:
            raise SystemExit("Refusing schedule cleanup while unlinked app accounts remain")
        if any(row[1] not in {"admin", "teacher"} for row in unmatched):
            raise SystemExit("Refusing cleanup: unexpected role on an unlinked account")
        unexpected = {name: count for name, count in dependencies.items() if name not in {"identity_claims.reviewed_by", "audit_log.actor_user_id"}}
        if unexpected:
            raise SystemExit(f"Refusing cleanup: unexpected foreign-key dependencies: {unexpected}")
        if not args.apply:
            print("DRY RUN: no rows changed")
            return 0
        if args.scope == "both":
            raise SystemExit("Refusing combined destructive cleanup: choose --scope accounts or --scope schedule")

        with connection.cursor() as cursor:
            if args.scope == "accounts":
                ids = tuple(row[0] for row in unmatched)
                placeholders = ", ".join("%s" for _ in ids)
                cursor.execute(f"UPDATE public.identity_claims SET reviewed_by = NULL WHERE reviewed_by IN ({placeholders})", ids)
                cursor.execute(f"UPDATE public.audit_log SET actor_user_id = NULL WHERE actor_user_id IN ({placeholders})", ids)
                cursor.execute(f"DELETE FROM public.users WHERE id IN ({placeholders})", ids)
                _backfill_group_metadata(connection)
            else:
                cursor.execute("UPDATE public.group_schedule_audiences SET archived = TRUE WHERE archived IS FALSE")
                cursor.execute("UPDATE public.schedule_entries SET archived = TRUE WHERE archived IS FALSE")
                cursor.execute("UPDATE public.schedule_syncs SET archived = TRUE WHERE archived IS FALSE")
        connection.commit()
        print(f"APPLIED scope={args.scope}; schedule rows were archived and removed from runtime reads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
