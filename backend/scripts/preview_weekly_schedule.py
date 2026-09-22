"""Preview canonical weekly refresh without writing derived production state."""
from __future__ import annotations

import argparse
import json

from backend.config import get_settings
from backend.database import Database
from backend.services.canonical_weekly_refresh import preview_current_week


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only preview of one canonical weekly refresh")
    parser.add_argument("--week-start", required=True)
    args = parser.parse_args()
    settings = get_settings()
    database = Database(settings.database_path, settings.database_url, autocommit=bool(settings.database_url))
    result = preview_current_week(database, settings, args.week_start)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "preview" else 1


if __name__ == "__main__":
    raise SystemExit(main())
