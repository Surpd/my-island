"""Run the read-only canonical student projection validator against persisted data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.config import get_settings
from backend.database import Database
from backend.services.student_schedule_validation import validate_student_projections


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", required=True)
    parser.add_argument("--grades", default="")
    parser.add_argument("--output")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    # Validation is read-only and benefits from ordinary scoped read
    # connections; reconnect/autocommit is reserved for the write path.
    database = Database(settings.database_path, settings.database_url, autocommit=False)
    report = validate_student_projections(
        database,
        args.week_start,
        grades=[value.strip() for value in args.grades.split(",") if value.strip()] or None,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    if args.summary:
        print(json.dumps({key: report.get(key) for key in ("week_start", "effective_week_id", "canonical_version_id", "status", "metrics", "grade_metrics", "limitations")}, ensure_ascii=False, indent=2, default=str))
    else:
        print(payload)


if __name__ == "__main__":
    main()
