"""Apply reviewed teacher corrections to an immutable canonical artifact.

This is a reconciliation step, not parser logic.  It may change teacher
metadata (and an explicitly reviewed activity label) but never changes student
sets, audiences, memberships, block keys, or source provenance.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from backend.scripts.build_full_current_v2 import teacher_id_for


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _source_cells(block: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    derived = block.get("derived_from") if isinstance(block.get("derived_from"), Mapping) else {}
    return {
        str(item.get("coordinate")): item
        for item in derived.get("source_cells") or []
        if isinstance(item, Mapping) and item.get("coordinate")
    }


def _teacher_by_name(teachers: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for teacher in teachers:
        key = _norm(teacher.get("display_name"))
        if key in result:
            raise ValueError(f"Teacher directory is ambiguous for {teacher.get('display_name')!r}")
        result[key] = teacher
    return result


def _set_teacher(assignment: dict[str, Any], teacher: Mapping[str, Any], *, reason: str, coordinate: str) -> None:
    teacher_id = str(teacher["id"])
    assignment["teacher_ids"] = [teacher_id]
    assignment["teachers"] = [str(teacher.get("display_name") or "")]
    metadata = dict(assignment.get("metadata") or {})
    metadata.pop("teacher_issue", None)
    metadata["teacher_resolution"] = {
        "kind": "reviewed_source_reconciliation",
        "source_cell": coordinate,
        "reason": reason,
    }
    assignment["metadata"] = metadata


def reconcile(artifact: Mapping[str, Any], manifest: Mapping[str, Any], teachers: list[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    result = copy.deepcopy(dict(artifact))
    teacher_directory = _teacher_by_name(teachers)
    overrides = manifest.get("teacher_overrides") if isinstance(manifest.get("teacher_overrides"), Mapping) else {}
    source_only = manifest.get("source_only_interpretations") if isinstance(manifest.get("source_only_interpretations"), Mapping) else {}
    touched: list[dict[str, Any]] = []
    unapplied: list[dict[str, Any]] = []
    applied_coords: set[str] = set()

    for block in (result.get("blocks") or {}).values():
        if not isinstance(block, dict):
            continue
        cells = _source_cells(block)
        for assignment in block.get("assignments") or []:
            if not isinstance(assignment, dict):
                continue
            for coordinate in assignment.get("source_cells") or []:
                coordinate = str(coordinate)
                override = overrides.get(coordinate)
                if not isinstance(override, Mapping):
                    continue
                teacher_name = _norm(override.get("teacher"))
                teacher = teacher_directory.get(teacher_name)
                if not teacher:
                    raise ValueError(f"Reviewed teacher {override.get('teacher')!r} for {coordinate} is absent from active directory")
                if override.get("activity"):
                    assignment["activity"] = str(override["activity"])
                _set_teacher(assignment, teacher, reason=str(override.get("reason") or "reviewed source correction"), coordinate=coordinate)
                touched.append({"block_key": block.get("block_key"), "source_cell": coordinate, "teacher_id": str(teacher["id"]), "teacher": teacher.get("display_name"), "activity": assignment.get("activity")})
                applied_coords.add(coordinate)
                break

            if not assignment.get("source_cells"):
                continue
            if any(str(coord) in overrides for coord in assignment.get("source_cells") or []):
                continue
            # Re-resolve aliases only for assignments that are still unresolved.
            # A persisted teacher id is already reviewed artifact data and must
            # not be replaced merely because another token appears in the raw
            # cell (for example a color/context-based resolution).
            if assignment.get("teacher_ids"):
                continue
            for coordinate in assignment.get("source_cells") or []:
                cell = cells.get(str(coordinate))
                raw = str((cell or {}).get("raw_text") or "")
                if not raw:
                    continue
                teacher_id, issue = teacher_id_for(raw, "", teachers)
                if teacher_id:
                    teacher = next(item for item in teachers if str(item.get("id")) == teacher_id)
                    _set_teacher(assignment, teacher, reason="shared teacher alias directory matched source text", coordinate=str(coordinate))
                    touched.append({"block_key": block.get("block_key"), "source_cell": str(coordinate), "teacher_id": teacher_id, "teacher": teacher.get("display_name"), "activity": assignment.get("activity"), "kind": "alias"})
                    applied_coords.add(str(coordinate))
                    break

    for coordinate, override in overrides.items():
        if coordinate not in applied_coords:
            unapplied.append({"source_cell": coordinate, "reason": "source cell is present in snapshot but has no canonical assignment"})
    for coordinate, interpretation in source_only.items():
        if coordinate not in applied_coords:
            unapplied.append({"source_cell": coordinate, "reason": "source-only interpretation retained; no canonical assignment created", "interpretation": dict(interpretation)})

    issues: list[dict[str, Any]] = []
    resolved = unresolved = optional = 0
    optional_activities = {"Творчество", "Тренинг", "Курс по выбору", "Цифровой трек", "SDEP"}
    for block in (result.get("blocks") or {}).values():
        for assignment in block.get("assignments") or []:
            if not isinstance(assignment, dict) or assignment.get("activity") == "NO_LESSON":
                continue
            ids = assignment.get("teacher_ids") or []
            if ids:
                resolved += 1
                continue
            if assignment.get("activity") in optional_activities:
                optional += 1
                continue
            unresolved += 1
            reason = str((assignment.get("metadata") or {}).get("teacher_issue") or "teacher_id=null: source does not provide a unique teacher")
            issues.append({"block_key": block.get("block_key"), "source_cells": assignment.get("source_cells"), "reason": reason})

    result["teacher_resolution"] = {
        "resolved_assignment_count": resolved,
        "unresolved_assignment_count": unresolved,
        "optional_assignment_count": optional,
        "issues": issues,
        "rule": "reviewed source-cell corrections plus shared teacher alias directory; unresolved remains null",
    }
    result.setdefault("source_metadata", {})["teacher_reconciliation_manifest"] = manifest.get("schema_version")
    result["source_metadata"]["teacher_reconciliation_source"] = manifest.get("source")
    result["source_metadata"]["teacher_reconciliation_unapplied"] = unapplied
    result["teacher_reconciliation"] = {"touched": touched, "unapplied": unapplied}
    result["parent_version_id"] = str(artifact.get("version_id") or artifact.get("parent_version_id") or "")
    result["status"] = "approved_with_exceptions" if result.get("unresolved") or issues else "approved_baseline"
    result["authoritative"] = False
    result["summary"] = dict(result.get("summary") or {})
    result["summary"]["teacher_resolved_assignments"] = resolved
    result["summary"]["teacher_unresolved_assignments"] = unresolved
    encoded = json.dumps({"parent": result["parent_version_id"], "source": result.get("source_snapshot"), "blocks": result.get("blocks")}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    result["version_id"] = hashlib.sha256(encoded).hexdigest()
    return result, {"version_id": result["version_id"], "parent_version_id": result["parent_version_id"], "touched": touched, "unapplied": unapplied, "teacher_resolution": result["teacher_resolution"]}


def make_import_identity(artifact: dict[str, Any], manifest: Mapping[str, Any]) -> str:
    """Give a corrected re-interpretation its own immutable source identity.

    The database intentionally prevents two canonical versions from sharing
    one raw source fingerprint.  Keep the raw fingerprint in provenance while
    making the reviewed reconciliation fingerprint explicit and deterministic.
    """
    source = dict(artifact.get("source_snapshot") or {})
    raw_id = str(source.get("id") or "")
    raw_fingerprint = str(source.get("fingerprint") or source.get("source_fingerprint") or "")
    manifest_fingerprint = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    derived_fingerprint = hashlib.sha256(json.dumps({"raw_fingerprint": raw_fingerprint, "manifest": manifest_fingerprint}, sort_keys=True).encode("utf-8")).hexdigest()
    source["raw_snapshot_id"] = raw_id
    source["raw_fingerprint"] = raw_fingerprint
    source["reconciliation_manifest_fingerprint"] = manifest_fingerprint
    source["id"] = f"{raw_id}|teacher-reconciliation-{manifest_fingerprint[:12]}"
    source["fingerprint"] = derived_fingerprint
    source["source_fingerprint"] = derived_fingerprint
    artifact["source_snapshot"] = source
    artifact.setdefault("source_metadata", {})["raw_source_snapshot_id"] = raw_id
    artifact["source_metadata"]["raw_source_fingerprint"] = raw_fingerprint
    artifact["source_metadata"]["reconciliation_manifest_fingerprint"] = manifest_fingerprint
    return derived_fingerprint


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply reviewed teacher corrections to a canonical artifact")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--teacher-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent-version-id", help="Persist this reviewed artifact as a child of an already imported version")
    parser.add_argument("--for-import", action="store_true", help="derive a unique immutable source identity for DB import")
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    teacher_source = json.loads(args.teacher_source.read_text(encoding="utf-8"))
    teachers = list((teacher_source.get("db") or {}).get("teachers") or [])
    corrected, report = reconcile(artifact, manifest, teachers)
    if args.parent_version_id:
        corrected["parent_version_id"] = args.parent_version_id
        encoded = json.dumps({"parent": corrected["parent_version_id"], "source": corrected.get("source_snapshot"), "blocks": corrected.get("blocks")}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        corrected["version_id"] = hashlib.sha256(encoded).hexdigest()
        report["parent_version_id"] = corrected["parent_version_id"]
        report["version_id"] = corrected["version_id"]
    if args.for_import:
        report["source_fingerprint"] = make_import_identity(corrected, manifest)
        encoded = json.dumps({"parent": corrected["parent_version_id"], "source": corrected.get("source_snapshot"), "blocks": corrected.get("blocks")}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        corrected["version_id"] = hashlib.sha256(encoded).hexdigest()
        report["version_id"] = corrected["version_id"]
    args.output.write_text(json.dumps(corrected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
