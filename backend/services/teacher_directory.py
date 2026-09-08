"""Conservative teacher-directory extraction and reconciliation.

The sheet is an assignment source, not a student-roster source.  This module
only creates teacher identities and assignments to groups which already exist
as canonical school groups; it never touches student memberships.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .school_data import stable_fingerprint


SHEET_NAME = "Учителя и группы"
SOURCE_KIND = "official_import"
TEACHER_ALIASES = {
    "дф": "Дмитрий Филиппов",
    "дмитрий ф": "Дмитрий Филиппов",
    "дмитрий к": "Дмитрий К",
    "иван б": "Иван",
    "мария е": "Мария",
    "анна": "Анна Владимировна",
    "оля": "Ольга",
    "анк": "Андрей",
    "алекс": "Алексей",
    "ае": "Анна Елисеева",
    "иа": "Ирина Анатольевна",
    "ев": "Елена Викторовна",
}
SUBJECT_ALIASES = {
    "математика": "Математика",
    "география": "География",
    "история": "История",
    "история искусства": "История искусства",
    "биология": "Биология",
    "химия": "Химия",
    "естествознание": "Естествознание",
    "физика": "Физика",
    "русский язык": "Русский язык",
    "русский": "Русский язык",
    "литература": "Литература",
    "обществознание": "Обществознание",
    "общество": "Обществознание",
    "информатика": "Информатика",
    "тренинг": "Тренинг",
    "творчество": "Творчество",
    "музыка": "Музыка",
    "пластика": "Пластика",
    "английский язык": "Английский язык",
}


@dataclass(frozen=True)
class TeacherRow:
    row_number: int
    teacher: str
    subject: str
    audiences: str
    note: str = ""

    @property
    def source_ref(self) -> str:
        return f"{SHEET_NAME}!A{self.row_number}:C{self.row_number}"


@dataclass(frozen=True)
class AssignmentCandidate:
    teacher: str
    subject: str
    group_name: str
    base_class_name: str | None
    subject_subgroup: str | None
    exam_track: str | None
    source_ref: str

    @property
    def natural_key(self) -> str:
        return f"{self.teacher}|{self.group_name}|{self.subject}"


@dataclass(frozen=True)
class TeacherIssue:
    teacher: str
    subject: str
    details: str
    source_ref: str


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def _key(value: Any) -> str:
    return _clean(value).casefold().replace("ё", "е")


def canonical_teacher_name(value: str) -> str:
    value = _clean(value)
    return TEACHER_ALIASES.get(_key(value), value)


def normalize_subjects(value: str) -> tuple[str, ...]:
    result: list[str] = []
    for raw in re.split(r"[,;/]+", _clean(value)):
        token = _clean(raw).strip(" .")
        if not token or "лаборатор" in _key(token):
            continue
        subject = SUBJECT_ALIASES.get(_key(token))
        if subject and subject not in result:
            result.append(subject)
    return tuple(result)


def parse_teacher_rows(values: Sequence[Sequence[Any]]) -> tuple[list[TeacherRow], list[TeacherIssue]]:
    if not values:
        raise ValueError("Учителя и группы: пустой лист")
    headers = [_key(item) for item in values[0]]
    required = ("учитель", "предмет", "классы и группы")
    if any(item not in headers for item in required):
        raise ValueError("Учителя и группы: ожидаются колонки Учитель, Предмет, Классы и группы")
    indexes = {name: headers.index(name) for name in required}
    rows: list[TeacherRow] = []
    issues: list[TeacherIssue] = []
    for row_number, raw in enumerate(values[1:], start=2):
        teacher = _clean(raw[indexes["учитель"]] if indexes["учитель"] < len(raw) else "")
        subject = _clean(raw[indexes["предмет"]] if indexes["предмет"] < len(raw) else "")
        audiences = _clean(raw[indexes["классы и группы"]] if indexes["классы и группы"] < len(raw) else "")
        note = _clean(raw[3] if len(raw) > 3 else "")
        if not teacher and not subject and not audiences:
            continue
        if not teacher or not subject or not audiences:
            issues.append(TeacherIssue(teacher, subject, "неполная строка источника", f"{SHEET_NAME}!A{row_number}:C{row_number}"))
            continue
        if not normalize_subjects(subject):
            issues.append(TeacherIssue(teacher, subject, "предмет не распознан детерминированно", f"{SHEET_NAME}!A{row_number}:C{row_number}"))
            continue
        rows.append(TeacherRow(row_number, teacher, subject, audiences, note))
    return rows, issues


def _grades(text: str) -> list[str]:
    result: list[str] = []
    for start, end in re.findall(r"(\d{1,2})\s*[-–—]\s*(\d{1,2})", text):
        for value in range(int(start), int(end) + 1):
            result.append(str(value))
    result.extend(re.findall(r"(?<![\d-])(7-[12]|5|6|7|8|9|10|11)(?![\d-])", text))
    return list(dict.fromkeys(result))


def _track_grades(text: str, marker: str, grades: Sequence[str]) -> set[str]:
    """Extract only grades explicitly attached to an OGE/EGE marker."""
    found: set[str] = set()
    for value in re.findall(rf"{marker}\s*(\d{{1,2}}(?:[-–—]\d{{1,2}})?)", text, flags=re.IGNORECASE):
        found.update(_grades(value))
    for value in re.findall(rf"(\d{{1,2}}(?:[-–—]\d{{1,2}})?)\s*{marker}", text, flags=re.IGNORECASE):
        found.update(_grades(value))
    if not found and marker.upper() == "ОГЭ" and "9" in grades:
        found.add("9")
    return found


def _class_names(grade: str) -> tuple[str, ...]:
    if grade == "9":
        return ("9-А", "9-Д")
    if grade == "7":
        return ("7-1", "7-2")
    return (grade,)


def _lookup_group(groups: Mapping[str, Mapping[str, Any]], *, subject: str, grade: str, track: str | None = None, subgroup: str | None = None) -> Mapping[str, Any] | None:
    candidates = []
    for group in groups.values():
        if _key(group.get("subject")) != _key(subject):
            continue
        if _clean(group.get("base_class_name")) != grade:
            continue
        if track and _key(group.get("exam_track")) != _key(track):
            continue
        if subgroup and _key(group.get("subject_subgroup")) != _key(subgroup):
            continue
        candidates.append(group)
    return candidates[0] if len(candidates) == 1 else None


def _class_group(groups: Mapping[str, Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    group = groups.get(name)
    return group if group and group.get("group_type") == "class" and group.get("canonical") else None


def build_assignment_candidates(rows: Sequence[TeacherRow], groups: Mapping[str, Mapping[str, Any]]) -> tuple[list[AssignmentCandidate], list[TeacherIssue]]:
    assignments: dict[str, AssignmentCandidate] = {}
    issues: list[TeacherIssue] = []
    for row in rows:
        subjects = normalize_subjects(row.subject)
        text = row.audiences
        lower = _key(text)
        if "по расписанию" in lower and not _grades(text):
            issues.append(TeacherIssue(row.teacher, row.subject, "постоянная группа не указана: «по расписанию»", row.source_ref))
        # English groups are global and have no class dimension.
        english_numbers = re.findall(r"(?:групп[аы]?\s*)?(\d{1,2})(?=\b)", text, flags=re.IGNORECASE) if "англий" in _key(row.subject) else []
        if english_numbers:
            for number in dict.fromkeys(english_numbers):
                group = groups.get(f"english:{number}")
                if not group:
                    issues.append(TeacherIssue(row.teacher, row.subject, f"английская группа {number} отсутствует среди canonical groups", row.source_ref))
                    continue
                assignments[f"{canonical_teacher_name(row.teacher)}|{group['name']}|{subjects[0]}"] = AssignmentCandidate(canonical_teacher_name(row.teacher), subjects[0], group["name"], None, str(number), None, row.source_ref)
            continue
        if "по расписанию" in lower and not _grades(text):
            continue
        grades = _grades(text)
        has_oge = bool(re.search(r"\bог[эе]\b|\bогэ\b", lower))
        has_ege = bool(re.search(r"\bегэ\b", lower))
        oge_grades = _track_grades(text, "огэ", grades) if has_oge else set()
        ege_grades = _track_grades(text, "егэ", grades) if has_ege else set()
        if has_ege and not ege_grades:
            issues.append(TeacherIssue(row.teacher, row.subject, "ЕГЭ указан без однозначного класса", row.source_ref))
        explicit_base = bool(re.search(r"\bбаз", lower))
        explicit_profile = bool(re.search(r"профил|угл", lower))
        math_letters = re.findall(r"(?:групп[аы]?\s*)?([абвсabc])\b", lower, flags=re.IGNORECASE) if "математ" in _key(row.subject) else []
        if math_letters and "9" in grades:
            for letter in dict.fromkeys(math_letters):
                letter = {"а": "A", "б": "B", "в": "B", "с": "C"}.get(letter, letter.upper())
                group = groups.get(f"grade9-math-{letter}")
                if group:
                    assignments[f"{canonical_teacher_name(row.teacher)}|{group['name']}|Математика"] = AssignmentCandidate(canonical_teacher_name(row.teacher), "Математика", group["name"], "9", letter, None, row.source_ref)
            grades = [grade for grade in grades if grade != "9"]
        for subject in subjects:
            for grade in grades:
                if grade == "9" and (grade in oge_grades or grade in ege_grades or explicit_base or explicit_profile):
                    variants: list[tuple[str | None, str | None]] = []
                    if explicit_base:
                        variants.append(("База", None))
                    if grade in oge_grades:
                        variants.append((None, "ОГЭ"))
                    if grade in ege_grades:
                        variants.append((None, "ЕГЭ"))
                    if explicit_profile:
                        variants.append(("Профиль", None))
                    for subgroup, track in variants or [(None, None)]:
                        if explicit_profile and subject == "Математика":
                            group = groups.get("math:11:advanced") if grade == "11" else None
                        else:
                            group = _lookup_group(groups, subject=subject, grade=grade, track=track, subgroup=subgroup)
                        if group:
                            assignments[f"{canonical_teacher_name(row.teacher)}|{group['name']}|{subject}"] = AssignmentCandidate(canonical_teacher_name(row.teacher), subject, group["name"], grade, group.get("subject_subgroup"), group.get("exam_track"), row.source_ref)
                        else:
                            issues.append(TeacherIssue(row.teacher, subject, f"не найден canonical group для {grade} / {track or subgroup or 'profile'}", row.source_ref))
                    continue
                if explicit_profile and subject == "Математика" and grade == "11":
                    group = groups.get("math:11:advanced")
                    if group:
                        assignments[f"{canonical_teacher_name(row.teacher)}|{group['name']}|{subject}"] = AssignmentCandidate(canonical_teacher_name(row.teacher), subject, group["name"], grade, group.get("subject_subgroup"), None, row.source_ref)
                    continue
                if explicit_base and subject == "Математика" and grade == "11":
                    group = groups.get("math:11:base")
                    if group:
                        assignments[f"{canonical_teacher_name(row.teacher)}|{group['name']}|{subject}"] = AssignmentCandidate(canonical_teacher_name(row.teacher), subject, group["name"], grade, group.get("subject_subgroup"), None, row.source_ref)
                    continue
                if grade in ege_grades:
                    group = _lookup_group(groups, subject=subject, grade=grade, track="ЕГЭ")
                    if group:
                        assignments[f"{canonical_teacher_name(row.teacher)}|{group['name']}|{subject}"] = AssignmentCandidate(canonical_teacher_name(row.teacher), subject, group["name"], grade, group.get("subject_subgroup"), "ЕГЭ", row.source_ref)
                    else:
                        issues.append(TeacherIssue(row.teacher, subject, f"не найден canonical ЕГЭ group для {grade}", row.source_ref))
                    continue
                for class_name in _class_names(grade):
                    group = _class_group(groups, class_name)
                    if group:
                        assignments[f"{canonical_teacher_name(row.teacher)}|{class_name}|{subject}"] = AssignmentCandidate(canonical_teacher_name(row.teacher), subject, class_name, class_name, None, None, row.source_ref)
                    else:
                        issues.append(TeacherIssue(row.teacher, subject, f"не найден canonical class {class_name}", row.source_ref))
            if not grades and not english_numbers:
                issues.append(TeacherIssue(row.teacher, subject, "не указан однозначный класс или группа", row.source_ref))
    return sorted(assignments.values(), key=lambda item: item.natural_key), issues


def sync_teacher_directory(database: Any, rows: Sequence[Sequence[Any]], *, spreadsheet_id: str, spreadsheet_title: str, apply: bool = True) -> dict[str, Any]:
    """Stage and apply one deterministic teacher snapshot transactionally."""
    parsed_rows, parse_issues = parse_teacher_rows(rows)
    with database.connection() as connection:
        raw_source = json.dumps({"sheet": SHEET_NAME, "rows": [list(row) for row in rows]}, ensure_ascii=False, sort_keys=True)
        source_key = f"google:{spreadsheet_id}:{SHEET_NAME}"
        source = database.execute(connection, """INSERT INTO school_sources(source_type, external_key, display_name, location_ref, authority_status, configuration)
          VALUES ('other', ?, ?, ?, 'authoritative', ?) ON CONFLICT(source_type, external_key) DO UPDATE SET display_name=excluded.display_name, location_ref=excluded.location_ref, authority_status='authoritative' RETURNING id""", (source_key, f"Учителя и группы · {spreadsheet_title}", f"{spreadsheet_id}:{SHEET_NAME}", json.dumps({"role": "teacher_assignments"}, ensure_ascii=False))).fetchone()
        source_id = source["id"]
        fingerprint = stable_fingerprint(raw_source)
        existing_snapshot = database.execute(connection, "SELECT id, is_last_known_valid FROM school_source_snapshots WHERE source_id = ? AND fingerprint = ?", (source_id, fingerprint)).fetchone()
        if existing_snapshot:
            snapshot_id = existing_snapshot["id"]
        else:
            run = database.execute(connection, "INSERT INTO school_sync_runs(source_id, mode, status, idempotency_key, diagnostics) VALUES (?, 'incremental', 'started', ?, '{}') RETURNING id", (source_id, fingerprint)).fetchone()
            previous = database.execute(connection, "SELECT id FROM school_source_snapshots WHERE source_id = ? AND is_last_known_valid IS TRUE ORDER BY created_at DESC LIMIT 1", (source_id,)).fetchone()
            snapshot = database.execute(connection, """INSERT INTO school_source_snapshots(source_id, sync_run_id, previous_snapshot_id, fingerprint, observed_at, raw_payload, structural_payload, status, is_last_known_valid)
              VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, 'valid', TRUE) RETURNING id""", (source_id, run["id"], previous["id"] if previous else None, fingerprint, raw_source, json.dumps({"rows": len(parsed_rows), "issues": len(parse_issues)}, ensure_ascii=False))).fetchone()
            snapshot_id = snapshot["id"]
            if previous:
                database.execute(connection, "UPDATE school_source_snapshots SET is_last_known_valid = FALSE, status = 'superseded' WHERE id = ?", (previous["id"],))
            for parsed in parsed_rows:
                payload = {"teacher": parsed.teacher, "subject": parsed.subject, "audiences": parsed.audiences, "note": parsed.note}
                database.execute(connection, "INSERT INTO school_source_records(snapshot_id, record_key, fingerprint, source_ref, change_kind, parse_status, raw_payload, structural_payload) VALUES (?, ?, ?, ?, 'new', 'validated', ?, ?)", (snapshot_id, f"row:{parsed.row_number}", stable_fingerprint(payload), parsed.source_ref, json.dumps(payload, ensure_ascii=False), json.dumps(payload, ensure_ascii=False)))
            database.execute(connection, "UPDATE school_sync_runs SET status = 'applied', finished_at = CURRENT_TIMESTAMP WHERE id = ?", (run["id"],))
        group_rows = database.execute(connection, "SELECT id,name,group_type,subject,base_class_name,subject_subgroup,exam_track,canonical FROM groups WHERE canonical IS TRUE").fetchall()
        groups = {str(row["name"]): dict(row) for row in group_rows}
        candidates, mapping_issues = build_assignment_candidates(parsed_rows, groups)
        issues = parse_issues + mapping_issues
        teacher_ids: dict[str, Any] = {}
        source_teacher_refs = {
            canonical_teacher_name(row.teacher): row.source_ref for row in parsed_rows
        }
        for name, source_ref in sorted(source_teacher_refs.items()):
            identity = database.execute(connection, "SELECT * FROM identities WHERE kind = 'teacher' AND status = 'active' AND lower(replace(display_name, 'ё', 'е')) = lower(replace(?, 'ё', 'е')) ORDER BY id LIMIT 1", (name,)).fetchone()
            if not identity:
                identity = database.execute(connection, "INSERT INTO identities(kind, display_name, status, origin, source_ref) VALUES ('teacher', ?, 'active', 'official_import', ?) RETURNING *", (name, source_ref)).fetchone()
            teacher_ids[name] = identity["id"]
            external_key = f"teacher:{name}"
            database.execute(connection, """INSERT INTO school_source_mappings(source_id, external_key, mapping_type, identity_id, status, manually_confirmed, evidence)
              VALUES (?, ?, 'identity', ?, 'confirmed', FALSE, ?) ON CONFLICT(source_id, external_key, mapping_type) WHERE valid_until IS NULL AND status <> 'revoked' DO UPDATE SET identity_id=excluded.identity_id, evidence=excluded.evidence""", (source_id, external_key, identity["id"], json.dumps({"source_ref": source_ref, "source_name": name}, ensure_ascii=False)))
        desired_refs: set[str] = set()
        counts = {"KEEP": 0, "ADD": 0, "REMOVE": 0, "UNRESOLVED": len(issues)}
        for candidate in candidates:
            identity_id = teacher_ids[candidate.teacher]
            group = groups[candidate.group_name]
            source_ref = candidate.source_ref
            desired_refs.add(f"{identity_id}|{group['id']}|{candidate.subject}|{source_ref}")
            existing = database.execute(connection, "SELECT id, source, active FROM teacher_assignments WHERE teacher_identity_id = ? AND group_id = ? AND subject = ? AND active IS TRUE ORDER BY CASE WHEN source = 'manual_confirmation' THEN 0 ELSE 1 END LIMIT 1", (identity_id, group["id"], candidate.subject)).fetchone()
            if existing:
                counts["KEEP"] += 1
                continue
            if apply:
                database.execute(connection, """INSERT INTO teacher_assignments(teacher_identity_id, group_id, subject, base_class_name, subject_subgroup, exam_track, capability, source, source_ref, active)
                  VALUES (?, ?, ?, ?, ?, ?, ?, 'official_import', ?, TRUE)
                  ON CONFLICT(teacher_identity_id, group_id, subject, capability, source, source_ref) DO UPDATE SET active=TRUE, base_class_name=excluded.base_class_name, subject_subgroup=excluded.subject_subgroup, exam_track=excluded.exam_track""", (identity_id, group["id"], candidate.subject, candidate.base_class_name, candidate.subject_subgroup, candidate.exam_track, "teach", source_ref))
            counts["ADD"] += 1
        stale = database.execute(connection, "SELECT id, teacher_identity_id, group_id, subject, source_ref FROM teacher_assignments WHERE source = 'official_import' AND active IS TRUE AND source_ref LIKE ?", (f"{SHEET_NAME}!A%",)).fetchall()
        for item in stale:
            ref = f"{item['teacher_identity_id']}|{item['group_id']}|{item['subject']}|{item['source_ref']}"
            if ref not in desired_refs:
                if apply:
                    database.execute(connection, "UPDATE teacher_assignments SET active = FALSE, valid_until = CURRENT_DATE WHERE id = ?", (item["id"],))
                counts["REMOVE"] += 1
        for issue in issues:
            database.execute(connection, "INSERT INTO school_resolution_issues(sync_run_id, issue_type, status, details, evidence) SELECT id, ?, 'open', ?, ? FROM school_sync_runs WHERE source_id = ? ORDER BY id DESC LIMIT 1", ("teacher_assignment_unresolved", issue.details, json.dumps({"teacher": issue.teacher, "subject": issue.subject, "source_ref": issue.source_ref}, ensure_ascii=False), source_id))
        return {"source_id": source_id, "snapshot_id": snapshot_id, "rows": len(parsed_rows), "candidates": len(candidates), "issues": len(issues), "counts": counts, "student_memberships_touched": 0}
