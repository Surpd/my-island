"""Build a local Sheets export artifact from the synthetic fixture only.

This uses the repository's offline export CLI. It reads the disposable SQLite
database in read-only mode and writes snapshot, plan, and batch requests under
.local-test/consolidation/export-artifact. It never contacts Google.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
DATA = (ROOT / ".local-test" / "consolidation").resolve()
DB = (DATA / "my-island-test.sqlite3").resolve()
FIXTURE = DATA / "fixture.json"
DIRECTORY = DATA / "export-directory.json"


def main() -> None:
    if DB.parent != DATA or not DB.is_file():
        raise SystemExit("Disposable consolidation fixture database is missing or outside its profile")
    if not FIXTURE.is_file():
        raise SystemExit("Synthetic fixture manifest is missing")

    # The backend harness only creates synthetic rows in this database.
    import sqlite3

    with sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        groups = [dict(row) for row in connection.execute(
            "SELECT id,name,display_name,group_type,base_class_name,subject FROM groups ORDER BY id")]
        teachers = [dict(row) for row in connection.execute(
            "SELECT id,display_name FROM identities WHERE kind='teacher' ORDER BY id")]
    DIRECTORY.write_text(json.dumps({"groups": groups, "teachers": teachers}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    output = DATA / f"export-artifact-{fixture['week_publication_draft_id'][:8]}"
    command = [sys.executable, "-m", "backend.scripts.export_schedule_sheet",
               "--draft-db", str(DB), "--draft-id", fixture["week_publication_draft_id"],
               "--directory", str(DIRECTORY), "--week-start", "2026-10-26",
               "--output", str(output), "--layout", "final-compact",
               "--sheet-id", "1810264001", "--mapping-sheet-id", "1810264002"]
    subprocess.run(command, cwd=ROOT, check=True)
    print(f"Local synthetic export artifact created at {output}")


if __name__ == "__main__":
    main()
