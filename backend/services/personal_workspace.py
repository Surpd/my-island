"""Versioned per-admin widget settings. Data requests keep their own role checks."""
import json
from datetime import date, timedelta
from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Widget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    type: Literal["operational_day", "teacher_load"]
    size: Literal["compact", "wide"] = "compact"
    filters: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_filters(self):
        allowed = REGISTRY[self.type]["filters"]
        if any(key not in allowed or not value.strip() or len(value) > 128 for key, value in self.filters.items()):
            raise ValueError("Unsupported widget filter")
        for key in ("day", "start_day", "end_day"):
            if key in self.filters:
                self.filters[key] = date.fromisoformat(self.filters[key]).isoformat()
        if "end_day" in self.filters and ("start_day" not in self.filters or self.filters["end_day"] < self.filters["start_day"]):
            raise ValueError("Invalid date interval")
        return self


class LayoutUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    expected_revision: int = Field(ge=0)
    # An empty list is a valid empty dashboard. Null explicitly restores defaults.
    widgets: list[Widget] | None = Field(max_length=24)

    @model_validator(mode="after")
    def unique_instances(self):
        ids = [w.instance_id for w in self.widgets or []]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate widget instance")
        return self


REGISTRY = {
    "operational_day": {"endpoint": "/api/admin/operations/day", "filters": ["day"]},
    "teacher_load": {"endpoint": "/api/admin/analytics/teachers", "filters": ["start_day", "end_day", "teacher_id",
                                                                            "activity_type", "activity_format", "group_id"]},
}
DEFAULTS = [{"instance_id": "today", "type": "operational_day", "size": "wide", "filters": {}},
            {"instance_id": "teachers", "type": "teacher_load", "size": "compact", "filters": {}}]


def layout_view(row, today, endpoints=None):
    catalog = {kind: entry for kind, entry in REGISTRY.items()
               if endpoints is None or entry["endpoint"] in endpoints}
    if row and row["schema_version"] != 1:
        return {"schema_version": row["schema_version"], "revision": row["revision"], "read_only": True,
                "widgets": [], "requests": {}, "catalog": catalog, "sizes": ["compact", "wide"]}
    stored = json.loads(row["widgets_json"]) if row else None
    widgets = [widget for widget in DEFAULTS if widget["type"] in catalog] if stored is None else stored
    requests = {}
    visible = []
    unavailable = []
    for widget in widgets:
        definition = REGISTRY.get(widget["type"])
        if definition is None:
            continue
        visible.append(widget)
        if widget["type"] not in catalog:
            unavailable.append(widget["instance_id"])
            continue
        params = dict(widget["filters"])
        if widget["type"] == "teacher_load" and "start_day" not in params:
            monday = today - timedelta(days=today.weekday())
            params.update(start_day=monday.isoformat(), end_day=(monday + timedelta(days=6)).isoformat())
        elif widget["type"] == "operational_day":
            params.setdefault("day", today.isoformat())
        href = definition["endpoint"] + "?" + urlencode(sorted(params.items()))
        requests.setdefault(href, []).append(widget["instance_id"])
    return {"schema_version": 1, "revision": row["revision"] if row else 0,
            "widgets": visible, "requests": requests, "unavailable_instances": unavailable, "catalog": catalog, "sizes": ["compact", "wide"]}


def read_layout(db, user_id, today, endpoints=None):
    with db.connection() as conn:
        row = db.execute(conn, "SELECT schema_version, revision, widgets_json FROM admin_widget_layouts WHERE user_id=?", (user_id,)).fetchone()
    return layout_view(row, today, endpoints)


def save_layout(db, user_id, change, today, endpoints=None):
    widgets = None if change.widgets is None else [widget.model_dump() for widget in change.widgets]
    payload = json.dumps(widgets)
    with db.connection() as conn:
        if change.expected_revision == 0:
            row = db.execute(conn, """INSERT INTO admin_widget_layouts (user_id, revision, widgets_json)
                VALUES (?,1,?) ON CONFLICT(user_id) DO NOTHING RETURNING schema_version, revision, widgets_json""",
                (user_id, payload)).fetchone()
        else:
            row = db.execute(conn, """UPDATE admin_widget_layouts SET revision=revision+1, widgets_json=?
                WHERE user_id=? AND revision=? AND schema_version=1 RETURNING schema_version, revision, widgets_json""",
                (payload, user_id, change.expected_revision)).fetchone()
        if row is None:
            current = db.execute(conn, "SELECT schema_version, revision, widgets_json FROM admin_widget_layouts WHERE user_id=?", (user_id,)).fetchone()
            return False, layout_view(current, today, endpoints)
    return True, layout_view(row, today, endpoints)
