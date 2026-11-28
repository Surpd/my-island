(() => {
  const subjects = ['Математика','Русский язык','Литература','Физика','Английский язык','История','География','Биология','Информатика','Обществознание','Изобразительное искусство','Музыка'];
  const works = ['Входная диагностика','Самостоятельная работа','Устный опрос','Проверочная работа','Домашняя работа','Работа над ошибками','Практикум','Контрольная работа','Индивидуальное задание','Групповой проект','Тематическая работа','Итоговая работа'];
  const surnames = ['Авдеев','Алексеева','Белов','Васильева','Громов','Давыдова','Егоров','Жукова','Зайцев','Ильина','Киселёв','Ковалёва','Лебедев','Миронова','Никитин','Орлова','Павлов','Романова','Савельев','Семёнова','Соколов','Тарасова','Федотов','Хохлова','Цветков','Чернова','Шестаков','Щербакова','Юдин','Яковлева'];
  const names = ['Михаил','Анна','Артём','Мария','Роман','Полина','Даниил','Софья','Матвей','Елена','Илья','Вера'];
  const pupils = surnames.map((name, i) => name + ' ' + names[i % names.length]);
  const terms = ['I','II','III'];
  const grades = [5,6,7,8,9,10,11];
  const states = { grade:7, book:0, subject:0, term:0, group:'А', selected:0, query:'', filter:'all', view:'work', summary:false, work:0, panel:'', message:'', memberScope:'class', preview:'', period:{}, current:{}, automation:{}, modes:{}, groupMap:{}, sync:{}, linked:{}, connected:{}, aliases:[], teams:{}, custom:[] };
  const key = () => states.grade + ':' + states.book;
  const sourceKey = (index = states.subject) => key() + ':' + index;
  const count = () => states.grade === 11 && !states.connected[11] ? 0 : states.grade < 7 ? 10 : 12;
  const id = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch]);
  const mode = (i) => states.modes[sourceKey(i)] || (i === 11 ? 'excluded' : i === 10 ? 'manual' : 'auto');
  const failed = (i = states.subject) => i === 3 && !states.sync[sourceKey(i)] && mode(i) === 'auto';
  const current = () => states.current[key()] ?? 0;
  const active = () => states.term === 0 || states.term === 1 && states.subject % 3 !== 2 || states.term === states.period[sourceKey()];
  const bookName = () => states.grade + (states.book ? 'Б' : 'А') + ' · Электронный журнал';
  const btn = (action, title, options = {}) => '<button type="button" data-jx="' + action + '" class="jx-btn ' + (options.style || '') + '"' + (options.label ? ' aria-label="' + id(options.label) + '"' : '') + (options.pressed == null ? '' : ' aria-pressed="' + options.pressed + '"') + (options.disabled ? ' disabled' : '') + '>' + title + '</button>';
  const select = (label, action, values, selected) => '<label>' + label + '<select data-jx-field="' + action + '">' + values.map(([value, name]) => '<option value="' + id(value) + '"' + (String(value) === String(selected) ? ' selected' : '') + '>' + id(name) + '</option>').join('') + '</select></label>';
  const input = (label, field, placeholder = '', type = 'text', required = false, value = '') => '<label>' + label + '<input data-jx-field="' + field + '" type="' + type + '" value="' + id(value) + '" placeholder="' + placeholder + '"' + (required ? ' required' : '') + '></label>';
  const option = (a) => a.map((x, i) => [i,x]);
  const year = '2026/27';
  const chips = () => grades.map(g => btn('grade:' + g, '<strong>' + g + '</strong><span> класс</span>', {pressed:states.grade === g,label:g + ' класс'})).join('');
  const scheduled = () => states.grade === 8 || states.grade === 9;
  const automatic = () => '<label class="jx-auto"><input data-jx-field="automation" type="checkbox"' + (scheduled() ? (states.automation[key()] ?? true) ? ' checked' : '' : ' disabled') + '> Автоматически каждый час' + (scheduled() ? '' : ' · Расписание не настроено') + '</label>';
  const conditions = () => '<details class="jx-meta"><summary>Сведения об источнике</summary><p>Учебный год ' + year + ' · Google Таблицы · чтение из источника</p>' + automatic() + btn('history','История синхронизаций') + '</details>';
  const subjectInfo = i => failed(i) ? '<span class="jx-warn">Ошибка синхронизации</span>' : mode(i) === 'manual' ? '<span class="jx-muted">Обновляется вручную</span>' : i === 9 ? '<span class="jx-muted">Без оценок</span>' : '<span class="jx-muted">24 ноя., 16:10</span>';
  const unmatched = () => [1, 8, 13].filter(n => states.linked[key() + ':' + n] == null);
  const toolbar = (v) => '<div class="jx-bar">' + input('Найти предмет','query','Название предмета','search',false,states.query) + select('Показать','filter',[['all','Все предметы'],['attention','Требуют внимания'],['manual','Только вручную']],states.filter) + (v === 'C' ? '<div class="jx-bar-end">' + btn('matching','Сопоставление' + (unmatched().length ? ' · ' + unmatched().length : '')) + btn('sources','Источники') + '</div>' : '') + '</div>';
  const subjectsList = v => {
    const rows = subjects.slice(0,count()).map((name,i) => ({name,i})).filter(({name,i}) => mode(i) !== 'excluded' && name.toLowerCase().includes(states.query.trim().toLowerCase()) && (states.filter === 'all' || states.filter === 'manual' && mode(i) === 'manual' || states.filter === 'attention' && failed(i)));
    if (!rows.length) return '<p class="jx-empty">По выбранным условиям предметы не найдены.' + btn('clear','Сбросить фильтры') + '</p>';
    if (v === 'D') return '<div class="jx-d-list">' + rows.map(({name,i}) => btn('pick:' + i,'<span><strong>' + name + '</strong><small>' + subjectInfo(i) + '</small></span><span aria-hidden="true">›</span>',{pressed:states.selected === i})).join('') + '</div>';
    return '<table class="jx-subject-table"><thead><tr><th scope="col">Предмет</th><th scope="col">Работы</th><th scope="col">Ученики</th><th scope="col">Источник</th></tr></thead><tbody>' + rows.map(({name,i}) => '<tr><th scope="row">' + btn('open:' + i,name + '<span aria-hidden="true"> ↗</span>',{style:'jx-text'}) + (v === 'S' ? '<small>I триместр · текущий открыт</small>' : '') + '</th><td>' + (i === 9 ? 0 : works.length) + '</td><td>' + (i === 9 ? '—' : pupils.length) + '</td><td>' + subjectInfo(i) + '</td></tr>').join('') + '</tbody></table>';
  };
  function catalog(v) {
    const i = states.selected;
    return '<div class="jx jx-' + v + '" data-jx-screen="catalog"><header class="jx-head"><div><span class="jx-kicker">Учебная работа</span><h1>Журналы и оценки</h1>' + (v === 'S' ? '<p>Просмотр оценок по классам, предметам и триместрам. Изменения в этой панели не попадают в оригинал.</p>' : '<p>Классы, предметы и успеваемость за триместр.</p>') + '</div><div class="jx-actions">' + (v === 'S' ? btn('matching','Journals → Matching') : '') + btn('connect',v === 'S' ? 'Подключить Google Таблицу' : 'Подключить таблицу',{style:'jx-primary'}) + '</div></header>' + '<nav class="jx-classes" aria-label="Классы">' + chips() + '</nav>' + (count() ? '<div class="jx-book"><div><strong>' + bookName() + '</strong><small>Учебный год ' + year + '</small></div><div class="jx-actions">' + btn('book:' + (states.book ? 0 : 1),states.book ? 'Журнал А' : 'Журнал Б') + (v === 'S' ? btn('original','Открыть оригинальную таблицу') : '') + btn('refresh','Обновить' + (v === 'S' ? ' сейчас' : '')) + '</div></div>' + (failed(3) ? '<div class="jx-alert"><span><strong>Физика · не удалось обновить вкладку</strong> Последние сохранённые оценки доступны.</span>' + btn('retry:3','Повторить') + '</div>' : '') + (v === 'S' ? automatic() : '') + '<section class="jx-catalog" aria-label="Предметы">' + (v === 'S' ? '<h2>Предметы</h2>' : toolbar(v)) + (v === 'D' ? '<div class="jx-d-spread">' + subjectsList(v) + '<aside class="jx-d-card"><span class="jx-kicker">' + states.grade + ' класс</span><h2>' + subjects[i] + '</h2><p>' + subjectInfo(i) + '</p><dl><div><dt>Текущий триместр</dt><dd>' + terms[current()] + '</dd></div><div><dt>Ученики</dt><dd>' + (i === 9 ? '—' : pupils.length) + '</dd></div><div><dt>Работы</dt><dd>' + (i === 9 ? 0 : works.length) + '</dd></div></dl>' + btn('open:' + i,'Открыть журнал',{style:'jx-primary',disabled:mode(i) === 'excluded'}) + '<div class="jx-d-secondary">' + btn('matching','Сопоставление · ' + unmatched().length) + btn('sources','Источники') + '</div></aside></div>' : subjectsList(v)) + '</section>' + (v === 'S' ? '<details class="jx-meta"><summary>Настройки вкладок · ' + states.grade + ' класс</summary>' + sourceSettings() + '</details>' : conditions()) : '<div class="jx-empty"><h2>Журнал ' + states.grade + ' класса ещё не подключён</h2>' + btn('known','Подключить журнал ' + states.grade + ' класса',{style:'jx-primary'}) + '</div>') + tail() + '</div>';
  }
  function result(row, work) {
    const n = (row * 7 + work * 3 + states.subject + states.term + states.grade) % 19;
    if (n === 0) return '0';
    if (n === 1) return 'н';
    if (n === 2) return '—';
    if (n === 3) return 'Выполнено';
    if (n === 4) return 'Не выполнено';
    return String(n + 1) + '<small> / 20</small>';
  }
  const courseRows = () => pupils.map((name,i) => ({name,i})).filter(({i}) => states.group === (i < 16 ? 'А' : 'Б'));
  const workHead = i => '<strong title="' + works[i] + '">' + works[i] + '</strong><small>' + (i * 2 + 2) + ' ноя. · вес ' + (i % 3 + 1) + '</small><small>макс. 20</small>';
  const mark = i => '<i class="jx-mark jx-mark-' + (i % 4 + 2) + '">' + (i % 4 + 2) + '</i>';
  function matrix(v, teams = false) {
    const rows = teams ? [{name:'Маяк',i:30},{name:'Горизонт',i:31}] : courseRows();
    const compact = v === 'D' && states.view === 'work' && !teams;
    const summaryOnly = !teams && v === 'C' && states.summary;
    const columns = summaryOnly ? [] : compact ? [states.work] : works.map((_,i) => i);
    return '<section class="jx-matrix"><div class="jx-matrix-meta"><strong>' + rows.length + (teams ? ' команды' : ' учеников') + ' · ' + works.length + ' работ</strong>' + (summaryOnly ? '' : '<span>0 — ноль · «н» — пропуск · — нет результата</span>') + (!teams && v === 'C' ? btn('summary','Только сводка',{pressed:summaryOnly}) : '') + '</div><div class="jx-scroll" role="region" tabindex="0" aria-label="' + (teams ? 'Командные результаты' : 'Таблица результатов учеников') + '"><table class="jx-marks"><caption>' + subjects[states.subject] + ' · ' + (teams ? 'Результаты команд' : 'Результаты учеников') + '</caption><thead><tr><th scope="col">' + (teams ? 'Команда' : 'Ученик') + '</th>' + columns.map(i => '<th scope="col">' + workHead(i) + '</th>').join('') + '<th scope="col">Средняя</th><th scope="col">Допуск</th><th scope="col">Итоговая</th></tr></thead><tbody>' + rows.map(({name,i}) => '<tr><th scope="row"><strong>' + name + '</strong><small>' + (teams ? id((states.teams[key() + ':' + i] || pupils.slice(i - 30, i - 27)).join(', ')) : 'Группа ' + states.group + (unmatched().includes(i) ? ' · не сопоставлен' : '')) + '</small>' + (i % 5 === 0 || i === 30 ? btn('technical:' + i,'Технические показатели',{style:'jx-text'}) : '') + (!teams && unmatched().includes(i) ? btn('matching:' + i,'Сопоставить ученика',{style:'jx-text'}) : '') + '</th>' + columns.map(work => '<td>' + result(i,work) + '</td>').join('') + '<td>' + (i % 3 + 3) + ',25</td><td>' + (i % 8 ? 'Допущен' : 'Не допущен') + '</td><td>' + mark(i) + '</td></tr>').join('') + '</tbody></table></div></section>';
  }
  const selectors = () => select('Класс','detail-grade',grades.map(g => [g,g + ' класс']),states.grade) + select('Предмет','subject',option(subjects.slice(0,count())).filter(([i]) => mode(i) !== 'excluded'),states.subject);
  const groups = () => ['А','Б'].map(g => btn('group:' + g,'Группа ' + g,{pressed:states.group === g})).join('');
  const termTabs = () => terms.map((t,i) => btn('term:' + i,t + ' триместр' + (current() === i ? ' · текущий' : ''),{pressed:states.term === i})).join('');
  function teamResults(v) {
    if (v === 'S') return '<h3>Командные результаты · оценки относятся к команде</h3>' + matrix(v,true);
    return '<details class="jx-teams"><summary><strong>Командные результаты</strong><span>2 команды · 12 работ</span></summary><p>Оценки относятся к команде.</p>' + matrix(v,true) + '</details>';
  }
  function journal(v) {
    const body = active() ? (states.subject === 9 ? '<p class="jx-empty">В этой группе пока нет распознанных учеников или команд.</p>' : matrix(v) + (states.subject % 3 === 0 && states.group === 'А' ? teamResults(v) : '')) : '<p class="jx-empty">Журнал за выбранный триместр ещё не создан.</p>';
    return '<div class="jx jx-' + v + '" data-jx-screen="journal">' + btn('catalog','← Все журналы',{style:'jx-text'}) + (v === 'S' ? '<nav class="jx-bar" aria-label="Навигация по журналам">' + selectors() + '</nav>' : '') + '<header class="jx-head"><div><span class="jx-kicker">' + states.grade + ' класс · журнал предмета</span><h1>' + subjects[states.subject] + '</h1><p>' + (v === 'S' ? bookName() + ' · ' + year + ' · обновлено 24 ноя., 16:10' : states.grade + (states.book ? 'Б' : 'А') + ' · ' + pupils.length + ' учеников') + '</p></div><div class="jx-actions">' + (v === 'S' ? btn('original','Оригинальная таблица') + btn('refresh','Обновить сейчас') : btn('manage','Журнал и источник')) + '</div></header>' + (v !== 'S' ? '<nav class="jx-bar" aria-label="Навигация по журналам">' + selectors() + '<div class="jx-segment" role="group" aria-label="Учебная группа">' + groups() + '</div></nav>' : '') + '<div class="jx-segment jx-terms" role="group" aria-label="Триместр">' + termTabs() + '</div>' + (states.subject % 2 === 0 && states.period[sourceKey()] == null ? '<div class="jx-alert"><span>Одной вкладке ещё нужно назначить триместр.</span>' + (v === 'S' ? terms.map((t,i) => btn('bind:' + i,'Привязать к ' + t)).join('') : btn('period','Назначить триместр')) + '</div>' : '') + (failed() ? '<div class="jx-alert"><span>Обновление не удалось. Последние сохранённые оценки доступны.</span>' + btn('retry:' + states.subject,'Повторить') + '</div>' : '') + (v === 'S' ? (states.term !== current() ? btn('current','Сделать выбранный триместр текущим') : '') + '<details class="jx-meta"><summary>История синхронизаций</summary>' + history() + '</details><div class="jx-segment" role="group" aria-label="Учебная группа">' + groups() + '</div><div class="jx-inline">' + groupForm() + '</div>' : '<div class="jx-subbar"><span>Группа ' + states.group + ' · ' + (states.group === 'А' && !states.groupMap[sourceKey() + states.group] ? 'Связь с учебной группой требует подтверждения' : 'Связь с учебной группой') + '</span>' + btn('group-map','Настроить',{style:'jx-text'}) + '</div>') + (v === 'D' && active() && states.subject !== 9 ? '<div class="jx-workbench"><aside class="jx-work-index"><div class="jx-segment" role="group" aria-label="Представление">' + btn('view:work','По работам',{pressed:states.view === 'work'}) + btn('view:table','Все оценки',{pressed:states.view === 'table'}) + '</div>' + (states.view === 'work' ? select('Выбранная работа','work',option(works),states.work) + '<div class="jx-work-links">' + works.map((w,i) => btn('work:' + i,'<small>' + (i * 2 + 2) + ' ноя.</small>' + w,{pressed:i === states.work})).join('') + '</div>' : '') + '</aside><div class="jx-work-results">' + body + '</div></div>' : body) + tail() + '</div>';
  }
  const titles = {connect:'Подключить Google Таблицу', manage:'Журнал и источник', sources:'Источники журнала', period:'Назначить триместр', current:'Назначить текущий триместр', history:'История синхронизаций', matching:'Сопоставление учеников и команд', 'group-map':'Связь учебной группы', technical:'Технические показатели', original:'Оригинальная таблица'};
  let returnAction = '';
  const history = () => '<ul class="jx-history">' + (states.sync[key()] || states.sync[sourceKey()] ? '<li><strong>Успешно</strong> · вручную · только что</li>' : '') + '<li><strong>Частично</strong> · по расписанию · 25 ноя., 09:00<br><small>Физика: сохранены данные предыдущего обновления.</small></li><li><strong>Успешно</strong> · вручную · 24 ноя., 16:10</li></ul>';
  const form = (action, content, title, disabled = false) => '<form data-jx-form="' + action + '">' + content + '<button class="jx-btn jx-primary" type="submit"' + (disabled ? ' disabled' : '') + '>' + title + '</button></form>';
  const groupForm = () => form('save-group',select('Каноническая группа','canonical',[['','Выберите существующую группу'],['1',states.grade + 'А · ' + subjects[states.subject]],['2',states.grade + 'Б · ' + subjects[states.subject]]],states.groupMap[sourceKey() + states.group] || ''),states.groupMap[sourceKey() + states.group] ? 'Сохранить связь' : 'Подтвердить группу');
  const sourceSettings = () => '<div class="jx-source-list">'+ subjects.slice(0,count()).map((name,i) => '<div><div><strong>' + name + '</strong><small>' + (mode(i) === 'excluded' ? 'Исключена из журналов и оценок' : subjectInfo(i)) + '</small></div>' + select('Режим синхронизации','mode:' + i,[['auto','Автоматически'],['manual','Только вручную'],['excluded','Исключить']],mode(i)) + btn('original','Открыть вкладку') + btn('retry:' + i,'Обновить вкладку',{disabled:mode(i) === 'excluded'}) + '</div>').join('') + '</div>';
  const people = () => [...pupils,...states.custom];
  const searchPeople = (field, value = '') => input('Найти ученика',field,'Фамилия или имя','search',false,value);
  function matching() {
    const pending = unmatched();
    const person = states.candidate == null ? '' : states.candidate;
    const candidates = people().map((n,i) => [i,n + ' · ' + (states.memberScope === 'school' && i % 4 === 0 ? states.grade + 1 : states.grade) + ' класс']);
    const cross = states.memberScope === 'school' && person !== '' && person % 4 === 0;
    const listed = (state, list) => [['','Выберите ученика'],...list.filter(([,n]) => n.toLowerCase().includes((state || '').toLowerCase()))];
    const checkAliases = pending.map(i => '<label class="jx-check"><input type="checkbox" value="' + i + '" data-jx-field="alias"' + (states.aliases.includes(i) ? ' checked' : '') + '><span>' + pupils[i] + '<small>2 предмета · 6 записей</small></span></label>').join('');
    const basicPreview = states.preview === 'match' ? '<div class="jx-preview"><strong>Предпросмотр сопоставления</strong><p>' + states.aliases.length + ' имён → ' + id(people()[person]) + ' · ' + states.aliases.length * 6 + ' записей.</p>' + (cross ? '<p>Ученик в другом классе. Связь касается только записей журнала.</p>' : '') + btn('confirm-match','Подтвердить сопоставление',{style:'jx-primary'}) + '</div>' : '';
    const createPreview = states.preview === 'create' ? '<div class="jx-preview"><strong>Предпросмотр нового ученика</strong><p>' + id(states.newName) + ' · ' + states.grade + ' класс · ' + states.aliases.length + ' вариантов имени</p>' + (states.duplicate ? '<p role="alert">Похожая запись найдена. Выберите ученика из справочника.</p>' : btn('confirm-create','Подтвердить создание',{style:'jx-primary'})) + '</div>' : '';
    return select('Таблица','match-book',grades.filter(g => g !== 11 || states.connected[11]).flatMap(g => [0,1].map(b => [g + ':' + b,g + (b ? 'Б' : 'А') + ' · Электронный журнал'])),key()) + '<h3>Индивидуальные записи</h3>' + (pending.length ? '<div class="jx-match-grid"><fieldset><legend>Имена из таблицы</legend>' + checkAliases + '</fieldset><div>' + select('Область поиска','scope',[['class','Этот класс'],['school','Вся школа']],states.memberScope) + searchPeople('person-query',states.personQuery) + select('Ученик из справочника','candidate',listed(states.personQuery,candidates),person) + (cross ? '<label class="jx-check"><input type="checkbox" data-jx-field="cross"' + (states.cross ? ' checked' : '') + '> Разрешить сопоставление с другим классом</label>' : '') + btn('preview-match','Проверить сопоставление',{disabled:!states.aliases.length || person === '' || cross && !states.cross}) + basicPreview + '</div></div><details' + (states.preview === 'create' ? ' open' : '') + '><summary>Создать ученика, которого нет в справочнике</summary>' + form('create-student',input('Фамилия','surname','','text',true,states.surname || '') + input('Имя','given','','text',true,states.given || '') + input('Комментарий','comment','','text',false,states.comment || ''),'Предпросмотр создания',!states.aliases.length) + createPreview + '</details>' : '<p>Неразобранных индивидуальных записей нет.</p>') + '<details' + (states.preview === 'replace' ? ' open' : '') + '><summary>Подтверждённые связи · исправить связь</summary>' + form('replace',select('Имя из таблицы','replace-alias',option(pupils),states.replaceAlias || 0) + searchPeople('replacement-query',states.replacementQuery) + select('Новый ученик','replacement',listed(states.replacementQuery,candidates),states.replacement),'Предпросмотр замены') + (states.preview === 'replace' ? '<div class="jx-preview"><strong>Проверьте замену связи</strong><p>' + pupils[states.replaceAlias || 0] + ' → ' + id(people()[states.replacement]) + ' · 6 записей.</p>' + btn('confirm-replace','Подтвердить замену связи',{style:'jx-primary'}) + '</div>' : '') + '</details><h3>Команды</h3>' + form('team',select('Команда','team-id',[[30,'Маяк'],[31,'Горизонт']],states.teamId || 30) + searchPeople('team-query',states.teamQuery) + '<label>Участники команды<select data-jx-field="team-members" multiple size="5">' + people().map((name,i) => [name,i]).filter(([name]) => (states.teams[key() + ':' + (states.teamId || 30)] || pupils.slice((states.teamId || 30) - 30,(states.teamId || 30) - 27)).includes(name) || name.toLowerCase().includes((states.teamQuery || '').toLowerCase())).map(([name,i]) => '<option value="' + i + '"' + ((states.teams[key() + ':' + (states.teamId || 30)] || pupils.slice((states.teamId || 30) - 30,(states.teamId || 30) - 27)).includes(name) ? ' selected' : '') + '>' + id(name) + '</option>').join('') + '</select></label>','Сохранить состав');
  }
  function panelBody() {
    switch(states.panel) {
      case 'connect': return '<p>Таблица импортируется по классам и предметным вкладкам.</p>' + form('connect',select('Класс','connect-grade',grades.map(g => [g,g + ' класс']),states.grade) + input('Ссылка на Google Таблицу','url','https://docs.google.com/spreadsheets/d/…','url',true),'Подключить и импортировать');
      case 'manage': return '<p>' + bookName() + ' · ' + year + '</p><div class="jx-actions">' + btn('original','Оригинальная таблица') + btn('refresh','Обновить сейчас') + btn('history','История синхронизаций') + btn('sources','Настройки вкладок') + (states.term !== current() ? btn('current','Сделать выбранный триместр текущим') : '') + '</div>' + conditions();
      case 'sources': return '<p>' + bookName() + ' · ' + year + '</p><p>Исключённые вкладки скрыты из списка предметов.</p>' + automatic() + sourceSettings();
      case 'history': return history();
      case 'period': return '<p>Вкладка без триместра · ' + subjects[states.subject] + '</p><div class="jx-actions">' + terms.map((t,i) => btn('bind:' + i,'Привязать к ' + t)).join('') + '</div>';
      case 'current': return '<p>' + terms[states.term] + ' триместр станет текущим для таблицы ' + bookName() + '.</p>' + btn('confirm-current','Назначить текущим',{style:'jx-primary'});
      case 'group-map': return '<p>' + subjects[states.subject] + ' · группа ' + states.group + '</p>' + groupForm();
      case 'matching': return matching();
      case 'technical': return '<p>' + (pupils[states.technical] || 'Маяк') + '</p><dl class="jx-tech"><div><dt>Баллы проекта</dt><dd>123</dd></div><div><dt>Статус импорта</dt><dd>Источник прочитан</dd></div></dl>';
      case 'original': return '<p>' + bookName() + ' · ' + subjects[states.subject] + '</p><p>Для демонстрации используется синтетическая таблица. Переход к реальному документу доступен в рабочей системе.</p>';
      default: return '';
    }
  }
  const dialogBody = () => '<header><h2 id="jx-dialog-heading">' + titles[states.panel] + '</h2>' + btn('close','Закрыть',{style:'jx-text'}) + '</header><div role="status">' + id(states.panelMessage || '') + '</div>' + panelBody();
  function tail() {
    return '<div class="jx-feedback" role="status">' + id(states.message) + (states.message ? btn('dismiss','Скрыть сообщение',{style:'jx-text'}) : '') + '</div>' + (states.panel ? '<dialog class="jx-dialog" aria-labelledby="jx-dialog-heading">' + dialogBody() + '</dialog>' : '');
  }
  const root = () => document.querySelector('.jx[data-jx-screen]');
  const activateDialog = () => {
    const dialog = root()?.querySelector('dialog.jx-dialog');
    if (dialog && !dialog.open) {
      dialog.showModal();
      dialog.addEventListener('cancel', e => { e.preventDefault(); closePanel(); });
    }
  };
  function rerender(selector) {
    const old = root();
    if (!old) return;
    const v = old.classList.contains('jx-S') ? 'S' : old.classList.contains('jx-D') ? 'D' : 'C';
    const scroll = [...old.querySelectorAll('.jx-scroll')].map(x => [x.scrollLeft,x.scrollTop]);
    const dialogScroll = old.querySelector('.jx-dialog')?.scrollTop || 0;
    const openDetails = [...old.querySelectorAll('details[open] > summary')].map(s => s.textContent);
    old.outerHTML = (old.dataset.jxScreen === 'catalog' ? catalog : journal)(v);
    activateDialog();
    const dialog = root().querySelector('.jx-dialog');
    if (dialog) dialog.scrollTop = dialogScroll;
    root().querySelectorAll('details > summary').forEach(s => { if (openDetails.includes(s.textContent)) s.parentElement.open = true; });
    [...root().querySelectorAll('.jx-scroll')].forEach((box,i) => { if (scroll[i]) [box.scrollLeft,box.scrollTop] = scroll[i]; });
    if (selector) root().querySelector(selector)?.focus({preventScroll:true});
  }
  function setPanel(panel, action) {
    if (!states.panel) returnAction = action;
    states.panel = panel;
    states.panelMessage = '';
    rerender();
  }
  function closePanel(message = '') {
    states.panel = '';
    states.preview = '';
    states.message = message;
    rerender('[data-jx="' + CSS.escape(returnAction) + '"]');
  }
  function resetClass(g) {
    states.grade = g;
    states.book = 0;
    states.selected = 0;
    states.aliases = [];
    states.preview = '';
    if (states.subject >= count()) states.subject = 0;
    if (mode(states.subject) === 'excluded') states.subject = subjects.findIndex((_,i) => i < count() && mode(i) !== 'excluded');
    states.term = current();
  }
  function openJournal(i) {
    states.subject = i;
    states.term = current();
    states.group = 'А';
    states.work = 0;
    states.panel = '';
    states.message = '';
    window.schoolGo('journal');
  }
  document.addEventListener('click', (e) => {
    const button = e.target.closest('.jx [data-jx]');
    if (!button) return;
    const action = button.dataset.jx;
    const [name, arg] = action.split(':');
    const n = Number(arg);
    const focus = '[data-jx="' + CSS.escape(action) + '"]';
    if (name === 'close') return closePanel();
    if (name === 'catalog') { states.panel = ''; states.message = ''; return window.schoolGo('catalog'); }
    if (name === 'open') return openJournal(n);
    if (name === 'matching') {
      states.preview = '';
      states.aliases = arg ? [n] : [];
      return setPanel(name,action);
    }
    if (name === 'technical') { states.technical = n; return setPanel(name,action); }
    if (name === 'current' && root().classList.contains('jx-S')) {
      states.current[key()] = states.term;
      states.message = 'Текущий триместр назначен в демонстрации.';
      return rerender();
    }
    if (titles[name]) return setPanel(name,action);
    if (name === 'known') { states.connected[states.grade] = true; states.message = 'Журнал подключён в демонстрации.'; }
    if (name === 'grade') resetClass(n);
    if (name === 'book') { states.book = n; states.selected = 0; }
    if (name === 'pick') states.selected = n;
    if (name === 'clear') { states.query = ''; states.filter = 'all'; }
    if (name === 'term') states.term = n;
    if (name === 'group') states.group = arg;
    if (name === 'view') states.view = arg;
    if (name === 'summary') states.summary = !states.summary;
    if (name === 'work') states.work = n;
    if (name === 'dismiss') states.message = '';
    if (name === 'refresh' || name === 'retry') {
      if (name === 'refresh') {
        states.sync[key()] = true;
        subjects.forEach((_,i) => { if (mode(i) === 'auto') states.sync[sourceKey(i)] = true; });
      } else states.sync[sourceKey(n)] = true;
      const message = 'Обновление завершено в демонстрации.';
      if (states.panel) states.panelMessage = message;
      else states.message = message;
    }
    if (name === 'bind') {
      states.period[sourceKey()] = n;
      states.term = n;
      if (states.panel) return closePanel('Триместр вкладки назначен в демонстрации.');
      states.message = 'Триместр вкладки назначен в демонстрации.';
    }
    if (name === 'confirm-current') { states.current[key()] = states.term; return closePanel('Текущий триместр назначен в демонстрации.'); }
    if (name === 'preview-match') states.preview = 'match';
    if (name === 'confirm-match' || name === 'confirm-create') {
      let selected = states.candidate;
      if (name === 'confirm-create') { states.custom.push(states.newName); selected = people().length - 1; }
      states.aliases.forEach(i => { states.linked[key() + ':' + i] = selected; });
      states.aliases = [];
      states.preview = '';
      states.panelMessage = name === 'confirm-create' ? 'Ученик создан, записи сопоставлены в демонстрации.' : 'Связи сохранены в демонстрации.';
    }
    if (name === 'confirm-replace') {
      states.linked[key() + ':' + (states.replaceAlias || 0)] = states.replacement;
      states.preview = '';
      states.panelMessage = 'Связь заменена в демонстрации.';
    }
    rerender(focus);
  });
  document.addEventListener('input', e => {
    const field = e.target;
    const data = field.closest('.jx') && field.dataset.jxField;
    if (!['query','person-query','replacement-query','team-query'].includes(data)) return;
    const names = { query:'query','person-query':'personQuery','replacement-query':'replacementQuery','team-query':'teamQuery' };
    states[names[data]] = field.value;
    states.preview = '';
    if (data === 'person-query') states.candidate = '';
    if (data === 'replacement-query') states.replacement = '';
    const pos = field.selectionStart;
    rerender('[data-jx-field="' + data + '"]');
    if (pos != null) root().querySelector('[data-jx-field="' + data + '"]')?.setSelectionRange(pos,pos);
  });
  document.addEventListener('change', e => {
    const field = e.target;
    const action = field.closest('.jx') && field.dataset.jxField;
    const value = field.value;
    if (!action) return;
    const focus = '[data-jx-field="' + CSS.escape(action) + '"]' + (action === 'alias' ? '[value="' + CSS.escape(value) + '"]' : '');
    if (action === 'detail-grade') {
      resetClass(Number(value));
      if (!count() || states.subject < 0) return window.schoolGo('catalog');
    } else if (action === 'filter') states.filter = value;
    else if (action === 'subject') { states.subject = Number(value); states.term = current(); states.group = 'А'; states.work = 0; }
    else if (action === 'work') states.work = Number(value);
    else if (action === 'match-book') { const [g,b] = value.split(':').map(Number); resetClass(g); states.book = b; }
    else if (action === 'scope') { states.memberScope = value; states.candidate = ''; states.cross = false; }
    else if (action === 'candidate') { states.candidate = value; states.cross = false; }
    else if (action === 'cross') states.cross = field.checked;
    else if (action === 'alias') states.aliases = field.checked ? [...states.aliases,Number(value)] : states.aliases.filter(i => i !== Number(value));
    else if (action === 'replace-alias') states.replaceAlias = Number(value);
    else if (action === 'replacement') states.replacement = value;
    else if (action === 'team-id') states.teamId = Number(value);
    else if (['surname','given','comment'].includes(action)) { states[action] = value; return; }
    else if (action === 'automation') { states.automation[key()] = field.checked; if (states.panel) states.panelMessage = 'Расписание книги сохранено в демонстрации.'; else states.message = 'Расписание книги сохранено в демонстрации.'; }
    else if (action.startsWith('mode:')) {
      states.modes[sourceKey(Number(action.split(':')[1]))] = value;
      states.panelMessage = 'Режим синхронизации сохранён в демонстрации.';
    } else return;
    states.preview = '';
    rerender(focus);
  });
  document.addEventListener('submit', e => {
    const target = e.target;
    if (!target.matches('.jx form[data-jx-form]')) return;
    e.preventDefault();
    const value = key => target.querySelector('[data-jx-field="' + key + '"]').value;
    const type = target.dataset.jxForm;
    if (type === 'connect') {
      const url = URL.canParse(value('url')) ? new URL(value('url')) : null;
      if (url?.hostname !== 'docs.google.com' || !/^\/spreadsheets\/d\/[^/]+/.test(url.pathname)) {
        states.panelMessage = 'Проверьте ссылку на документ Google Таблиц.';
      } else {
        states.connected[Number(value('connect-grade'))] = true;
        resetClass(Number(value('connect-grade')));
        return closePanel('Таблица импортирована в демонстрации.');
      }
    }
    if (type === 'save-group') {
      if (!value('canonical')) { if (states.panel) states.panelMessage = 'Выберите учебную группу.'; else states.message = 'Выберите учебную группу.'; }
      else {
        states.groupMap[sourceKey() + states.group] = value('canonical');
        if (states.panel) return closePanel('Связь группы сохранена в демонстрации.');
        states.message = 'Связь группы сохранена в демонстрации.';
      }
    }
    if (type === 'create-student') {
      states.surname = value('surname');
      states.given = value('given');
      states.comment = value('comment');
      states.newName = value('surname').trim() + ' ' + value('given').trim();
      states.duplicate = people().some(n => n.toLowerCase() === states.newName.toLowerCase());
      if (!value('surname').trim() || !value('given').trim()) states.panelMessage = 'Введите имя и фамилию.';
      else states.preview = 'create';
    }
    if (type === 'replace') {
      if (states.replacement == null || states.replacement === '' || String(states.replacement) === String(states.linked[key() + ':' + states.replaceAlias])) states.panelMessage = 'Выберите другого ученика.';
      else states.preview = 'replace';
    }
    if (type === 'team') {
      states.teams[key() + ':' + (states.teamId || 30)] = [...target.querySelector('[data-jx-field="team-members"]').selectedOptions].map(o => people()[Number(o.value)]);
      states.panelMessage = 'Состав команды сохранён в демонстрации.';
    }
    rerender();
  });
  window.schoolSections = window.schoolSections || {};
  for (const [screen, render] of Object.entries({catalog,journal})) {
    window.schoolSections[screen] = variant => {
      if (states.panel) queueMicrotask(activateDialog);
      return render(variant);
    };
  }
})();
