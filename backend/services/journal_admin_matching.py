"""Admin previews and atomic creation/alias confirmation for journal rows."""
from __future__ import annotations

import hmac
import json
import re
from typing import Any

from backend.services.journal_matching import belongs_to_grade, normalized_name, preview_fingerprint, suggestion_reason


def candidates(database, grade: int, scope: str = 'class', q: str = '') -> list[dict[str, Any]]:
    if scope not in {'class', 'school'}:
        raise ValueError('Неизвестная область поиска.')
    query = normalized_name(q).split()
    with database.connection() as connection:
        people = [dict(row) for row in database.execute(connection,
            "SELECT id,display_name,class_name,status FROM identities WHERE kind='student' AND status='active' ORDER BY display_name,id").fetchall()]
        groups = {}
        for row in database.execute(connection,"""SELECT m.identity_id,g.name,g.base_class_name FROM memberships m JOIN groups g ON g.id=m.group_id
            WHERE m.active IS TRUE AND m.member_role='student' AND (m.valid_from IS NULL OR m.valid_from<=CURRENT_DATE)
            AND (m.valid_until IS NULL OR m.valid_until>=CURRENT_DATE)""").fetchall():
            groups.setdefault(str(row['identity_id']),[]).append(dict(row))
        aliases = {}
        for row in database.execute(connection,"""SELECT identity_id,external_key FROM school_source_mappings
            WHERE mapping_type='identity' AND manually_confirmed IS TRUE AND valid_until IS NULL AND status='confirmed'""").fetchall():
            aliases.setdefault(str(row['identity_id']),[]).append(row['external_key'].removeprefix('alias:'))
        for person in people:
            person['groups'] = groups.get(str(person['id']),[])
            person['_aliases'] = aliases.get(str(person['id']),[])
    return [{k:v for k,v in person.items() if not k.startswith('_')} for person in people
            if (scope == 'school' or belongs_to_grade(person, grade)) and
            (not query or any(all(token in value for token in query) for value in [normalized_name(person['display_name']), *person['_aliases']])
             or suggestion_reason(q, person['display_name']))]


def selected_rows(database, connection, spreadsheet_id: str, names: list[str]) -> list[dict[str, Any]]:
    names = {normalized_name(name) for name in names if normalized_name(name)}
    if not names or len(names) > 50:
        raise ValueError('Выберите от 1 до 50 вариантов имени.')
    rows = [dict(row) for row in database.execute(connection, """SELECT s.id,s.source_id,s.display_name,s.identity_id,s.updated_at,
        src.grade,src.subject FROM journal_students s JOIN journal_sources src ON src.id=s.source_id
        WHERE src.spreadsheet_id=? AND s.active IS TRUE AND s.entry_kind='individual' ORDER BY s.id""", (spreadsheet_id,)).fetchall()
        if normalized_name(row['display_name']) in names]
    if {normalized_name(row['display_name']) for row in rows} != names:
        raise ValueError('Выбранные записи изменились. Обновите список.')
    if len({row['grade'] for row in rows}) != 1:
        raise ValueError('Выбранные варианты принадлежат разным классам.')
    return rows


def batch_preview(database, connection, spreadsheet_id: str, names: list[str], identity_id: Any, allow_cross_grade=False):
    rows = selected_rows(database, connection, spreadsheet_id, names)
    previews = [database._journal_match_preview(connection, spreadsheet_id, name, identity_id, allow_cross_grade=allow_cross_grade)
                for name in sorted({row['display_name'] for row in rows})]
    return {'identity': previews[0]['identity'], 'items': [{k:v for k,v in item.items() if not k.startswith('_')} for item in previews],
            'cross_grade':not belongs_to_grade(previews[0]['identity'],rows[0]['grade']), 'source_names': sorted({row['display_name'] for row in rows}), 'grade':rows[0]['grade'],
            'record_count':sum(p['record_count'] for p in previews), 'subject_count':len({row['source_id'] for row in rows}),
            'subjects':sorted({row['subject'] for row in rows}), 'result_count':sum(p['result_count'] for p in previews),
            'preview_token':preview_fingerprint([p['preview_token'] for p in previews]), '_previews':previews}


def batch(database, payload: dict, actor_id=None, confirm=False):
    with database.connection() as connection:
        if confirm:
            database._journal_mapping_lock(connection, payload['spreadsheet_id'])
        preview = batch_preview(database, connection, payload['spreadsheet_id'], payload['source_names'], payload['identity_id'],payload.get('allow_cross_grade',False))
        if confirm:
            if not hmac.compare_digest(payload.get('preview_token', ''), preview['preview_token']):
                raise ValueError('Данные изменились после предпросмотра. Повторите проверку.')
            for item in preview['_previews']:
                database._apply_journal_match(connection, payload['spreadsheet_id'], payload['identity_id'], item, actor_id)
        return {k:v for k,v in preview.items() if not k.startswith('_')}


