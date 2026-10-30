"""Canonical room comparison and presentation helpers.

Stored schedule values remain untouched. These helpers are for comparisons and
read-only projections only.
"""
from __future__ import annotations

import re
from typing import Any


_WHITESPACE = re.compile(r"\s+")
_NUMBERED_CABINET = re.compile(
    r"^(?:(?:каб(?:инет)?\.?\s*))?(\d+[a-zа-я]?(?:[-/][a-zа-я0-9]+)?)$",
    re.IGNORECASE,
)


def display_room(value: Any) -> str:
    """Return a stable display label without rewriting unrelated room names."""
    label = _WHITESPACE.sub(" ", str(value or "").strip())
    if not label:
        return ""
    match = _NUMBERED_CABINET.fullmatch(label)
    if match:
        return match.group(1).upper()
    return label


def room_key(value: Any) -> str:
    """Return a comparison key; only known cabinet prefixes are discarded."""
    return display_room(value).casefold()
