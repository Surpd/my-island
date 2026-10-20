"""Explicit school-editor template selection, independent of canonical history."""
from typing import Any

from backend.database import Database
from backend.services.schedule_experiments import _item


def open_active_school_template(database: Database) -> dict[str, Any]:
    # One statement observes the pointer and its draft in the same DB snapshot.
    # Opening never imports, normalizes, creates, or rewrites a template.
    with database.connection() as connection:
        row = database.execute(connection, """SELECT d.*
            FROM school_template_selection s
            JOIN schedule_experiment_drafts d ON d.id=s.draft_id
            WHERE s.scope='school'""").fetchone()
    if not row:
        raise ValueError("Основной школьный шаблон не назначен. Нужен проверенный редакторский черновик; canonical-копия автоматически не создаётся.")
    draft = _item(row)
    if (draft["payload"].get("draft_kind") != "school"
            or draft["payload"].get("week_start") or draft["payload"].get("source_template")):
        raise ValueError("Активный школьный шаблон указывает на неподходящий черновик")
    draft["already_exists"] = True
    return draft
