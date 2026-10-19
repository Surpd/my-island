"""Offline CLI: structured snapshot -> new-sheet Sheets batchUpdate plan.

python -m backend.scripts.export_schedule_sheet --source source.json \
    --week-start 2026-10-19 --output .local-test/schedule-export
No environment/credentials, production connections, or source draft writes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from backend.services.schedule_sheet_export import freeze_week, build_sheet_plan, sheets_requests


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--source", type=Path, help="Detached payload + groups + teachers JSON")
    sources.add_argument("--draft-db", type=Path, help="Local Schedule Studio SQLite, opened read-only")
    parser.add_argument("--draft-id", help="Exact saved draft ID; never chooses a user draft implicitly")
    parser.add_argument("--directory", type=Path, help="Local JSON with groups and teachers arrays")
    parser.add_argument("--week-start", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--title", help="New worksheet title; defaults to MI + week start")
    parser.add_argument("--layout", choices=("classic", "compact-v2", "readable-v3", "final-compact"), default="classic")
    parser.add_argument("--audience-evidence", type=Path, help="Matching cached group/class scope evidence; no live roster lookup")
    parser.add_argument("--existing-notes", type=Path, help="Prior annotations keyed by logical lesson ID")
    parser.add_argument("--sheet-id", type=int, default=1910232601)
    parser.add_argument("--mapping-sheet-id", type=int, default=1910232602)
    args = parser.parse_args()
    if args.source:
        source = json.loads(args.source.read_text(encoding="utf-8"))
    else:
        if not args.draft_id or not args.directory:
            parser.error("--draft-db requires --draft-id and --directory")
        if not args.draft_db.is_file():
            parser.error("Local draft database does not exist")
        directory = json.loads(args.directory.read_text(encoding="utf-8"))
        with sqlite3.connect(args.draft_db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT id,name,revision,payload FROM schedule_experiment_drafts WHERE id=?",
                                     (args.draft_id,)).fetchone()
        if row is None:
            parser.error("Saved draft not found")
        raw = json.loads(row["payload"])
        source = {"source": {"draft_id": row["id"], "name": row["name"], "revision": row["revision"]},
                  "payload": {k: raw[k] for k in ("days", "slots", "classes", "lessons")},
                  "groups": directory["groups"], "teachers": directory["teachers"]}
    snapshot = freeze_week(source, args.week_start)
    evidence = json.loads(args.audience_evidence.read_text(encoding="utf-8")) if args.audience_evidence else None
    notes = json.loads(args.existing_notes.read_text(encoding="utf-8")) if args.existing_notes else None
    plan = build_sheet_plan(snapshot, title=args.title or f"MI · {args.week_start}", sheet_id=args.sheet_id,
                            mapping_sheet_id=args.mapping_sheet_id, layout=args.layout, audience_evidence=evidence,
                            existing_notes=notes)
    args.output.mkdir(parents=True, exist_ok=True)
    files = {name: json.dumps(value, ensure_ascii=False, indent=2) for name, value in (
        ("snapshot.json", snapshot), ("plan.json", plan), ("requests.json", sheets_requests(plan)))}
    for name, encoded in files.items():
        target = args.output / name
        # Export snapshots are immutable. Use a new directory for another revision.
        if target.exists() and target.read_text(encoding="utf-8") != encoded:
            raise FileExistsError(f"Export already exists with different contents: {target}")
    for name, encoded in files.items():
        (args.output / name).write_text(encoded, encoding="utf-8")
    print(json.dumps(plan["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
