"""Exact display aliases for schedule subjects. Program and assignment IDs stay separate."""
import json
from pathlib import Path

_ALIASES = json.loads(Path(__file__).with_name("schedule_subject_aliases.json").read_text(encoding="utf-8"))


def subject_label(value):
    source = " ".join(str(value or "").split())
    return _ALIASES.get(source.casefold(), source)


def program_key(assignment):
    """Keep recorded curriculum distinctions when comparing two subject labels."""
    metadata = assignment.get("metadata") or {}
    modifiers = assignment.get("modifiers") or {}
    return tuple(str(assignment.get(key) or metadata.get(key) or modifiers.get(key) or "").casefold()
                 for key in ("classroom_course_id", "course", "program", "exam_track", "subject_subgroup"))
