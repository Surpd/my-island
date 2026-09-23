"""Emit a bounded JSON chunk for the safe Supabase connector importer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def audience_fields(item: dict) -> tuple[str, list[str]]:
    audience = item.get("audience") if isinstance(item.get("audience"), dict) else {}
    groups = [str(x) for x in audience.get("canonical_group_ids") or item.get("canonical_group_ids") or []]
    role = str(item.get("role") or item.get("kind") or "primary")
    if not groups and role in {"default", "residual", "final_state", "final_unassigned"}:
        kind = "complement" if role not in {"final_state", "final_unassigned"} else "remaining"
    elif role == "explicit_no_lesson":
        kind = "canonical_groups" if groups else "remaining"
    else:
        kind = str(audience.get("type") or "canonical_groups")
    metadata = {key: value for key, value in item.items() if key not in {"student_ids", "student_count", "activity", "role", "kind", "audience"}}
    metadata["audience"] = {"type": kind, "canonical_group_ids": groups}
    return kind, groups, metadata


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("artifact", type=Path); parser.add_argument("kind", choices=("blocks", "assignments")); parser.add_argument("offset", type=int); parser.add_argument("limit", type=int)
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8")); blocks = artifact.get("blocks") or {}
    if isinstance(blocks, list): blocks = {str(x.get("block_key")): x for x in blocks}
    if args.kind == "blocks":
        values = []
        for key, block in list(blocks.items())[args.offset:args.offset + args.limit]:
            slot = block.get("slot") if isinstance(block.get("slot"), dict) else {}
            derived = block.get("derived_from") if isinstance(block.get("derived_from"), dict) else {}
            values.append({"version_id": artifact["version_id"], "block_key": str(key), "weekday": block.get("weekday"), "start_time": slot.get("start"), "end_time": slot.get("end"), "grade_scope": str(block.get("grade_scope") or ""), "status": str(block.get("status") or "unresolved"), "mode": str(block.get("mode") or ""), "structural_fingerprint": str(derived.get("structural_fingerprint") or ""), "source_provenance": {"derived_from": derived, "source_cells": derived.get("source_cells") or []}, "evidence": block.get("evidence") or {}, "unresolved": block.get("unresolved") or []})
    else:
        values = []
        for key, block in list(blocks.items()):
            for ordinal, item in enumerate(block.get("assignments") or []):
                kind, groups, metadata = audience_fields(item)
                values.append({"block_key": str(key), "ordinal": ordinal, "activity": str(item.get("activity") or ""), "assignment_kind": str(item.get("role") or item.get("kind") or "primary"), "audience_kind": kind, "canonical_group_ids": groups, "source_cells": item.get("source_cells") or [], "teacher_ids": [str(x) for x in item.get("teacher_ids") or []], "metadata": metadata})
        values = values[args.offset:args.offset + args.limit]
    print(json.dumps(values, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
