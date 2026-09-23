"""Import an approved canonical schedule artifact without reparsing or directory writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.config import get_settings
from backend.database import Database
from backend.services.canonical_schedule import import_canonical_artifact


def main(path: str, *, autocommit: bool = False) -> int:
    settings = get_settings()
    artifact_path = Path(path)
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    database = Database(settings.database_path, settings.database_url, autocommit=autocommit)
    result = import_canonical_artifact(database, artifact)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import an immutable canonical schedule artifact")
    parser.add_argument("artifact", nargs="?", default="docs/SCHEDULE_CANONICAL_V1_SHADOW.json")
    parser.add_argument("--pooler-autocommit", action="store_true", help="Use autocommit for a transaction-pooler connection; reruns are idempotent")
    args = parser.parse_args()
    raise SystemExit(main(args.artifact, autocommit=args.pooler_autocommit))
