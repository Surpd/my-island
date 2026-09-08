"""Versioned human decisions used only by the read-only student dry-run."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_MANUAL_RESOLUTIONS_PATH = Path("docs/STUDENT_MEMBERSHIP_MANUAL_RESOLUTIONS_2026-09-08.json")


def load_manual_resolutions(path: str | Path = DEFAULT_MANUAL_RESOLUTIONS_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Student membership manual resolutions must be a version 1 object")
    return payload


def display_name_override(resolutions: dict[str, Any], display_name: str) -> dict[str, Any] | None:
    target = str(display_name).casefold().replace("ё", "е")
    for item in resolutions.get("identity_status_overrides", []):
        if str(item.get("display_name", "")).casefold().replace("ё", "е") == target:
            return item
    return None


def alias_for_source(resolutions: dict[str, Any], source_name: str, source_ref: str = "") -> dict[str, Any] | None:
    target = str(source_name).casefold().replace("ё", "е")
    for item in resolutions.get("aliases", []):
        if str(item.get("source_name", "")).casefold().replace("ё", "е") != target:
            continue
        if item.get("source_ref") and item["source_ref"] != source_ref:
            continue
        return item
    return None


def group_assignment_for_source(resolutions: dict[str, Any], source_name: str, source_ref: str = "") -> dict[str, Any] | None:
    target = str(source_name).casefold().replace("ё", "е")
    for item in resolutions.get("confirmed_group_assignments", []):
        if str(item.get("source_name", "")).casefold().replace("ё", "е") != target:
            continue
        allowed_refs = set(str(ref) for ref in item.get("source_refs", []) if ref)
        if item.get("source_ref"):
            allowed_refs.add(str(item["source_ref"]))
        if allowed_refs and source_ref not in allowed_refs:
            continue
        return item
    return None
