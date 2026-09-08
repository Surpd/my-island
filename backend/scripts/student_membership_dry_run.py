"""Generate the 2026-09-08 student membership reconciliation artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.config import get_settings
from backend.database import Database
from backend.services.student_membership_reconciliation import run_live_dry_run, render_plan_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only student membership reconciliation")
    parser.add_argument("--json", dest="json_path", default="docs/STUDENT_MEMBERSHIP_DRY_RUN_2026-09-08.json")
    parser.add_argument("--markdown", dest="markdown_path", default="docs/STUDENT_MEMBERSHIP_DRY_RUN_2026-09-08.md")
    args = parser.parse_args()
    settings = get_settings()
    payload = run_live_dry_run(Database(database_url=settings.database_url), settings)
    json_path = Path(args.json_path)
    markdown_path = Path(args.markdown_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    markdown_path.write_text(render_plan_markdown(payload), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path), "summary": payload["summary"], "production_writes_performed": payload["production_writes_performed"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
