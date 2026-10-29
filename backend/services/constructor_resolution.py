"""Explicit, revision-bound draft corrections; never changes school memberships."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from backend.services.schedule_experiments import ExperimentConflict, get_experiment, save_experiment, experiment_audience_preview, _lesson_interval, _lesson_audience


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
    rooms=sorted({str(l.get('room') or '').strip() for l in payload['lessons']} - {''})
    occupied={str(l.get('room') or '').strip() for l in payload['lessons'] if l['id'] not in lesson_ids and l.get('day')==target.get('day') and (other:=_lesson_interval(l,payload['slots'])) and interval[0]<other[1] and other[0]<interval[1]}
    return [{'room':r,'available':r not in occupied,'availability':'schedule_only'} for r in rooms]


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
    if preview['preview_token']!=preview_token: raise ExperimentConflict(draft)
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
            if other['id']!=target['id'] and other.get('day')==target.get('day') and str(other.get('room') or '').strip()==value and oi and interval[0]<oi[1] and oi[0]<interval[1]: raise ValueError('Кабинет занят в этом интервале. Выберите другой или проверьте совместность занятий.')
        target['room']=value
    elif action=='joint':
        if len(selected)<2: raise ValueError('Выберите не менее двух связанных занятий.')
        def teachers(l): return sorted(set(l.get('teacherIds') or [])|({l['teacherId']} if l.get('teacherId') else set()))
        signatures={(l.get('day'),_lesson_interval(l,payload['slots']),tuple(teachers(l)),str(l.get('room') or '').strip(),l.get('subject')) for l in selected}
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
