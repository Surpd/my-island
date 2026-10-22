from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.services.schedule_canonical_bootstrap import build_bootstrap_canonical


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sdep", action="append", default=[], help="Explicit weekday:grade marker observed in source, e.g. 4:10")
    args = parser.parse_args()
    corpus = json.loads(args.input.read_text(encoding="utf-8"))
    markers = []
    for value in args.sdep:
        weekday, grade = value.split(":", 1)
        markers.append((int(weekday), grade))
    artifact = build_bootstrap_canonical(corpus, explicit_sdep=markers)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(artifact["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
