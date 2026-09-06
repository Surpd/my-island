from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from backend.database import Database


@dataclass(frozen=True)
class ScheduleEntry:
    lesson_date: str
    start_time: str
    subject: str
    end_time: str = ""
    teacher: str = ""
    room: str = ""
    audience: str = ""
    subject_subgroup: str = ""
    exam_track: str = ""
    lesson_type: str = "lesson"
    delivery_mode: str = ""
    parse_status: str = "parsed"
    parse_diagnostics: str = ""
    source_tab: str = ""
    source_coordinate: str = ""
    header_source_column: str = ""
    week_start: str = ""
    raw_source: str = ""

    @property
    def entry_key(self) -> str:
        try:
            source = json.loads(self.raw_source)
        except (TypeError, json.JSONDecodeError):
            source = {}
        if isinstance(source, dict) and isinstance(source.get("raw_source"), dict):
            source = source["raw_source"]
        location = "|".join(str(source.get(key, "")) for key in ("sheet", "row", "column"))
        return "|".join((self.lesson_date, self.start_time, self.subject, self.teacher, self.room, self.audience, self.subject_subgroup, self.exam_track, location))


def parse_schedule_rows(rows: list[dict]) -> list[ScheduleEntry]:
    entries: list[ScheduleEntry] = []
    for row_number, row in enumerate(rows, start=1):
        lesson_date = str(row.get("date", "")).strip()
        start_time = str(row.get("start_time", "")).strip()
        subject = str(row.get("subject", "")).strip()
        if not lesson_date or not start_time or not subject:
            raise ValueError(f"row {row_number}: date, start_time and subject are required")
        try:
            date.fromisoformat(lesson_date)
            datetime.strptime(start_time, "%H:%M")
        except ValueError as error:
            raise ValueError(f"row {row_number}: invalid date/time") from error
        try:
            week_start = date.fromisoformat(lesson_date) - timedelta(days=date.fromisoformat(lesson_date).weekday())
        except ValueError:
            week_start = None
        raw_source = row.get("raw_source", row)
        if isinstance(raw_source, dict):
            source_tab = str(raw_source.get("sheet", ""))
            source_coordinate = str(raw_source.get("coordinate", ""))
            if not source_coordinate and raw_source.get("row") and raw_source.get("column"):
                source_coordinate = f"{raw_source.get('row')}:{raw_source.get('column')}"
            header_source_column = str(raw_source.get("header_source_column", ""))
        else:
            source_tab = source_coordinate = header_source_column = ""
        entries.append(ScheduleEntry(
            lesson_date=lesson_date,
            start_time=start_time,
            subject=subject,
            end_time=str(row.get("end_time", "")).strip(),
            teacher=str(row.get("teacher", "")).strip(),
            room=str(row.get("room", "")).strip(),
            audience=str(row.get("audience", "")).strip(),
            subject_subgroup=str(row.get("subject_subgroup", "")).strip(),
            exam_track=str(row.get("exam_track", "")).strip(),
            lesson_type=str(row.get("lesson_type", "lesson")).strip() or "lesson",
            delivery_mode=str(row.get("delivery_mode", "")).strip(),
            parse_status=str(row.get("parse_status", "parsed")).strip() or "parsed",
            parse_diagnostics=str(row.get("parse_diagnostics", "")).strip(),
            source_tab=source_tab,
            source_coordinate=source_coordinate,
            header_source_column=header_source_column,
            week_start=week_start.isoformat() if week_start else "",
            raw_source=json.dumps(row, ensure_ascii=False, sort_keys=True),
        ))
    if len({entry.entry_key for entry in entries}) != len(entries):
        raise ValueError("duplicate schedule entry")
    return entries


def sync_schedule(database: Database, rows: list[dict], source: str = "sample") -> int:
    return sync_schedule_with_stats(database, rows, source=source)["total"]


def _stored_source(value: object) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def sync_schedule_with_stats(database: Database, rows: list[dict], source: str = "sample") -> dict[str, int]:
    """Validate first, then incrementally replace only changed cells for each week."""
    entries = parse_schedule_rows(rows)
    source_hash = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    stats = {
        "total": len(entries), "unchanged": 0, "changed": 0, "new": 0, "removed": 0,
        "parsed": sum(entry.parse_status in {"parsed", "special"} for entry in entries),
        "ambiguous": sum(entry.parse_status in {"partial", "ambiguous"} for entry in entries),
        "failed": sum(entry.parse_status == "failed" for entry in entries),
    }
    with database.connection() as connection:
        database.execute(connection, "BEGIN")
        if database.database_url:
            database.execute(connection, "UPDATE schedule_entries SET week_start = date_trunc('week', lesson_date)::date WHERE week_start IS NULL")
        weeks = sorted({entry.week_start for entry in entries if entry.week_start})
        existing = database.execute(connection, "SELECT id, week_start, source_tab, source_coordinate, raw_source FROM schedule_entries WHERE week_start IN ({})".format(",".join("?" for _ in weeks)), tuple(weeks)).fetchall() if weeks else []
        existing_by_location = {
            (str(row["source_tab"] or ""), str(row["source_coordinate"] or "")): row
            for row in existing if row["source_tab"] and row["source_coordinate"]
        }
        entries_by_location = {
            (entry.source_tab, entry.source_coordinate): entry
            for entry in entries if entry.source_tab and entry.source_coordinate
        }
        if not existing_by_location or len(existing_by_location) != len(existing):
            for week_start in weeks:
                database.execute(connection, "DELETE FROM schedule_entries WHERE week_start = ?", (week_start,))
            stats["new"] = len(entries)
        else:
            for location, old_row in existing_by_location.items():
                if location not in entries_by_location:
                    database.execute(connection, "DELETE FROM schedule_entries WHERE id = ?", (old_row["id"],))
                    stats["removed"] += 1
        for entry in entries:
            location = (entry.source_tab, entry.source_coordinate)
            if existing_by_location and location in existing_by_location and location in entries_by_location:
                current_source = json.loads(entry.raw_source)
                if _stored_source(existing_by_location[location]["raw_source"]) == _stored_source(current_source):
                    stats["unchanged"] += 1
                    continue
                database.execute(connection, "DELETE FROM schedule_entries WHERE id = ?", (existing_by_location[location]["id"],))
                stats["changed"] += 1
            database.execute(connection, """INSERT INTO schedule_entries(
                entry_key, lesson_date, start_time, end_time, subject, teacher, room, audience,
                subject_subgroup, exam_track, lesson_type, delivery_mode, parse_status,
                parse_diagnostics, source_tab, source_coordinate, header_source_column, week_start,
                source_hash, raw_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                entry.entry_key, entry.lesson_date, entry.start_time, entry.end_time, entry.subject,
                entry.teacher, entry.room, entry.audience, entry.subject_subgroup, entry.exam_track,
                entry.lesson_type, entry.delivery_mode, entry.parse_status,
                json.dumps([item for item in entry.parse_diagnostics.split("; ") if item], ensure_ascii=False),
                entry.source_tab, entry.source_coordinate, entry.header_source_column, entry.week_start,
                source_hash, entry.raw_source,
            ))
        database.execute(connection, "INSERT INTO schedule_syncs(status, source, source_hash, validation_problems) VALUES ('success', ?, ?, ?)", (source, source_hash, json.dumps(stats, ensure_ascii=False)))
    return stats


def record_failed_sync(database: Database, source: str, error: Exception) -> None:
    with database.connection() as connection:
        database.execute(connection, "INSERT INTO schedule_syncs(status, source, error) VALUES ('failed', ?, ?)", (source, str(error)))
