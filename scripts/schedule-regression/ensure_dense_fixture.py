"""Incrementally add the synthetic dense school template to an existing fixture DB."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
DATA = (ROOT / ".local-test" / "consolidation").resolve()
DB_PATH = (DATA / "my-island-test.sqlite3").resolve()
FIXTURE_PATH = DATA / "fixture.json"


def main() -> None:
    if DB_PATH.parent != DATA or not DB_PATH.is_file() or not FIXTURE_PATH.is_file():
        raise SystemExit("Expected disposable consolidation DB and synthetic manifest under .local-test/consolidation")
    manifest = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if not manifest.get("synthetic") or manifest.get("schema") != "synthetic-consolidation-v1":
        raise SystemExit("Refusing to mutate a non-synthetic profile")
    sys.path.insert(0, str(ROOT))
    from backend.database import Database

    spec = importlib.util.spec_from_file_location("consolidation_fixture", Path(__file__).with_name("fixture.py"))
    if not spec or not spec.loader:
        raise SystemExit("Could not load synthetic fixture helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = Database(path=DB_PATH)
    updated = module.ensure_dense_template(database, manifest)
    FIXTURE_PATH.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Synthetic school template ready: {updated['counts'].get('dense_constructor_lessons', 0)} lessons")


if __name__ == "__main__":
    main()
