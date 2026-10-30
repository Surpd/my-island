"""Explicit, revision-bound draft corrections; never changes school memberships."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4
from collections.abc import Mapping

from backend.services.schedule_experiments import ExperimentConflict, get_experiment, save_experiment, experiment_audience_preview, _lesson_interval, _lesson_audience
from backend.services.schedule_rooms import display_room, room_key


def fingerprint(lesson, slots=None):
    fields = ('id','day','weekday','period','start','end','subject','teacherId','teacherIds','room','audience','groupId','groupIds','classId','classIds','audience_snapshot','activityType')
    values={k:lesson.get(k) for k in fields}
    values['audience_snapshot']=sorted((lesson.get('audience_snapshot') or {}).get('student_ids') or [])
    interval=_lesson_interval(lesson,slots or [])
    values['effective_interval']=list(interval) if interval else None
    return hashlib.sha256(json.dumps(values,sort_keys=True,default=str).encode()).hexdigest()


def active_exceptions(lesson, scope=None, slots=None):
    return [e for e in lesson.get('resolution_history',[]) if not e.get('revoked_at') and e.get('fingerprint')==fingerprint(lesson,slots) and (scope is None or e.get('scope')=='template' or e.get('scope')==scope)]


def validated_exceptions(lessons, lesson, scope=None, slots=None):
    """Return receipts whose entire linked set still carries the same valid decision."""
    by_id={str(item.get('id')):item for item in lessons}
    valid=[]
    for receipt in active_exceptions(lesson,scope=scope,slots=slots):
        linked=[str(value) for value in receipt.get('lesson_ids') or []]
        if not linked or any(value not in by_id for value in linked):
            continue
        if all(any(other.get('id')==receipt.get('id') and other.get('type')==receipt.get('type')
                   for other in active_exceptions(by_id[value],scope=scope,slots=slots)) for value in linked):
            valid.append(receipt)
    return valid


def room_options(payload, lesson_ids):
    selected=[l for l in payload['lessons'] if l['id'] in lesson_ids]
    if len(selected)!=1: return []
    target=selected[0]; interval=_lesson_interval(target,payload['slots'])
    if not interval: return []
    rooms={room_key(l.get('room')):display_room(l.get('room')) for l in payload['lessons'] if room_key(l.get('room'))}
    occupied={room_key(l.get('room')) for l in payload['lessons'] if l['id'] not in lesson_ids and l.get('day')==target.get('day') and (other:=_lesson_interval(l,payload['slots'])) and interval[0]<other[1] and other[0]<interval[1] and room_key(l.get('room'))}
    return [{'room':label,'available':key not in occupied,'availability':'schedule_only'} for key,label in sorted(rooms.items())]


def resolution_preview(database,draft_id,*,expected_revision,lesson_ids,audience_database=None):
    draft=get_experiment(database,draft_id)
    if draft['revision']!=expected_revision: raise ExperimentConflict(draft)
    ids=sorted(set(lesson_ids)); by_id={l['id']:l for l in draft['payload']['lessons']}
    if not ids or set(ids)-by_id.keys(): raise ValueError('Выберите существующие занятия.')
    selected=[by_id[i] for i in ids]
    audiences=experiment_audience_preview(audience_database or database,draft['payload'])['lessons']
    meaningful={'revision':expected_revision,'lessons':{i:fingerprint(by_id[i],draft['payload']['slots']) for i in ids},'audiences':{i:audiences[i] for i in ids}}
    token=hashlib.sha256(json.dumps(meaningful,sort_keys=True,default=str).encode()).hexdigest()
    rule=_lesson_audience(selected[0])
    affected=[l for l in draft['payload']['lessons'] if _lesson_audience(l)==rule and audiences[l['id']].get('needs_recalculation')]
    group_ids={}
    for lesson in affected:
        rule=_lesson_audience(lesson)
        group_ids.update({str(value):None for value in (rule.get('group_ids') or [])+(rule.get('class_ids') or [])})
    if group_ids:
        with (audience_database or database).connection() as connection:
            rows=(audience_database or database).execute(connection,"SELECT id,COALESCE(display_name,name) AS name FROM groups WHERE id IN ("+','.join('?' for _ in group_ids)+")",tuple(group_ids)).fetchall()
        group_ids.update({str(row['id']):str(row['name']) for row in rows})
    affected_rows=[]
    for lesson in affected:
        interval=_lesson_interval(lesson,draft['payload']['slots'])
        rule=_lesson_audience(lesson)
        raw_day=lesson.get('day',lesson.get('weekday'))
        try:day=int(raw_day)
        except (TypeError,ValueError):day=None
        source_cell=lesson.get('source_cell') or lesson.get('sourceCell') or lesson.get('source_cell_ref')
        source_cells=lesson.get('source_cells') or lesson.get('sourceCells')
        if not source_cell and isinstance(source_cells,list):source_cell=', '.join(str(value) for value in source_cells[:4])
        affected_rows.append({'id':lesson['id'],'subject':lesson.get('subject'),'day':day,
            'start':f'{interval[0]//60:02d}:{interval[0]%60:02d}' if interval else None,
            'end':f'{interval[1]//60:02d}:{interval[1]%60:02d}' if interval else None,
            'group_names':sorted({group_ids[value] for value in (rule.get('group_ids') or [])+(rule.get('class_ids') or []) if group_ids.get(str(value))}),
            'group_ids':sorted({str(value) for value in (rule.get('group_ids') or [])+(rule.get('class_ids') or [])}),
            'source_cell':str(source_cell)[:160] if source_cell else None,
            'old_count':len((lesson.get('audience_snapshot') or {}).get('student_ids') or []),
            'current_count':len(audiences[lesson['id']].get('proposed_student_ids') or [])})
    roster={'affected_lessons':affected_rows}
    return {'draft_id':draft_id,'revision':expected_revision,'lesson_ids':ids,'preview_token':token,'lessons':selected,'rooms':room_options(draft['payload'],ids),'room_limitation':'Проверены только кабинеты, уже указанные в черновике. Фактическая доступность, вместимость и оснащение не подтверждены.','audiences':{i:audiences[i] for i in ids},'roster':roster,'exceptions':{i:validated_exceptions(draft['payload']['lessons'],by_id[i],scope=draft['payload'].get('week_start') or 'template',slots=draft['payload']['slots']) for i in ids}}


def apply_resolution(database,draft_id,*,expected_revision,preview_token,lesson_ids,action,reason,actor_user_id,room=None,exception_id=None,audience_database=None):
    if actor_user_id is None: raise ValueError('Требуется действующий вход администратора.')
    if not str(reason or '').strip(): raise ValueError('Укажите основание принятого решения.')
    preview=resolution_preview(database,draft_id,expected_revision=expected_revision,lesson_ids=lesson_ids,audience_database=audience_database)
    if preview['preview_token']!=preview_token: raise ExperimentConflict(get_experiment(database,draft_id))
    draft=get_experiment(database,draft_id); payload=deepcopy(draft['payload']); selected=[l for l in payload['lessons'] if l['id'] in preview['lesson_ids']]
    now=datetime.now(timezone.utc).isoformat(); record_id=uuid4().hex
    if action in ('joint','independent_practice'):
        directory=audience_database or database
        with directory.connection() as connection:
            active={str(r['id']) for r in directory.execute(connection,"SELECT id FROM identities WHERE kind='teacher' AND status='active'").fetchall()}
        assigned={str(t) for l in selected for t in (l.get('teacherIds') or [])+([l['teacherId']] if l.get('teacherId') else [])}
        if assigned-active:raise ValueError('Сначала исправьте отсутствующего или неактивного преподавателя.')
    if action=='room':
        if len(selected)!=1 or not str(room or '').strip(): raise ValueError('Выберите одно занятие и укажите кабинет.')
        value=str(room).strip(); target=selected[0]; interval=_lesson_interval(target,payload['slots'])
        if not interval: raise ValueError('У занятия неверно указан интервал времени.')
        for other in payload['lessons']:
            oi=_lesson_interval(other,payload['slots'])
            if other['id']!=target['id'] and other.get('day')==target.get('day') and room_key(other.get('room'))==room_key(value) and room_key(value) and oi and interval[0]<oi[1] and oi[0]<interval[1]: raise ValueError('Кабинет занят в этом интервале. Выберите другой или проверьте совместность занятий.')
        target['room']=value
    elif action=='joint':
        if len(selected)<2: raise ValueError('Выберите не менее двух связанных занятий.')
        def teachers(l): return sorted(set(l.get('teacherIds') or [])|({l['teacherId']} if l.get('teacherId') else set()))
        signatures={(l.get('day'),_lesson_interval(l,payload['slots']),tuple(teachers(l)),room_key(l.get('room')),l.get('subject')) for l in selected}
        if len(signatures)!=1 or not teachers(selected[0]) or not str(selected[0].get('room') or '').strip(): raise ValueError('Сначала уточните одинаковые время, предмет, преподавателей и общий кабинет.')
        if any(preview['audiences'][l['id']]['status']!='ok' or preview['audiences'][l['id']]['needs_recalculation'] for l in selected): raise ValueError('Сначала проверьте и подтвердите состав участников.')
    elif action=='independent_practice':
        if len(selected)!=1: raise ValueError('Выберите одно занятие для самостоятельной практики.')
    elif action=='confirm_roster':
        rules={json.dumps(_lesson_audience(l),sort_keys=True) for l in selected}
        if len(rules)!=1: raise ValueError('Массовое подтверждение допускается только для одного и того же правила аудитории.')
        if any(preview['audiences'][l['id']]['status']!='ok' or preview['audiences'][l['id']].get('proposed_student_ids') is None for l in selected): raise ValueError('Невозможно безопасно определить состав участников.')
        for l in selected:l['audience_snapshot']=None
    elif action=='revoke':
        linked=sorted({str(value) for lesson in selected for receipt in lesson.get('resolution_history',[]) if receipt.get('id')==exception_id for value in receipt.get('lesson_ids') or []})
        if not exception_id or linked!=preview['lesson_ids'] or any(not any(receipt.get('id')==exception_id and not receipt.get('revoked_at') for receipt in lesson.get('resolution_history',[])) for lesson in selected):
            raise ValueError('Выберите все связанные занятия и действующее подтверждение для отмены.')
        for l in selected:
            for e in l.get('resolution_history',[]):
                if e['id']==exception_id and not e.get('revoked_at'):e.update(revoked_at=now,revoked_by=str(actor_user_id),revocation_reason=reason)
    elif action!='defer': raise ValueError('Недопустимое действие для этой проблемы.')
    if action!='revoke':
        for l in selected:
            effective=deepcopy(l)
            if action=='confirm_roster' or not isinstance(l.get('audience_snapshot'),dict):effective['audience_snapshot']={'student_ids':preview['audiences'][l['id']].get('proposed_student_ids') or preview['audiences'][l['id']].get('student_ids') or []}
            l.setdefault('resolution_history',[]).append({'id':record_id,'type':action,'lesson_ids':preview['lesson_ids'],'actor_id':str(actor_user_id),'confirmed_at':now,'reason':reason,'scope':payload.get('week_start') or 'template','fingerprint':fingerprint(effective,payload['slots'])})
    return save_experiment(database,draft_id,expected_revision=expected_revision,payload=payload,actor_user_id=actor_user_id,audience_database=audience_database,_trusted_resolution_history=True)


def _bulk_audience_scope(draft, scope, day=None, class_id=None):
    if scope not in {'template', 'day', 'class', 'stale'}:
        raise ValueError('Выберите область пересчёта аудиторий.')
    if scope == 'day' and day is None:
        raise ValueError('Для пересчёта текущего дня выберите день.')
    if scope == 'class' and not str(class_id or '').strip():
        raise ValueError('Для пересчёта класса или группы выберите аудиторию.')
    lessons = draft['payload'].get('lessons') or []
    chosen = []
    for lesson in lessons:
        rule = _lesson_audience(lesson)
        if not rule.get('kind'):
            continue
        raw_day = lesson.get('day', lesson.get('weekday'))
        if scope == 'day' and str(raw_day) != str(day):
            continue
        if scope == 'class':
            ids = set(str(value) for value in (rule.get('class_ids') or []) + (rule.get('group_ids') or []))
            ids.update(str(value) for value in (lesson.get('classIds') or []))
            if lesson.get('classId'):
                ids.add(str(lesson['classId']))
            if str(class_id) not in ids:
                continue
        chosen.append(lesson)
    return chosen


def _bulk_audience_resolution(database, payload):
    """Resolve a weekly draft on each lesson's effective calendar date."""
    week_start = str(payload.get('week_start') or '').strip()
    if not week_start:
        return experiment_audience_preview(database, payload)
    try:
        monday = date.fromisoformat(week_start[:10])
    except ValueError as error:
        raise ValueError('Некорректная дата начала недели в черновике.') from error
    monday -= timedelta(days=monday.weekday())
    lessons = payload.get('lessons') or []
    by_day = {}
    for index, lesson in enumerate(lessons):
        try:
            day = int(lesson.get('day', lesson.get('weekday', -1)))
        except (TypeError, ValueError):
            continue
        if day in range(7):
            by_day.setdefault(day, []).append((str(lesson.get('id') or index), lesson))
    if not by_day:
        return experiment_audience_preview(database, payload)
    resolved = {}
    directory_fingerprints = {}
    for day, rows in sorted(by_day.items()):
        reference_date = (monday + timedelta(days=day)).isoformat()
        day_result = experiment_audience_preview(database, {**payload, 'reference_date': reference_date})
        directory_fingerprints[str(day)] = day_result.get('directory_fingerprint') or ''
        for lesson_id, lesson in rows:
            result = day_result['lessons'].get(lesson_id)
            if result is not None:
                resolved[lesson_id] = result
    fingerprint = hashlib.sha256(json.dumps(directory_fingerprints, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'lessons': resolved, 'directory_fingerprint': fingerprint}


def bulk_audience_preview(database, draft_id, *, expected_revision, scope, day=None,
                          class_id=None, audience_database=None, _resolved=None,
                          _directory_fingerprint=None):
    """Preview safe roster updates over a selected draft scope, without writes."""
    draft = get_experiment(database, draft_id)
    if int(draft['revision']) != expected_revision:
        raise ExperimentConflict(draft)
    selected = _bulk_audience_scope(draft, scope, day=day, class_id=class_id)
    if isinstance(_resolved, Mapping):
        resolved = _resolved
        directory_fingerprint = str(_directory_fingerprint or '')
    else:
        audience_result = _bulk_audience_resolution(audience_database or database, draft['payload'])
        resolved = audience_result['lessons']
        directory_fingerprint = str(audience_result.get('directory_fingerprint') or '')
    rows = []
    safe_count = unresolved_count = changed_count = added_count = removed_count = 0
    for lesson in selected:
        lesson_id = str(lesson.get('id') or '')
        result = resolved.get(lesson_id, {})
        old = sorted(set(str(value) for value in ((lesson.get('audience_snapshot') or {}).get('student_ids') or [])))
        proposed_value = result.get('proposed_student_ids')
        safe = result.get('status') == 'ok' and isinstance(proposed_value, list)
        proposed = sorted(set(str(value) for value in proposed_value)) if safe else None
        changed = (proposed != old or not isinstance(lesson.get('audience_snapshot'), Mapping)) if safe else False
        if scope == 'stale' and not changed and result.get('status') == 'ok':
            continue
        added = sorted(set(proposed or []) - set(old)) if safe else []
        removed = sorted(set(old) - set(proposed or [])) if safe else []
        changed_count += int(changed)
        added_count += len(added)
        removed_count += len(removed)
        safe_count += int(safe and changed)
        unresolved_count += int(not safe)
        interval = _lesson_interval(lesson, draft['payload'].get('slots') or [])
        rows.append({
            'id': lesson_id,
            'subject': lesson.get('subject') or lesson.get('activity') or ('Обед' if lesson.get('specialMark') == 'lunch' else ''),
            'day': lesson.get('day', lesson.get('weekday')),
            'start': f'{interval[0]//60:02d}:{interval[0]%60:02d}' if interval else None,
            'end': f'{interval[1]//60:02d}:{interval[1]%60:02d}' if interval else None,
            'status': result.get('status', 'incomplete'),
            'reason': result.get('reason'),
            'old_count': len(old),
            'proposed_count': len(proposed) if proposed is not None else None,
            'added_count': len(added),
            'removed_count': len(removed),
            'needs_recalculation': bool(result.get('needs_recalculation')),
            'safe': safe,
            'changed': changed,
        })
    meaningful = {
        'draft_id': draft_id, 'revision': expected_revision, 'scope': scope,
        'day': day, 'class_id': str(class_id or ''),
        'lessons': {str(lesson.get('id') or ''): fingerprint(lesson, draft['payload'].get('slots')) for lesson in selected},
        # This captures the current directory resolution, including membership
        # changes and unresolved blockers, for a stale-preview guard.
        'audiences': resolved, 'directory_fingerprint': directory_fingerprint,
    }
    token = hashlib.sha256(json.dumps(meaningful, sort_keys=True, default=str).encode()).hexdigest()
    return {
        'draft_id': draft_id, 'revision': expected_revision, 'scope': scope,
        'day': day, 'class_id': class_id, 'preview_token': token,
        'summary': {'selected_count': len(rows), 'changed_count': changed_count,
                    'added_count': added_count, 'removed_count': removed_count,
                    'safe_count': safe_count, 'unresolved_count': unresolved_count},
        'lessons': rows,
    }


def apply_bulk_audience(database, draft_id, *, expected_revision, scope, preview_token,
                        reason, actor_user_id, day=None, class_id=None, audience_database=None):
    """Confirm only known proposed rosters from a fresh scope preview in one CAS."""
    if actor_user_id is None:
        raise ValueError('Требуется действующий вход администратора.')
    if not str(reason or '').strip():
        raise ValueError('Укажите основание пересчёта аудиторий.')
    current = get_experiment(database, draft_id)
    if int(current['revision']) != expected_revision:
        raise ExperimentConflict(current)
    # Resolve the directory once. The preview token and committed snapshots
    # must describe the same directory read, even if memberships change while
    # the confirmation is being processed.
    audience_result = _bulk_audience_resolution(audience_database or database, current['payload'])
    resolved = audience_result['lessons']
    preview = bulk_audience_preview(database, draft_id, expected_revision=expected_revision,
                                    scope=scope, day=day, class_id=class_id,
                                    audience_database=audience_database, _resolved=resolved,
                                    _directory_fingerprint=audience_result.get('directory_fingerprint'))
    if preview['preview_token'] != preview_token:
        raise ExperimentConflict(get_experiment(database, draft_id))
    payload = deepcopy(current['payload'])
    preview_rows = {row['id']: row for row in preview['lessons']}
    changed_ids = [lesson_id for lesson_id, row in preview_rows.items()
                   if row['safe'] and row['changed']]
    if not changed_ids:
        return current
    now = datetime.now(timezone.utc).isoformat()
    receipt_id = uuid4().hex
    for lesson in payload.get('lessons') or []:
        lesson_id = str(lesson.get('id') or '')
        if lesson_id not in changed_ids:
            continue
        result = resolved.get(lesson_id, {})
        proposed = result.get('proposed_student_ids')
        if result.get('status') != 'ok' or not isinstance(proposed, list):
            raise ValueError('Состав одного из занятий стал неоднозначным. Пересчитайте аудитории заново.')
        effective = deepcopy(lesson)
        effective['audience_snapshot'] = {'student_ids': proposed}
        lesson['audience_snapshot'] = None
        lesson.setdefault('resolution_history', []).append({
            'id': receipt_id, 'type': 'confirm_roster', 'lesson_ids': changed_ids,
            'actor_id': str(actor_user_id), 'confirmed_at': now,
            'reason': str(reason).strip(), 'scope': payload.get('week_start') or 'template',
            'fingerprint': fingerprint(effective, payload.get('slots')),
        })
    return save_experiment(database, draft_id, expected_revision=expected_revision,
                           payload=payload, actor_user_id=actor_user_id,
                           audience_database=audience_database,
                           _trusted_resolution_history=True,
                           _preserve_unconfirmed_rosters=True,
                           _audience_resolution={'lessons': resolved})