def creation_preview(database, connection, payload):
    surname, given = (' '.join(payload[key].split()) for key in ('surname','given_name'))
    if not all(re.fullmatch(r"[^\W\d_]+(?:[- ][^\W\d_]+)*", name, re.UNICODE) for name in (surname,given)):
        raise ValueError('Проверьте фамилию и имя: используйте буквы, пробелы или дефис.')
    display_name = surname + ' ' + given
    rows = selected_rows(database, connection, payload['spreadsheet_id'], payload['source_names'])
    if int(payload['grade']) != rows[0]['grade'] or any(row['identity_id'] is not None for row in rows):
        raise ValueError('Класс или сопоставления изменились. Обновите список.')
    # Include inactive identities; matching a surname is deliberately conservative.
    duplicates = [dict(row) for row in database.execute(connection,
        "SELECT id,display_name,class_name,status FROM identities WHERE kind='student' ORDER BY display_name,id").fetchall()
        if normalized_name(surname) in normalized_name(row['display_name']).split()
        or suggestion_reason(display_name, row['display_name'])]
    archive_filter = ' AND archived_at IS NULL' if database._group_archive_column_available(connection) else ''
    groups = [dict(row) for row in database.execute(connection,
        "SELECT id,name,display_name,base_class_name FROM groups WHERE group_type='class' AND canonical IS TRUE" + archive_filter).fetchall()
        if belongs_to_grade(dict(row, class_name=row['base_class_name'] or row['name']), int(payload['grade']))]
    if len(groups) != 1:
        raise ValueError('Базовый класс неоднозначен. Сначала проверьте канонические группы класса.')
    ids = [row['id'] for row in rows]
    results = [dict(row) for row in database.execute(connection,
        f"SELECT id,journal_student_id,identity_id,is_current FROM journal_results WHERE journal_student_id IN ({','.join('?' for _ in ids)}) ORDER BY id", ids).fetchall()]
    token = preview_fingerprint({'rows':rows,'results':results,'duplicates':duplicates,'group':groups[0],
                                 'name':display_name,'comment':payload.get('comment','').strip()})
    return {'display_name':display_name,'grade':int(payload['grade']),'class_name':groups[0]['base_class_name'] or groups[0]['name'],
            'duplicates':duplicates,'can_confirm':not duplicates,'preview_token':token if not duplicates else None,
            'record_count':len(rows),'subject_count':len({row['source_id'] for row in rows}),
            'subjects':sorted({row['subject'] for row in rows}), 'result_count':sum(bool(r['is_current']) for r in results),
            '_group':groups[0], '_names':sorted({row['display_name'] for row in rows})}


def create_student(database, payload: dict, actor_id=None, confirm=False):
    with database.connection() as connection:
        if confirm:
            database._journal_mapping_lock(connection, payload['spreadsheet_id'])
            # Serialize journal-assisted People creation across workbooks as well.
            if database.database_url:
                database.execute(connection, "LOCK TABLE identities IN SHARE ROW EXCLUSIVE MODE")
        preview = creation_preview(database, connection, payload)
        if confirm:
            if not preview['can_confirm'] or not hmac.compare_digest(payload.get('preview_token',''), preview['preview_token']):
                raise ValueError('Найден возможный дубль или данные изменились. Повторите предпросмотр.')
            identity = database.execute(connection, """INSERT INTO identities(kind,display_name,class_name,status,origin,source_ref,admin_note)
                VALUES ('student',?,?,'active','admin_override','journal_matching',?) RETURNING id""",
                (preview['display_name'],preview['class_name'],payload.get('comment','').strip() or None)).fetchone()
            database.execute(connection, """INSERT INTO memberships(group_id,identity_id,member_role,source,source_ref,active,created_by)
                VALUES (?,?,'student','admin_override','journal_matching',TRUE,?)
                ON CONFLICT(group_id,identity_id,member_role,source,source_ref) DO NOTHING""", (preview['_group']['id'],identity['id'],actor_id))
            batch_result = batch_preview(database, connection, payload['spreadsheet_id'], preview['_names'], identity['id'])
            for item in batch_result['_previews']:
                database._apply_journal_match(connection,payload['spreadsheet_id'],identity['id'],item,actor_id)
            database.execute(connection,"INSERT INTO audit_log(actor_user_id,action,entity_type,entity_id,details) VALUES (?,'identity.created','person',?,?)",
                (actor_id,identity['id'],json.dumps({'source':'journal_matching','class_group_id':str(preview['_group']['id']),'record_count':preview['record_count']})))
            preview['identity_id'] = str(identity['id'])
        return {k:v for k,v in preview.items() if not k.startswith('_')}
