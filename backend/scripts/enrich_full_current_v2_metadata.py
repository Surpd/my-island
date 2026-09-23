"""Attach structured source-cell metadata to the current v2 artifact."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--artifact", type=Path, required=True); parser.add_argument("--metadata", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8")); metadata = {str(x["coordinate"]): x for x in json.loads(args.metadata.read_text(encoding="utf-8"))}
    touched = 0
    for block in (artifact.get("blocks") or {}).values():
        derived = block.get("derived_from") if isinstance(block.get("derived_from"), dict) else {}
        cells = derived.get("source_cells") or []
        for cell in cells:
            coordinate = str(cell.get("coordinate") or "") if isinstance(cell, dict) else str(cell or "")
            if coordinate in metadata:
                source = metadata[coordinate]
                cell.update({"formatted_value": source.get("formatted_value"), "effective_value": source.get("effective_value"), "background_color": source.get("background_color"), "text_format": source.get("text_format"), "note": source.get("note")})
                touched += 1
        derived["structured_cell_metadata"] = [{"coordinate": str(cell.get("coordinate") or ""), "background_color": cell.get("background_color"), "text_format": cell.get("text_format"), "note": cell.get("note")} for cell in cells]
        block["derived_from"] = derived
    artifact.setdefault("source_metadata", {})["structured_cell_metadata_cells"] = len(metadata)
    artifact["source_metadata"]["structured_cell_metadata_attached"] = touched
    # The artifact version is content-addressed; provenance enrichment is part
    # of the immutable version identity.
    import hashlib
    encoded = json.dumps({"parent": artifact.get("parent_version_id"), "source": artifact.get("source_snapshot"), "blocks": artifact.get("blocks")}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    artifact["version_id"] = hashlib.sha256(encoded).hexdigest()
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"version_id": artifact["version_id"], "metadata_cells": len(metadata), "attached": touched}, ensure_ascii=False))


if __name__ == "__main__":
    main()
