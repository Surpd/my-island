from __future__ import annotations

import argparse
import json
import sys

from backend.config import get_settings
from backend.database import Database
from backend.services.google_live import GoogleLiveError
from backend.services.google_sync_service import refresh_schedule


# Deployment scheduler contract: invoke once daily around 21:00 Europe/Moscow.
def main(week_start: str | None = None) -> int:
    settings = get_settings()
    database = Database(settings.database_path, settings.database_url)
    print(json.dumps({"mode": "schedule_daily", "result": refresh_schedule(database, settings, week_start)}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh the active Google Sheets schedule snapshot")
    parser.add_argument("--week-start", help="Monday of the target ISO week; defaults to the active/next week")
    try:
        raise SystemExit(main(parser.parse_args().week_start))
    except (GoogleLiveError, ValueError, RuntimeError) as error:
        print(f"Daily schedule sync blocked: {error}", file=sys.stderr)
        raise SystemExit(1) from error
