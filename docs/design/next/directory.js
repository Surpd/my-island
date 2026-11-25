(() => {
  const subjects = ['Математика', 'Русский язык', 'Английский язык', 'История', 'Биология', 'Информатика', 'География', 'Физика', 'Химия', 'Литература'];
  const first = ['Анна', 'Мария', 'Елена', 'Ольга', 'Наталья', 'Екатерина', 'Ирина', 'Татьяна', 'Светлана', 'Вера', 'Дарья', 'Алина'];
  const last = ['Воронова', 'Соколова', 'Лебедева', 'Климова', 'Орлова', 'Романова', 'Ковалева', 'Морозова', 'Петрова', 'Никитина', 'Федорова', 'Волкова', 'Егорова', 'Голубева', 'Белова'];
  const grades = [5, 6, 7, 8, 9, 10, 11];
  const classes = grades.flatMap(grade => ['А', 'Б'].map(letter => ({ id: grade + letter, grade, name: grade + letter, type: 'class' })));
  const groups = grades.flatMap(grade => [2, 2, 0, 5].map((subject, i) => ({
    id: 'g' + grade + i, grade, subject: subjects[subject], name: grade + ' · ' + subjects[subject] + (subject === 2 ? ' ' + (i + 1) : ''),
    archived: grade === 11 && i === 3, type: 'group'
  })));
  const people = classes.flatMap((c, i) => Array.from({ length: 19 + i % 7 }, (_, n) => ({
    id: 'p' + i + '-' + n, name: last[(n * 7 + i) % last.length] + ' ' + first[(i + n) % first.length],
    classId: c.id, groupIds: n === 0 ? [] : ['g' + c.grade + n % 2, 'g' + c.grade + (n % 2 + 2)]
  })));
  const teachers = Array.from({ length: 29 }, (_, i) => ({
    id: 't' + i, name: last[i % last.length] + ' ' + first[Math.floor(i / last.length) + i % 9] + ' ' + ['Сергеевна', 'Игоревна', 'Алексеевна', 'Андреевна'][i % 4],
    subjects: i === 0 ? [subjects[0], subjects[5]] : [subjects[i % subjects.length]],
    status: i > 25 ? 'Неактивен' : 'Активен', note: i === 0 ? 'Координирует подготовку к олимпиаде по математике.' : '',
    assignments: i > 25 ? [] : Array.from({ length: i === 0 ? 6 : 1 + i % 5 }, (_, j) => ({
      id: 'a' + i + '-' + j, subject: subjects[i % subjects.length], target: (j > 2 ? classes : groups)[(i + j * 4) % (j > 2 ? classes : groups).length].id,
      course: '', source: 'Школьный справочник'
    }))
  }));
  const colors = ['#e4eaff','#f5e6f3','#e2eee6','#faecd6','#e3edf1'];
  const state = { view:'board', grade:'', subject:'', query:'', archived:false, roster:'', rosterQuery:'', pupil:'',
    tQuery:'', tSubject:'', tGrade:'', tStatus:'Активен', tView:'table', teacher:'t0', tab:'default',
    week:'template', day:0, stage:0, formValues:null, form:'teacher', entity:'t0', returnTo:'profile', report:'', groupFilter:'', classFilter:'' };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[char]);
  const fold = value => String(value ?? '').trim().toLocaleLowerCase('ru');
  const className = id => classes.find(c=>c.id===id)?.name || id;
  const includes = (text, query) => fold(text).includes(fold(query));
  const initials = name => name.split(' ').slice(0,2).map(word => word[0]).join('');
  const avatar = (name, n = 0) => '<span class="dr-avatar" aria-hidden="true" style="--dr-avatar:' + colors[n % colors.length] + '">' + esc(initials(name)) + '</span>';
  const action = (label, name, value = '', css = '', extra = '') => `<button type="button" class="dr-button ${css}" data-dr-act="${name}" data-dr-value="${esc(value)}" ${extra}>${esc(label)}</button>`;
  const disabled = (label, reason) => `<div class="dr-unavailable"><button class="dr-button" type="button" disabled>${esc(label)}</button><small>${esc(reason)}</small></div>`;
  const empty = (msg) => `<p class="dr-empty">${esc(msg)}</p>`;
  const menu = (label, body) => `<details class="dr-menu"><summary aria-label="${esc(label)}"><svg aria-hidden="true" width="18" height="18" viewBox="0 0 18 18" fill="currentColor"><circle cx="3" cy="9" r="1.4"/><circle cx="9" cy="9" r="1.4"/><circle cx="15" cy="9" r="1.4"/></svg></summary><div>${body}</div></details>`;
  const select = (key, label, options, value = state[key], extra = '') => `<select aria-label="${esc(label)}" data-dr-state="${key}" ${extra}>${options.map(item => {
    const [id, caption] = Array.isArray(item) ? item : [item,item];
    return `<option value="${esc(id)}" ${String(value)===String(id) ? 'selected' : ''}>${esc(caption)}</option>`;
  }).join('')}</select>`;
  const search = (key, label) => `<input data-dr-state="${key}" aria-label="${esc(label)}" type="search" placeholder="${esc(label)}" value="${esc(state[key])}">`;
  const tabs = (key, items, label, current = state[key]) => `<nav class="dr-tabs" aria-label="${label}">${items.map(([value, caption]) => action(caption, key, value, current === value ? 'active' : '', current === value ? 'aria-current="true"' : '')).join('')}</nav>`;
  const head = (title, sub, controls) => `<header class="dr-head"><div><h1>${esc(title)}</h1><p>${esc(sub)}</p></div><div class="dr-actions">${controls}</div></header>`;
  const wrap = (variant, content) => `<div class="dr dr-${variant}">${content}${state.report ? `<p class="dr-feedback" role="status">${esc(state.report)}</p>` : ''}</div>`;
  const itemById = id => [...classes, ...groups].find(g => g.id === id);
  const activeTeacher = () => teachers.find(t => t.id === state.teacher) || teachers[0];
  const roster = (item) => people.filter(person => item.type === 'class' ? person.classId === item.id : person.groupIds.includes(item.id));
  const showRoster = item => {
    const entries = roster(item).filter(p => includes(p.name, state.rosterQuery));
    return `<section class="dr-roster" aria-label="Состав ${esc(item.name)}"><header><div><h2>${esc(item.name)}</h2><p>${roster(item).length} в составе · ${item.type === 'class' ? 'Административный класс' : 'Учебная группа'}</p></div>${action('Закрыть состав','roster')}</header><div class="dr-tools">${search('rosterQuery','Поиск по составу')}${item.archived ? '' : action(item.type === 'class' ? 'Перевести в класс' : 'Добавить в группу','form','membership:' + item.id,'primary')}</div>
    <ol class="dr-roster-list">${entries.map(p => `<li draggable="${item.type === 'group' && !item.archived}" data-dr-drag="${esc(p.id)}">${action(p.name,'pupil',p.id,'link')}<span>${esc(className(p.classId))}</span>${item.type === 'group' && !item.archived ? menu('Действия с участником', action('Переместить','form','move:' + item.id + ':' + p.id) + action('Исключить','form','remove:' + item.id + ':' + p.id)) : ''}</li>`).join('')}</ol>${entries.length ? '' : empty('В составе нет учеников по этому запросу.')}</section>`;
  };
  const groupTile = (item, v) => {
    const count = roster(item).length;
    return `<article class="dr-group ${item.id === state.roster ? 'selected' : ''}" data-dr-drop="${item.archived || item.type !== 'group' ? '' : esc(item.id)}">
    <div class="dr-group-title">${action(item.name,'roster',item.id,'link')}${item.type === 'group' ? menu('Действия с группой ' + item.name, action('Открыть состав','roster',item.id) + action('Настройки группы','form','group:' + item.id) + action(item.archived ? 'Восстановить…' : 'Архивировать…','form','archive:' + item.id)) : ''}</div>
    <div class="dr-group-meta"><strong>${count}</strong><span>в составе${item.archived ? ' · архив' : ''}</span></div>
    ${v==='S' && item.id === state.roster ? showRoster(item) : ''}</article>`;
  };
  function peopleScreen(v) {
    const gradeOptions = [['','Все классы и параллели'], ...grades.map(n => [n,n + '-я параллель']), ...classes.map(c => [c.id,c.name])];
    const groupMatches = item => (!state.grade || String(item.grade)===state.grade || (item.type==='class' && item.id===state.grade) || (item.type==='group' && String(item.grade)===state.grade.slice(0,-1))) &&
      (!state.query || includes(item.name,state.query) || roster(item).some(p => includes(p.name,state.query)));
    const activeGroups = groups.filter(g => g.archived===state.archived && (!state.subject || g.subject===state.subject) && groupMatches(g));
    const filteredClasses = classes.filter(groupMatches);
    const selected = itemById(state.roster);
    const levels = grades.map(n => {
      const schoolClasses = filteredClasses.filter(c => c.grade===n);
      const schoolGroups = activeGroups.filter(g => g.grade===n);
      return schoolClasses.length || schoolGroups.length ? `<section class="dr-level"><h2>${n}-я параллель</h2><div class="dr-level-content">${schoolClasses.length ? `<div class="dr-labeled-track"><h3>Адм. классы</h3><div class="dr-class-pair">${schoolClasses.map(c => groupTile(c,v)).join('')}</div></div>` : ''}${schoolGroups.length?`<div class="dr-labeled-track"><h3>Учебные группы</h3><div class="dr-group-grid">${schoolGroups.map(g=>groupTile(g,v)).join('')}</div></div>`:''}</div></section>` : '';
    }).filter(Boolean);
    const tools = `<div class="dr-tools">${search('query','Поиск групп и учеников')}${select('grade','Класс или параллель',gradeOptions)}${select('subject','Предмет или направление',[['','Все предметы'],...subjects])}<label class="dr-check"><input type="checkbox" data-dr-state="archived" ${state.archived?'checked':''}>Архивные группы</label></div>`;
    const instructor = `<div class="dr-overline"><strong>Учебные группы</strong><span>${activeGroups.length} на доске</span></div>`;
    const baseline = `${instructor}${grades.map(n => activeGroups.some(g=>g.grade===n) ? `<section class="dr-level"><h2>${n}-я параллель</h2><div class="dr-group-grid">${activeGroups.filter(g=>g.grade===n).map(g=>groupTile(g,v)).join('')}</div></section>` : '').join('') || empty('Нет подходящих учебных групп.')}
    <details class="dr-shelf" open><summary>Административные классы · ${filteredClasses.length}</summary><div class="dr-group-grid">${filteredClasses.map(c=>groupTile(c,v)).join('')}</div></details>`;
    const tracks = v==='D' ? `<section class="dr-track"><h2>Административные классы</h2><div class="dr-track-grid">${filteredClasses.map(c=>groupTile(c,v)).join('') || empty('Классы не найдены.')}</div></section>${instructor}<div class="dr-subject-board">${subjects.map(sub => {
      const lanes=activeGroups.filter(g=>g.subject===sub);
      return lanes.length ? `<section><h2>${esc(sub)}</h2>${lanes.map(g=>groupTile(g,v)).join('')}</section>` : '';
    }).join('') || empty('Нет подходящих учебных групп.')}</div>` : levels.join('') || empty('Классы и учебные группы не найдены.');
    const pending = state.grade && state.subject && !state.archived ? `<details class="dr-shelf"><summary>Нераспределённые ученики</summary><p>Список ориентировочный. Перед распределением нужно подтвердить полноту состава параллели по этому предмету.</p>${disabled('Подтвердить полноту состава','Требуется проверка данных школьного справочника.')}</details>` : '';
    const list = people.filter(p=>includes(p.name,state.query) && (!state.classFilter||p.classId===state.classFilter) && (!state.groupFilter||p.groupIds.includes(state.groupFilter)));
    const studentView = `<div class="dr-tools">${search('query','Поиск по ФИО')}${select('classFilter','Административный класс',[['','Все классы'],...classes.map(c=>[c.id,c.name])])}${select('groupFilter','Учебная группа',[['','Все учебные группы'],...groups.filter(g=>!g.archived).map(g=>[g.id,g.name])])}</div>
    <div class="dr-table-wrap"><table><thead><tr><th scope="col">Ученик</th><th scope="col">Класс</th><th scope="col">Учебные группы</th></tr></thead><tbody>${list.map(p=>`<tr><td>${action(p.name,'pupil',p.id,'link')}</td><td>${esc(className(p.classId))}</td><td><details><summary>${p.groupIds.length} в составе</summary>${p.groupIds.map(id=>action(itemById(id)?.name||id,'roster',id,'link')).join('')}</details></td></tr>`).join('')}</tbody></table>${list.length?'':empty('Ученики не найдены.')}</div>`;
    const p = people.find(p=>p.id===state.pupil);
    const inspector = p ? `<section class="dr-roster"><header><div><h2>${esc(p.name)}</h2><p>${esc(className(p.classId))} · административный класс</p></div>${action('Закрыть карточку','pupil')}</header><div class="dr-tools">${action('Изменить класс','form','student:' + p.id,'primary')}${action('Добавить в учебную группу','form','join:' + p.id)}</div><ul>${p.groupIds.map(id=>`<li>${action(itemById(id)?.name||id,'roster',id,'link')}${action('Исключить','form','remove:' + id + ':' + p.id)}</li>`).join('') || '<li>Учебных групп пока нет</li>'}</ul></section>` : '';
    return wrap(v, head('Ученики и группы', `${people.length} учеников · ${classes.length} классов · 2026/27`, action('Добавить ученика','form','student:','primary') + menu('Действия со справочником',action('Добавить учебную группу','form','group:') + action('Добавить класс','form','class:') + disabled('Обновить данные школы','Для обновления нужна школьная сессия.'))) +
      tabs('view',[['board','Доска'],['students','Ученики']],'Раздел справочника') + (state.view==='board' ? tools : '')+
      `<div class="dr-board-layout ${(p||selected&&v!=='S')?'has-roster':''}"><div>${state.view==='students'?studentView:(v==='S'?baseline:tracks)+pending}</div>${inspector || (selected&&v!=='S'?showRoster(selected):'')}</div>`);
  }
  function teachersScreen(v) {
    const options = [['','Все классы и параллели'], ...grades.map(n=>[n,n+'-я параллель']), ...classes.map(c=>[c.id,c.name])];
    const rows = teachers.filter(t=>includes(t.name,state.tQuery) && (!state.tStatus||t.status===state.tStatus) && (!state.tSubject||t.subjects.includes(state.tSubject)) &&
      (!state.tGrade||t.assignments.some(a=>{const g=itemById(a.target); return a.target===state.tGrade||String(g?.grade)===state.tGrade;})));
    const teacherName = t => `<span class="dr-person">${avatar(t.name,teachers.indexOf(t))}${action(t.name,'teacher',t.id,'link')}</span>`;
    const tools = `<div class="dr-tools">${search('tQuery','Поиск преподавателя по ФИО')}${select('tSubject','Предмет',[['','Все предметы'],...subjects])}${select('tGrade','Класс или параллель',options)}${select('tStatus','Статус',[['','Любой статус'],'Активен','Неактивен'])}${tabs('tView',[['table','Таблица'],['cards','Карточки']],'Вид справочника',state.tView)}</div>`;
    const table = `<div class="dr-table-wrap"><table><thead><tr><th scope="col">Преподаватель</th><th scope="col">Предметы</th><th scope="col">Назначения</th>${v==='S'?'<th scope="col">Недельная нагрузка</th>':''}<th scope="col">Статус</th></tr></thead><tbody>${rows.map(t=>`<tr><td>${teacherName(t)}</td><td>${esc(t.subjects.join(' · ')) || 'Не указаны'}</td><td>${t.assignments.length}</td>${v==='S'?'<td>Нет данных</td>':''}<td><span class="dr-status ${t.status==='Неактивен'?'inactive':''}">${esc(t.status)}</span></td></tr>`).join('')}</tbody></table></div>`;
    const cards = list => `<div class="dr-staff-grid">${list.map(t=>`<article>${teacherName(t)}<span>${esc(t.subjects.join(' · ')) || 'Предметы не указаны'}</span><div><strong>${t.assignments.length}</strong> назначений <span class="dr-status ${t.status==='Неактивен'?'inactive':''}">${esc(t.status)}</span></div></article>`).join('')}</div>`;
    const catalog = v==='D' ? `<div class="dr-staff-catalog">${[...subjects,'Предметы не указаны'].map(s=>{
      const matches=rows.filter(t=>(state.tSubject ? t.subjects.includes(state.tSubject) && s===state.tSubject : (t.subjects[0]||'Предметы не указаны')===s));
      return matches.length ? `<section class="dr-subject-register"><h2>${esc(s)} <small>${matches.length}</small></h2><ol>${matches.map(t=>`<li>${teacherName(t)}<div><strong>${t.assignments.length} назнач.</strong><small>${esc(t.assignments.slice(0,2).map(a=>itemById(a.target)?.name||a.target+'-я параллель').join(' · ')||'Назначений нет')}</small></div></li>`).join('')}</ol></section>` : '';
    }).join('')}</div>` : cards(rows);
    const status = menu('Действия со справочником', action('Обновить тестовые данные','refresh') + disabled('Обновить школьный справочник','Нужны действующая школьная сессия и связь со справочником.'));
    return wrap(v, head('Преподаватели', `${teachers.length} в справочнике · ${teachers.filter(t=>t.status==='Активен').length} активных`, action('Создать преподавателя','form','teacher:','primary')+(v==='S'?action('Обновить','refresh'):'')+status) + tools +
      `<p class="dr-result" role="status">Показано ${rows.length} из ${teachers.length}</p>` + (rows.length ? (state.tView==='cards'||(v==='D'&&!state.tViewChosen)?catalog:table) : empty('По этим фильтрам преподавателей нет.')));
  }
  const tabItems = [['main','Основное'],['assignments','Группы и назначения'],['schedule','Расписание'],['availability','Ограничения']];
  const weekOptions = [['template','Базовый шаблон'],['2026-11-23','23–27 ноября · опубликовано'],['missing','30 ноября – 4 декабря · нет снимка']];
  function assignmentPanel(t) {
    return `<section class="dr-profile-panel"><header><div><h2>Группы и назначения</h2><p>Преподаватель по умолчанию для класса, группы или параллели</p></div>${action('Добавить назначение','form','assignment:','primary')}</header><div class="dr-assignment-list">${t.assignments.map(a=>{
      const g=itemById(a.target);
      return `<article><span class="dr-target">${esc(g?.type==='class' ? g.name : g?.grade || a.target)}</span><div><b>${esc(a.subject)}${a.course?' · '+esc(a.course):''}</b><span>${esc(g?.name||a.target+'-я параллель')}</span><details><summary>Источник назначения</summary><p>${esc(a.source)} · действует</p></details></div><div>${g?action('Открыть состав','openGroup',g.id,'link'):''}${menu('Действия с назначением',action('Изменить назначение','form','assignment:'+a.id)+action('Снять назначение','form','unassign:'+a.id))}</div></article>`;
    }).join('') || empty('Действующих назначений пока нет.')}</div></section>`;
  }
  function schedulePanel(t, mini = false) {
    const schedule = t.assignments.slice(0, mini ? 3 : 6);
    const constructor = disabled('Открыть конструктор','Переход доступен в административном приложении.');
    return `<section class="dr-profile-panel dr-agenda"><header><div><h2>${mini?'Уроки по базовому шаблону':'Расписание'}</h2><p>${mini?'Первые занятия по дням недели':state.week==='template'?'Базовый шаблон':state.week==='missing'?'Опубликованный снимок отсутствует':'Опубликованная неделя'}</p></div>${mini?action('Всё расписание','tab','schedule'):select('week','Источник расписания',weekOptions)}</header>
      ${state.week==='missing'&&!mini ? empty('На эту неделю нет опубликованного снимка. Выберите базовый шаблон или другую неделю.') : schedule.length ? `<ol>${schedule.map((a,i)=>`<li><time>${['Пн · 09:00','Вт · 10:00','Ср · 11:00','Чт · 09:00','Пт · 11:00','Пт · 12:00'][i]}</time><div><b>${esc(a.subject)}</b><span>${esc(itemById(a.target)?.name || a.target+'-я параллель')} · каб. ${21+i%3}</span></div></li>`).join('')}</ol>` : empty('В выбранном источнике нет занятий этого преподавателя.')}${mini?'':constructor}</section>`;
  }
  function weekOverview(t) {
    const days=['Понедельник','Вторник','Среда','Четверг','Пятница'];
    const slots=['09:00','10:00','11:00','09:00','11:00','12:00'];
    const lessons=t.assignments.slice(0,slots.length).map((a,i)=>({...a,day:Math.min(i,4),time:slots[i],room:21+i%3}));
    const dayLessons=lessons.filter(a=>a.day===state.day);
    return `<section class="dr-week-overview"><header><div><h2>Неделя преподавателя</h2><p>Базовый шаблон · выберите день</p></div>${action('Источники расписания','tab','schedule')}</header>
      <div class="dr-week-strip" aria-label="День недели">${days.map((day,i)=>`<button type="button" data-dr-act="day" data-dr-value="${i}" aria-pressed="${i===state.day}"><span>${day}</span><strong>${lessons.filter(a=>a.day===i).length}</strong><small>занятий</small></button>`).join('')}</div>
      <div class="dr-day-detail"><h3>${days[state.day]}</h3>${dayLessons.length?`<ol>${dayLessons.map(a=>`<li><time>${a.time}</time><b>${esc(a.subject)}</b><span>${esc(itemById(a.target)?.name||a.target+'-я параллель')} · каб. ${a.room}</span></li>`).join('')}</ol>`:empty('В этот день в шаблоне нет занятий.')}</div>
    </section>`;
  }
  function profileScreen(v) {
    const t=activeTeacher();
    const tab=state.tab==='default' ? (v==='S'?'main':'activity') : state.tab;
    const info = `<section class="dr-profile-panel"><header><div><h2>Основная информация</h2><p>Предметы и комментарий преподавателя</p></div></header><dl class="dr-profile-facts"><div><dt>ФИО</dt><dd>${esc(t.name)}</dd></div><div><dt>Статус</dt><dd>${esc(t.status)}</dd></div><div><dt>Предметы</dt><dd>${esc(t.subjects.join(' · ')) || 'Не указаны'}</dd></div><div><dt>Комментарий</dt><dd>${esc(t.note) || 'Не указан'}</dd></div></dl></section>`;
    const assignment = assignmentPanel(t);
    const availability = `<section class="dr-profile-panel"><h2>Ограничения доступности</h2><p>В справочнике нет отдельных записей об ограничениях преподавателя.</p><p>Уточнения можно записать в комментарии карточки. Занятость сверяйте с расписанием.</p>${action('Открыть расписание','tab','schedule')}</section>`;
    const activity=v==='D'?`<div class="dr-profile-week">${weekOverview(t)}${assignment}</div>`:`<div class="dr-activity">${assignment+schedulePanel(t,true)}</div>`;
    const content = { main:info, assignments:assignment, schedule:schedulePanel(t), availability, activity };
    const controls = action('Редактировать основное','form','teacher:'+t.id,'primary') + menu('Действия с профилем',action('Посмотреть приложение','preview',t.id) + disabled('Обновить данные школы','Для обновления нужна школьная сессия.'));
    return wrap(v, action('К преподавателям','navigate','teachers','link dr-back') + `<header class="dr-profile-head">${avatar(t.name,teachers.indexOf(t))}${head(t.name,t.subjects.join(' · ') || 'Предметы не указаны',controls)}<span class="dr-status ${t.status==='Неактивен'?'inactive':''}">${esc(t.status)}</span></header>`+
      (v==='S'?action('Посмотреть приложение','preview',t.id):'') + tabs('tab',v==='S'?tabItems:[['activity','Школьная деятельность'],...tabItems],'Карточка преподавателя',tab)+(content[tab]||info));
  }
  const field = (caption, name, value = '', config = '') => `<label class="dr-field" for="dr-${name}">${caption}<input id="dr-${name}" name="${name}" value="${esc(value)}" ${config}><small class="dr-field-error" id="dr-error-${name}" hidden></small></label>`;
  const picker = (caption, name, options, value='', config='') => `<label class="dr-field" for="dr-${name}">${caption}<select id="dr-${name}" name="${name}" ${config}>${options.map(item=>{
    const [id,label]=Array.isArray(item)?item:[item,item];
    return `<option value="${esc(id)}" ${id===value?'selected':''}>${esc(label)}</option>`;
  }).join('')}</select><small class="dr-field-error" id="dr-error-${name}" hidden></small></label>`;
  const choice = (label, name, value, selected=false) => `<label class="dr-choice"><input type="checkbox" name="${name}" value="${esc(value)}" ${selected?'checked':''}>${esc(label)}</label>`;
  function formsScreen(v) {
    const t=activeTeacher();
    const [id, pupilId]=state.entity.split(':');
    const individual=people.find(p=>p.id===id);
    const p=people.find(p=>p.id===pupilId);
    const g=itemById(id);
    const a=t.assignments.find(a=>a.id===id);
    const type=state.form;
    const creating=!id;
    const classOptions=[['','Выберите класс'],...classes.map(c=>[c.id,c.name])];
    const groupOptions=[['','Выберите учебную группу'],...groups.filter(x=>!x.archived).map(x=>[x.id,x.name])];
    const studentOptions=list=>[['','Выберите ученика'],...list.map(p=>[p.id,p.name+' · '+className(p.classId)])];
    const teacher = teachers.find(t=>t.id===id);
    const scheduledTarget = itemById(a?.target);
    const audienceDefault = scheduledTarget ? scheduledTarget.type : a ? 'parallel' : 'group';
    const title = {teacher: creating?'Создать преподавателя':'Редактирование преподавателя',student: creating?'Добавить ученика':'Административный класс ученика',
      group:creating?'Добавить учебную группу':'Настройки группы',class:'Добавить административный класс',assignment:creating?'Добавить назначение':'Изменить назначение',
      membership:g?.type==='class'?'Перевести ученика в класс':'Добавить ученика в группу',move:'Переместить ученика',remove:'Исключить из группы',
      archive:g?.archived?'Восстановить группу':'Архивировать группу',join:'Добавить в учебную группу',unassign:'Снять назначение'}[type];
    let fields='';
    if(type==='teacher') fields = field('ФИО', 'name', teacher?.name, 'required autocomplete="off"') + picker('Статус','status',['Активен','Неактивен'],teacher?.status)+
      `<fieldset class="dr-subject-picker"><legend>Предметы школы</legend><input type="search" placeholder="Найти предмет" aria-label="Найти предмет" data-dr-subject-search>${subjects.map(s=>choice(s,'subjects',s,teacher?.subjects.includes(s))).join('')}</fieldset><label class="dr-field">Комментарий<textarea name="note" rows="3">${esc(teacher?.note)}</textarea></label>`;
    if(type==='student') fields=(individual?`<p class="dr-form-context">${esc(individual.name)} · текущий класс ${esc(individual.classId)}</p>`:field('ФИО','name','','required autocomplete="off"'))+picker('Административный класс','classId',classOptions,individual?.classId,'required');
    if(type==='class'||type==='group') fields=field('Название','name',g?.name,'required')+
      picker('Тип группы','type',[['group','Учебная группа'],['class','Административный класс'],['exam','Экзаменационная группа']],type==='class'?'class':g?.exam?'exam':'group',g?'disabled':'data-dr-group-type') +
      picker('Параллель','grade',[['','Выберите параллель'],...grades.map(n=>[String(n),n+'-я параллель'])],String(g?.grade||''),'required')+
      `<div data-dr-group-subject ${type==='class'?'hidden':''}>${picker('Предмет или направление','subject',[['','Выберите предмет'],...subjects],g?.subject,type==='class'?'':'required')}</div>`+
      picker('Преподаватель по умолчанию','defaultTeacher',[['','Не назначен'],...teachers.filter(t=>t.status==='Активен').map(t=>[t.id,t.name])],g?.defaultTeacher)+
      `<label class="dr-field">Комментарий<textarea name="groupComment" rows="3">${esc(g?.comment)}</textarea></label>`+
      (g ? action(g.archived?'Связи архивной группы':'Архивировать…','form','archive:'+g.id) : '')+
      (!g ? `<label class="dr-field">Добавить учеников (необязательно)<select name="members" multiple size="5">${studentOptions(people).slice(1).map(([id,name])=>`<option value="${id}">${esc(name)}</option>`).join('')}</select><small>Для нескольких учеников используйте множественный выбор.</small></label>`:'');
    if(type==='assignment') fields=picker('Предмет','subject',[['','Выберите предмет'],...subjects],a?.subject,'required')+
      picker('Аудитория','audience',[['group','Учебная группа'],['class','Административный класс'],['parallel','Вся параллель']],audienceDefault,'data-dr-assignment')+
      picker('Параллель','grade',[['','Выберите параллель'],...grades.map(n=>[String(n),n+'-я параллель'])],String(scheduledTarget?.grade||(!scheduledTarget?a?.target:'' )||''),'required data-dr-assignment')+
      `<div data-dr-target-wrap>${picker('Группа или класс','target',[...groupOptions,...classOptions.slice(1)],a?.target,'required')}</div>`+
      picker('Курс / формат (необязательно)','course',['','ОГЭ','ЕГЭ'],a?.course);
    if(type==='membership') fields=`<p class="dr-form-context">${esc(g?.name)}</p>`+
      picker('Ученик','student',studentOptions(people.filter(p=>!roster(g).includes(p))),'','required');
    if(type==='join') fields=`<p class="dr-form-context">${esc(individual?.name)}</p>`+
      picker('Учебная группа','target',groupOptions.filter(([id])=>!individual?.groupIds.includes(id)),'','required');
    if(type==='move') fields=`<p class="dr-form-context">${esc(p?.name)} · ${esc(g?.name)}</p>`+
      picker('Новая группа по тому же предмету','target',groupOptions.filter(([id])=>!id||id!==g.id&&itemById(id)?.subject===g.subject&&!p?.groupIds.includes(id)),'','required');
    if(type==='remove'||type==='unassign') fields=`<p class="dr-form-context">${esc(type==='remove'?p?.name+' · '+g?.name:a?.subject+' · '+(itemById(a?.target)?.name||a?.target))}</p>`;
    if(type==='archive') fields=`<p class="dr-form-context">${esc(g?.name)} · ${g?roster(g).length:0} в составе.</p><p>Архив сохраняет состав и историю. Проверка занятий, журналов и назначений требует данных школы.</p>${disabled('Проверить связи и продолжить','В синтетических данных нет подтверждённого отчёта о связях.')}`;
    const firstFields={teacher:['name','status'],student:individual?['classId']:['name'],assignment:['subject'],group:['name','type'],class:['name','type']};
    const staged=v==='D'&&Boolean(firstFields[type])&&!(type==='student'&&individual);
    const secondStage={teacher:'Предметы и комментарий',assignment:'Аудитория и формат',student:'Административный класс',group:'Параметры и состав',class:'Параметры и состав'};
    const progress=staged?`<ol class="dr-stage-progress" aria-label="Шаги формы">${['Основное',secondStage[type],'Проверка и сохранение'].map((label,i)=>`<li ${state.stage===i?'aria-current="step"':''}><span>${i+1}</span>${esc(label)}</li>`).join('')}</ol>`:'';
    const summaryFields=staged&&state.stage===2?'<div class="dr-form-review" tabindex="-1"><h2>Проверьте введённые данные</h2><dl></dl></div>':'';
    const stepAttributes=staged?`data-dr-staged data-first-fields="${firstFields[type].join(',')}"`:'';
    const label=staged&&state.stage<2 ? state.stage===0?'Далее':'Проверить' : type==='remove'?'Исключить':type==='unassign'?'Снять назначение':'Сохранить';
    const buttons=`${staged&&state.stage?action('Назад','stepBack'):action('Отмена','cancel')}${type==='archive'?'':`<button class="dr-button primary" type="submit">${label}</button>`}`;
    const side = `<aside class="dr-form-aside"><h2>${esc(type==='teacher'?teacher?.name||'Новый преподаватель':type==='assignment'?t.name:g?.name||individual?.name||'Школьный справочник')}</h2><p>${esc(type==='assignment'?'Назначение задаёт преподавателя по умолчанию. Состав расписания проверяется отдельно.':type==='student'?'Административный класс и учебные группы меняются отдельно.':'Изменения применяются к данным этого прототипа.')}</p></aside>`;
    return wrap(v, action(state.returnTo==='profile'?'К профилю':'К справочнику','cancel','','link dr-back') + head(title,'Школьный справочник', '')+
      `${progress}<div class="dr-form-layout ${staged?'dr-form-staged':''}"><form class="dr-form" data-dr-form ${stepAttributes} novalidate><div class="dr-errors" role="alert" tabindex="-1" hidden></div><div class="dr-form-fields">${fields}</div>${summaryFields}<footer>${buttons}</footer></form>${v==='S'||staged?'':side}</div>`);
  }
  function openForm(value, source) {
    const [type, ...entity]=value.split(':');
    Object.assign(state,{form:type,entity:entity.join(':'),returnTo:source,report:'',stage:0,formValues:null});
    window.schoolGo('forms');
  }
  function redraw(target) {
    window.schoolDraw();
    if (!target) return;
    const replacement=document.querySelector(`.dr [data-dr-state="${target.dataset.drState}"]`);
    replacement?.focus({preventScroll:true});
    if(target.type==='search') replacement?.setSelectionRange(target.selectionStart,target.selectionEnd);
  }
  document.addEventListener('click', event=>{
    const button=event.target.closest('.dr [data-dr-act]');
    if (!button) return;
    const {drAct:act,drValue:value}=button.dataset;
    const root=button.closest('.dr');
    state.report='';
    if(act==='form') return openForm(value,root.querySelector('.dr-form')?state.returnTo:root.querySelector('.dr-profile-head')?'profile':root.querySelector('.dr-staff-catalog,.dr-result')?'teachers':'people');
    if(act==='teacher'){
      Object.assign(state,{teacher:value,tab:'default',week:'template',day:0,form:'teacher',entity:value,returnTo:'profile'});
      return window.schoolGo('profile');
    }
    if(act==='openGroup'){
      Object.assign(state,{view:'board',query:'',grade:'',subject:'',roster:value,rosterQuery:'',pupil:'',archived:Boolean(itemById(value)?.archived)});
      return window.schoolGo('people');
    }
    if(act==='preview') return window.schoolPreviewTeacher(activeTeacher().name);
    if(act==='navigate'||act==='cancel') return window.schoolGo(act==='cancel'?state.returnTo:value);
    if(act==='roster') Object.assign(state,{roster:state.roster===value?'':value,rosterQuery:'',pupil:'',view:'board'});
    if(act==='pupil') Object.assign(state,{pupil:value,roster:''});
    if(act==='refresh') state.report='Справочник обновлён из тестового набора.';
    if(act==='day') state.day=Number(value);
    if(act==='stepBack') {
      state.formValues=new FormData(root.querySelector('.dr-form'));
      state.stage--;
      return redraw();
    }
    if(['tab','view','tView'].includes(act)) {
      state[act]=value;
      if(act==='tab'&&value==='schedule') state.week='template';
      if(act==='view') Object.assign(state,{query:'',roster:'',pupil:''});
      if(act==='tView') state.tViewChosen=true;
    }
    redraw();
    const matching=[...document.querySelectorAll('.dr [data-dr-act]')].find(b=>b.dataset.drAct===act&&b.dataset.drValue===value);
    matching?.focus({preventScroll:true});
  });
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape') return;
    const details=event.target.closest('.dr-menu[open]');
    if(details){details.open=false;details.querySelector('summary')?.focus();}
  });
  function updateFormTargets(form) {
    if(!form?.elements.audience) return;
    const audience=form.elements.audience.value;
    const grade=form.elements.grade.value;
    const target=form.elements.target;
    const previous=target.value;
    const choices=(audience==='group'?groups.filter(g=>!g.archived):classes).filter(g=>String(g.grade)===grade);
    target.replaceChildren(new Option(audience==='class'?'Выберите класс':'Выберите группу',''),...choices.map(g=>new Option(g.name,g.id)));
    if(choices.some(g=>g.id===previous)) target.value=previous;
    target.required=audience!=='parallel';
    target.disabled=audience==='parallel';
    form.querySelector('[data-dr-target-wrap]').hidden=audience==='parallel';
  }
  new MutationObserver(records=>{
    if(!records.some(record=>[...record.addedNodes].some(node=>node.nodeType===1&&(node.matches('.dr')||node.querySelector('.dr'))))) return;
    const form=document.querySelector('.dr-form');
    if(!form) return;
    const staged=form.hasAttribute('data-dr-staged');
    if(staged&&state.formValues) {
      for(const field of form.elements) {
        if(!field.name||field.name==='target') continue;
        const values=state.formValues.getAll(field.name);
        if(field.type==='checkbox') field.checked=values.includes(field.value);
        else if(field.multiple) for(const option of field.options) option.selected=values.includes(option.value);
        else if(values.length) field.value=values[0];
      }
    }
    updateFormTargets(form);
    if(!staged) return;
    if(state.formValues?.has('target')) form.elements.target.value=state.formValues.get('target');
    const layout=form.querySelector('.dr-form-fields');
    const first=form.dataset.firstFields.split(',');
    const groupSubject=form.querySelector('[data-dr-group-subject]');
    if(groupSubject) {
      const schoolClass=form.elements.type.value==='class';
      groupSubject.hidden=schoolClass;
      form.elements.subject.disabled=schoolClass;
      form.elements.subject.required=!schoolClass;
    }
    for(const group of layout.children) {
      const input=group.querySelector('[name]');
      group.hidden=state.stage===2||group.hidden||(input&&(state.stage===0)!==first.includes(input.name));
    }
    if(state.stage===2) {
      const seen=new Set();
      const review=form.querySelector('.dr-form-review');
      const summary=review.querySelector('dl');
      for(const field of form.elements) {
        if(field.disabled||!field.name||seen.has(field.name)||!state.formValues?.has(field.name)) continue;
        seen.add(field.name);
        const values=state.formValues.getAll(field.name).filter(Boolean);
        const label=field.name==='subjects'?'Предметы школы':field.labels[0]?.firstChild.textContent.trim();
        const output=field.options?values.map(value=>Array.from(field.options).find(o=>o.value===value)?.text||value):values;
        if(label&&output.length) summary.insertAdjacentHTML('beforeend',`<div><dt>${esc(label)}</dt><dd>${esc(output.join(' · '))}</dd></div>`);
      }
      review.focus({preventScroll:true});
    } else if(state.stage>0) layout.querySelector(':scope > :not([hidden]) :is(input,select,textarea)')?.focus({preventScroll:true});
  }).observe(document.body,{subtree:true,childList:true});
  function filter(event) {
    const target=event.target;
    const key=target.closest('.dr [data-dr-state]')?.dataset.drState;
    if(!key) return;
    state[key]=target.type==='checkbox'?target.checked:target.value;
    if(key==='archived') state.roster='';
    redraw(target);
  }
  document.addEventListener('input',event=>{
    if(event.target.type==='search') filter(event);
    if(event.target.matches('.dr [data-dr-subject-search]')) {
      for(const item of event.target.parentElement.querySelectorAll('.dr-choice')) item.hidden=!includes(item.textContent,event.target.value);
    }
  });
  document.addEventListener('change',event=>{
    if(event.target.type!=='search') filter(event);
    if(event.target.matches('.dr [data-dr-assignment]')) updateFormTargets(event.target.form);
    if(event.target.matches('.dr [data-dr-group-type]')){
      const wrap=event.target.form.querySelector('[data-dr-group-subject]');
      wrap.hidden=event.target.value==='class';
      wrap.querySelector('select').required=!wrap.hidden;
    }
  });
  function validate(field) {
    const error=field.form?.querySelector('#dr-error-'+field.name);
    if(!error||field.disabled) return true;
    const valid=field.checkValidity() && (!field.required || Boolean(field.value.trim()));
    error.hidden=valid;
    error.textContent=valid?'':field.tagName==='SELECT'?'Выберите значение.':'Заполните это поле.';
    if(valid) field.removeAttribute('aria-invalid');
    else field.setAttribute('aria-invalid','true');
    field.setAttribute('aria-describedby',error.id);
    return valid;
  }
  document.addEventListener('focusout',event=>{
    if(event.target.matches('.dr-form [required]')) validate(event.target);
  });
  document.addEventListener('submit',event=>{
    const form=event.target.closest('.dr-form');
    if(!form) return;
    event.preventDefault();
    if(state.form==='archive') return;
    const staged=form.hasAttribute('data-dr-staged');
    const invalid=[...form.querySelectorAll('[required]')].filter(f=>(!staged||state.stage===2||!f.closest('[hidden]'))&&!validate(f));
    const errors=form.querySelector('.dr-errors');
    if(invalid.length){
      errors.hidden=false;
      errors.innerHTML='<strong>Проверьте обязательные поля</strong><ul>'+invalid.map(f=>`<li><a href="#${f.id}">${esc(f.labels[0].firstChild.textContent.trim())}</a></li>`).join('')+'</ul>';
      errors.focus();
      return;
    }
    const values=new FormData(form);
    if(staged&&state.stage<2) {
      state.formValues=values;
      state.stage++;
      return redraw();
    }
    const value=key=>String(values.get(key)||'').trim();
    const [id,pupilId]=state.entity.split(':');
    const t=activeTeacher();
    const type=state.form;
    let report='Изменения сохранены в прототипе.';
    if(type==='teacher'){
      const existing=teachers.find(t=>t.id===id);
      const fields={name:value('name'),subjects:values.getAll('subjects'),status:value('status'),note:value('note')};
      if(existing) Object.assign(existing,fields);
      else {
        const newId='t'+teachers.length;
        teachers.push({id:newId,...fields,assignments:[]});
        state.teacher=newId;
      }
    }
    if(type==='student'){
      const existing=people.find(p=>p.id===id);
      if(existing) existing.classId=value('classId');
      else people.push({id:'p-new-'+people.length,name:value('name'),classId:value('classId'),groupIds:[]});
    }
    if(type==='group'||type==='class'){
      const existing=itemById(id);
      const selectedType=value('type')||(existing?.exam?'exam':existing?.type)||'group';
      const groupType=selectedType==='exam'?'group':selectedType;
      const fields={name:value('name'),type:groupType,subject:value('subject'),grade:Number(value('grade')),exam:selectedType==='exam',defaultTeacher:value('defaultTeacher'),comment:value('groupComment')};
      if(existing) {
        if(existing.subject!==fields.subject && !window.confirm('Изменить основной предмет группы? Предметы существующих занятий и назначений сохранятся.')) return;
        Object.assign(existing,fields);
      } else {
        const newId='new-'+(classes.length+groups.length);
        (groupType==='class'?classes:groups).push({id:newId,...fields,archived:false});
        for(const p of people.filter(p=>values.getAll('members').includes(p.id))) {
          if(groupType==='class') p.classId=newId;
          else p.groupIds.push(newId);
        }
      }
    }
    if(type==='assignment'){
      const existing=t.assignments.find(a=>a.id===id);
      const fields={subject:value('subject'),target:value('audience')==='parallel'?value('grade'):value('target'),course:value('course')};
      if(existing) Object.assign(existing,fields);
      else t.assignments.push({id:'a-'+Date.now(),...fields,source:'Тестовое назначение'});
    }
    if(type==='unassign') t.assignments=t.assignments.filter(a=>a.id!==id);
    if(['membership','join','move','remove'].includes(type)){
      const p=people.find(p=>p.id===(type==='membership'?value('student'):type==='join'?id:pupilId));
      if(!p) return;
      if(type==='move'||type==='remove') p.groupIds=p.groupIds.filter(groupId=>groupId!==id);
      const group=type==='membership'?itemById(id):itemById(value('target'));
      if(group?.type==='class') p.classId=group.id;
      if(group?.type==='group'&&!p.groupIds.includes(group.id)) p.groupIds.push(group.id);
      report='Состав обновлён в прототипе.';
    }
    state.report=report;
    window.schoolGo(state.returnTo);
  });
  document.addEventListener('dragstart',event=>{
    const row=event.target.closest('.dr [data-dr-drag][draggable="true"]');
    if(!row) return;
    event.dataTransfer.setData('text/plain',row.dataset.drDrag+'|'+state.roster);
  });
  document.addEventListener('dragover',event=>{
    if(event.target.closest('.dr [data-dr-drop]:not([data-dr-drop=""])')) event.preventDefault();
  });
  document.addEventListener('drop',event=>{
    const target=event.target.closest('.dr [data-dr-drop]:not([data-dr-drop=""])');
    if(!target) return;
    event.preventDefault();
    const [pupil,source]=event.dataTransfer.getData('text/plain').split('|');
    const destination=target.dataset.drDrop;
    const a=itemById(source);
    const b=itemById(destination);
    if(!a||!b||!people.some(p=>p.id===pupil)||source===destination) return;
    if(a.subject!==b.subject){
      state.report='Перемещение возможно между группами одного предмета. Для другого предмета используйте «Добавить в группу».';
      redraw();
      return;
    }
    openForm('move:'+source+':'+pupil,'people');
    requestAnimationFrame(()=>{const select=document.querySelector('.dr-form [name="target"]');if(select) select.value=destination;});
  });

  window.schoolSections=Object.assign(window.schoolSections||{},{people:peopleScreen,teachers:teachersScreen,profile:profileScreen,forms:formsScreen});
})();
