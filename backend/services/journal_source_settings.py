"""Per-tab journal synchronization preferences.

The persistent identity is the Google spreadsheet ID plus sheet ID. Tab titles
are presentation data and may change without changing the source.
"""

from __future__ import annotations

from typing import Any


MODES = {"auto", "manual", "excluded"}


def source_mode(source: dict[str, Any] | None) -> str:
    if not source:
        return "manual"
    if source.get("excluded_from_journals"):
        return "excluded"
    return "auto" if source.get("auto_sync_enabled") else "manual"


def load_source_settings(database: Any, spreadsheet_id: str) -> dict[str, dict[str, Any]]:
    """Return settings indexed by stable Google sheet ID."""
    rows = database.journal_source_sync_settings(spreadsheet_id)
    return {str(row["sheet_id"]): dict(row) for row in rows if row.get("sheet_id") is not None}


def select_sync_tabs(tabs: list[dict[str, Any]], settings: dict[str, dict[str, Any]], *, trigger_kind: str) -> tuple[list[dict[str, Any]], int, int]:
    """Filter excluded tabs and scheduled tabs in manual mode before value reads."""
    selected: list[dict[str, Any]] = []
    skipped = excluded = 0
    for tab in tabs:
        sheet_id = tab.get("sheetId")
        preference = settings.get(str(sheet_id)) if sheet_id is not None else None
        mode = source_mode(preference)
        if mode == "excluded":
            excluded += 1
        elif trigger_kind == "scheduled" and mode != "auto":
            skipped += 1
        else:
            selected.append(tab)
    return selected, skipped, excluded
