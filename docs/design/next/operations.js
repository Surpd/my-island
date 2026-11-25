(() => {
  const screens = window.schoolSections ||= {};
  const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const names = ['Марина Орлова', 'Андрей Смирнов', 'Павел Соколов', 'Анна Ким', 'Елена Волкова', 'София Белова', 'Денис Лебедев', 'Алёна Власова', 'Егор Козлов'];
  const students = ['Алина Морозова', 'Вера Попова', 'Дмитрий Кузнецов', 'Ирина Лебедева', 'Кирилл Егоров', 'Мария Волкова', 'Михаил Иванов', 'София Никитина', 'Тимофей Орлов', 'Юлия Белова'];
  const groups = ['7А', '7Б', '8А', '8Б', '9А', '9Б', '10А', '11А'];
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
  const button = (label, action, style = '', attributes = '') => '<button type="button" class="op-btn '+ style +'" data-op="'+ action +'" '+ attributes +'>'+ label +'</button>';
  const status = (label, tone = 'plain') => '<span class="op-tone op-'+tone+'">'+label+'</span>';
  const tabs = (items, value, action, label) => '<div class="op-tabs" aria-label="'+label+'">'+items.map(([key, title]) => button(title, action+':'+key, value===key?'is-active':'', 'aria-pressed="'+(value===key)+'"')).join('')+'</div>';
  const select = (label, key, list, value) => '<label class="op-field">'+ label+'<select aria-label="'+escape(label)+'" data-op-input="'+key+'">'+list.map(o => { const [v,t] = Array.isArray(o)?o:[o,o]; return '<option value="'+escape(v)+'" '+(v===value?'selected':'')+'>'+escape(t)+'</option>'; }).join('')+'</select></label>';
  const input = (label, key, value, type = 'text', attributes = '') => '<label class="op-field">'+ label+'<input type="'+type+'" data-op-input="'+key+'" value="'+escape(value)+'" '+attributes+'></label>';
  const title = (label, sub, actions = '') => '<header class="op-heading"><div><h1>'+label+'</h1>'+(sub?'<p>'+sub+'</p>':'')+'</div>'+actions+'</header>';
  const panel = (label, html, attr = '') => '<section class="op-panel" '+attr+'><h2>'+label+'</h2>'+html+'</section>';
  const minutes = n => n >= 60 ? Math.floor(n/60)+' ч'+(n%60?' '+n%60+' мин':'') : n+' мин';
  const table = (label, headers, rows) => '<div class="op-table-wrap" tabindex="0" role="region" aria-label="'+label+'"><table><thead><tr>'+headers.map(h=>'<th scope="col">'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map((c,i)=>i===0?'<th scope="row">'+c+'</th>':'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
  const empty = (label, text, action = '') => '<div class="op-empty"><strong>'+label+'</strong><p>'+text+'</p>'+action+'</div>';
  const initialWidgets = () => [{ id: 1, type: 'day', size: 'wide', date: '' }, { id: 2, type: 'load', size: 'compact', teacher: '', activity: '', start: '', end: '' }, { id: 3, type: 'load', size: 'compact', teacher: names[1], activity: '', start: '2026-11-23', end: '2026-11-27' }];
  const getWidgets = () => { try { const data = JSON.parse(localStorage.getItem('my-island-next-widgets')); if (Array.isArray(data)) return data; } catch {} return initialWidgets(); };
  const state = {
    day: 2, issue: '', more: false, teacher: '', className: '8Б', trajectory: '', journal: 'Алгебра · 8Б', subgroup: '', journalDate: 'period', journalDetail: '',
    start: '2026-11-23', end: '2026-11-27', teacherQuery: '', teacherActivity: '', teacherFormat: '', teacherGroup: '', classType: '',
    widgets: getWidgets(), widgetEdit: false, widgetAdd: '', nextWidget: Date.now(), settings: 'connection', google: 'grant', settingsNotice: '', announcements: [
      ['Расписание консультаций', 'В пятницу консультации по математике начнутся в 14:00.', 'Ученики', true],
      ['Подготовка к проектной неделе', 'Предложения по темам принимаются до 27 ноября.', 'Все', false]
    ],
    role: 'student', person: students[0], query: '', previewClass: '', previewGroup: '', previewDate: '2026-11-25', previewWidth: '390', previewHeight: '760',
    previewOpen: true, previewMessage: '', previewTeacher: ''
  };
  const segments = [
    ['09:00','09:45','Алгебра','7А',0,'208',''],
    ['09:00','09:45','Биология','8Б',4,'214',''],
    ['09:55','10:40','Физика · лаборатория','8Б',1,'305','room'],
    ['09:55','10:40','Робототехника','9А',6,'305','room'],
    ['09:55','10:40','История','7А',2,'201',''],
    ['10:55','11:40','Английский язык','7А',3,'Онлайн','replacement'],
    ['10:55','11:40','Геометрия','9А',0,'208',''],
    ['11:50','12:35','Литература','8Б',5,'202','cancelled'],
    ['11:50','12:35','География','7Б',8,'206',''],
    ['13:05','13:50','Биология · практикум','9Б',4,'214',''],
    ['13:05','13:50','Самостоятельная работа','8Б',null,'',''],
    ['14:00','14:30','Консультация','9А',0,'208','added']
  ];
  const changeName = { room:'Пересечение', replacement:'Замена', cancelled:'Отменено', added:'Добавлено' };
  const dayInfo = day => day===2 ? segments : day===4 ? null : day>4 ? [] : segments.filter((_,i)=>i%3!==day).map(r=>[...r.slice(0,6),'']);
  const dayLabel = i => ['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'][i];
  const issues = [
    ['room','09:55–10:40','Два занятия в кабинете 305','Физика · 8Б и робототехника · 9А',2],
    ['deferred','12:40–13:00','Занятие отложено при публикации','Кабинет не указан в источнике',null]
  ];
  const fixtureNote = title => '<span class="op-sr">'+title+' · синтетические данные</span>';
  function timeline(day, compact = false) {
    const rows = dayInfo(day);
    if (!rows) return empty('Неделя недоступна', 'Для этой даты источник публикации недоступен. Проверьте выбранную неделю.', button('Открыть расписание →','go:schedule'));
    if (!rows.length) return empty('Занятий в опубликованном дне нет', 'На '+(23+day)+' ноября активных интервалов нет.');
    const hours = [...new Set(rows.map(r=>r[0]))];
    return '<div class="op-timeline">'+hours.slice(0,compact?3:hours.length).map(t=>'<div class="op-slot"><time>'+t+'</time><div>'+rows.filter(r=>r[0]===t).map(r=>'<button type="button" class="op-lesson '+(r[6]?'op-lesson-'+r[6]:'')+'" data-op="issue:item'+segments.findIndex(s=>s[0]===r[0]&&s[2]===r[2])+'"><span><strong>'+r[2]+'</strong><small>До '+r[1]+' · '+r[3]+(r[4]!==null?' · '+names[r[4]]:'')+'</small></span><span>'+ (r[6]?status(changeName[r[6]],r[6]==='added'?'good':'warn'):'') +'<small>'+(r[5]?' '+r[5]:'')+'</small></span></button>').join('')+'</div></div>').join('')+'</div>';
  }
  function exceptions() {
    const active = state.issue;
    const assignment = active.startsWith('item') ? segments[Number(active.slice(4))] : null;
    const issue = issues.find(i=>i[0]===active);
    const card = (key,t,heading,desc) => '<button class="op-exception" type="button" data-op="issue:'+key+'" aria-expanded="'+(active===key)+'"><time>'+t+'</time><strong>'+heading+'</strong><small>'+desc+' →</small></button>';
    const showDetail = issue || assignment;
    return '<aside class="op-day-priority" aria-label="Исключения дня">'+panel('Требуют внимания', state.day===2?issues.map(i=>card(...i)).join(''):'<p>Для выбранного дня активных исключений нет.</p>')+
      (showDetail?panel(issue?issue[2]:assignment[2],'<p>'+(issue?issue[1]+' · '+issue[3]:assignment[0]+'–'+assignment[1]+' · '+assignment[3]+(assignment[4]!==null?' · '+names[assignment[4]]:''))+'</p>'+ (active==='room'?'<ul><li>09:55–10:40 · Физика · 8Б</li><li>09:55–10:40 · Робототехника · 9А</li></ul>':'') + button('Открыть в расписании →','go:schedule','primary')+button('Закрыть подробности','issue:') ,'id="op-detail" aria-label="Подробности события"'):'')+
      '</aside>';
  }
  function secondaryDay() {
    return '<div class="op-day-secondary"><details class="op-panel op-context"><summary>Следующая неделя <small>Черновик · проверить адресатов</small></summary><p>30 ноября — 6 декабря</p><p>Публикация приостановлена. У одного занятия нужно проверить адресатов.</p>'+button('Открыть черновик →','go:schedule')+'</details>'+
      '<details class="op-panel op-context"><summary>Заявки, источники и история</summary><p>1 заявка на роль · 2 аккаунта без привязки</p>'+button('Каталог людей →','go:people')+button('Источники и диагностика →','go:settings')+'</details>'+
      '<details class="op-panel op-context"><summary>Отчёты и моё пространство</summary>'+button('Нагрузка преподавателей','go:load')+button('Нагрузка классов','go:classes')+button('Аналитика журналов','go:stats')+button('Моё пространство','go:widgets')+'</details></div>';
  }
  screens.today = () => {
    const rows = dayInfo(state.day), published = !!rows;
    return '<div class="op-page">'+title('Сегодня','Школьное расписание · московское время',button('К сегодняшнему дню','day:2'))+fixtureNote('Оперативный день')+
      '<div class="op-datebar"><p>23–29 ноября 2026</p>'+tabs(weekdays.map((d,i)=>[i,d+' <strong>'+(23+i)+'</strong>']),state.day,'day','Дни недели')+'</div>'+
      '<div class="op-day-grid"><div class="op-day-heading"><header><div><h2>'+dayLabel(state.day)+', '+(23+state.day)+' ноября</h2><p>'+(published?rows.filter(r=>r[6]!=='cancelled').length+' занятий · '+(state.day===2?'3 изменения · ':'')+'из действующей публикации':'Нет действующей публикации для выбранного дня')+'</p></div>'+status(published?(state.day===2?'Частичная публикация':'Опубликовано'):'Источник недоступен',state.day===2||!published?'warn':'good')+'</header>'+
      (state.day===2?'<div class="op-publication">Часть занятий отложена. Список включает только видимые в выпуске интервалы и отмены.</div>':'')+'</div>'+exceptions()+
      '<section class="op-day-agenda" aria-label="Занятия дня">'+timeline(state.day)+'</section>'+
      '<details class="op-panel op-coverage"><summary>Покрытие источника <small>'+ (published?'Часть кабинетов известна':'Данные недоступны')+'</small></summary><p>'+ (published?'Известны кабинеты из опубликованных интервалов. Полный перечень кабинетов не проверен.':'По этому дню невозможно определить расписание и занятость кабинетов.')+'</p></details>'+secondaryDay()+'</div></div>';
  };
  const staffPeriods = [[24,16,24,16,22],[20,16,20,14,20],[18,16,16,16,16],[18,16,16,14,14],[16,16,14,14,14],[16,14,14,14,12],[14,14,14,12,12],[14,12,12,12,10],[12,10,10,10,10]];
  const subjects = ['Алгебра','Физика','История','Английский','Биология','Литература','Робототехника','Геометрия','География'];
  const loadFixtures = names.flatMap((name, ti) => staffPeriods[ti].flatMap((n,di)=>Array.from({length:Math.ceil(n/3)}, (_,ii)=>{
    const duration = n - ii*3 >= 3 ? 45 : (n%3)*15;
    const start = ['08:15','09:00','09:55','10:55','11:50','13:05','14:00','14:45'][ii];
    const [h,m] = start.split(':').map(Number), end = h*60+m+duration;
    return { name, date:'2026-11-'+(23+di), day:di, start, end:String(Math.floor(end/60)).padStart(2,'0')+':'+String(end%60).padStart(2,'0'), duration, subject:subjects[ti], group:groups[(ti+ii)%groups.length], activity:ii===7?'Консультация':ii===6&&ti%2===0?'Самостоятельная работа':'Урок', format:ti===3?'Онлайн':'Очно', joint:ii===4&&ti<3 };
  })));
  const dateLimit = (item, start = state.start, end = state.end) => item.date>=start && item.date<=end;
  const sum = rows => rows.reduce((n,r)=>n+r.duration,0);
  const distinct = rows => [...new Set(rows)];
  const reportDates = () => '<div class="op-filters">'+input('С','start',state.start,'date')+input('По','end',state.end,'date')+'</div>';
  const rangeCovered = () => state.start<=state.end && state.start>= '2026-11-23' && state.end<='2026-11-29';
  const reportMissing = () => empty(state.start>state.end?'Проверьте даты':'Нет опубликованного расписания за этот период', state.start>state.end?'Начало должно быть до конца периода.':'Для выбранного диапазона каноническая неделя недоступна.', button('Вернуться к 23–27 ноября','resetRange'));
  const loadRows = (teacher, start = state.start, end = state.end, activity = state.teacherActivity, format = state.teacherFormat, group = state.teacherGroup) => loadFixtures.filter(r=>(!teacher||r.name===teacher)&&dateLimit(r,start,end)&&(!activity||r.activity===activity)&&(!format||r.format===format)&&(!group||r.group===group));
  const bar = time => '<span class="op-bar" aria-hidden="true" style="--extent:'+Math.min(100,time/18)+'%"><i></i></span>';
  const intervalTable = rows => table('Опубликованные интервалы',['Дата','Время','Занятие','Группа','Длительность'],rows.map(r=>[r.date.slice(8)+'.11',r.start+'–'+r.end,r.subject+(r.format==='Онлайн'?' · онлайн':'')+(r.joint?' · совместно':''),r.group,minutes(r.duration)]));
  screens.load = () => {
    const every = names.filter(n=>n.toLocaleLowerCase('ru').includes(state.teacherQuery.toLocaleLowerCase('ru'))).map(n=>[n,loadRows(n)]).filter(([,r])=>r.length).sort((a,b)=>sum(b[1])-sum(a[1]));
    const active = every.find(([n])=>n===state.teacher);
    const breakdown = every.map(([n, rows])=>[button(escape(n),'teacher:'+names.indexOf(n),'op-name','aria-expanded="'+(n===state.teacher)+'"')+'<small>'+subjects[names.indexOf(n)]+'</small>', '<span class="op-duration">'+minutes(sum(rows))+'</span>'+bar(sum(rows)),minutes(sum(rows.filter(r=>r.activity!=='Самостоятельная работа'))), minutes(sum(rows.filter(r=>r.activity==='Самостоятельная работа'))), minutes(sum(rows.filter(r=>r.joint)))]);
    return '<div class="op-page">'+title('Нагрузка преподавателей','Длительность занятий по опубликованному расписанию')+reportDates()+
      '<div class="op-filters">'+input('Преподаватель','teacherQuery',state.teacherQuery,'search','placeholder="Имя или фамилия"')+select('Деятельность','teacherActivity',[['','Все виды'],'Урок','Консультация','Самостоятельная работа'],state.teacherActivity)+select('Формат','teacherFormat',[['','Все форматы'],'Очно','Онлайн'],state.teacherFormat)+select('Группа','teacherGroup',[['','Все группы'],...groups],state.teacherGroup)+'</div>'+
      (rangeCovered()?(every.length?'<div class="op-rule"><strong>Покрытие расписания</strong> Данные получены по '+names.length+' преподавателям. '+every.length+' в выборке.</div>'+table('Сводка по преподавателям',['Преподаватель','Всего по расписанию','Ведение занятия','Ответственность','Совместно'],breakdown)+
      (active?panel(active[0]+' · интервалы','<p>'+minutes(sum(active[1]))+' · '+active[1].length+' занятий · опубликованная неделя</p>'+intervalTable(active[1]),'id="op-detail"'):'')+
      '<details class="op-panel"><summary>Как посчитана нагрузка</summary><p>Сумма длительностей опубликованных интервалов. Ответственность выделена для самостоятельной работы без присутствия учителя. Совместные занятия могут входить в часы ведения. В примере все интервалы содержат время.</p></details>':empty('Преподаватели не найдены','Измените имя или фильтры.')):reportMissing())+'</div>';
  };
  function union(rows) {
    const mins = t => Number(t.slice(0,2))*60+Number(t.slice(3));
    const ranges = rows.map(r=>[mins(r.start),mins(r.end)]).sort((a,b)=>a[0]-b[0]);
    const merged = [];
    ranges.forEach(r=>{ const last=merged.at(-1); if (!last||last[1]<r[0]) merged.push(r); else last[1]=Math.max(last[1],r[1]); });
    return merged;
  }
  const classes = [...groups, 'Физика · лаборатория', 'Проектная группа', 'Английский · группа 2'];
  const classFixtures = loadFixtures.map((r,i)=> i%9===0 ? {...r,group:classes[8+i%3]} : r);
  screens.classes = () => {
    const visible = classes.filter((_,i)=>(!state.classType || (i<8?'Класс':'Учебная группа')===state.classType) && (!state.trajectory || (i<8?'Основная программа':'Профильная группа')===state.trajectory));
    const chosen = visible.includes(state.className)?state.className:visible[0];
    const dates = weekdays.slice(0,5).map((d,i)=>[d,i,'2026-11-'+(23+i)]).filter(([,,date])=>date>=state.start&&date<=state.end);
    const displayTime = n => String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');
    const audience = classFixtures.filter(r => r.group===chosen && dateLimit(r));
    const intervals = [...new Map(audience.map(r=>[[r.date,r.start,r.end,r.subject].join('|'),r])).values()];
    const days = dates.map(([d,i,date]) => [d,date,union(intervals.filter(r=>r.day===i))]);
    const grid = '<div class="op-clock" role="group" aria-label="Границы учебного дня"><div class="op-clock-scale"><span>08:00</span><span>10:00</span><span>12:00</span><span>14:00</span><span>16:00</span></div>'+days.map(([d,date,slots])=>{
      if (!slots.length) return '<div class="op-clock-row"><strong>'+d+' '+date.slice(8)+'</strong><p>Нет интервалов</p></div>';
      const start=slots[0][0], end=slots.at(-1)[1], busy=slots.reduce((n,[s,e])=>n+e-s,0);
      return '<div class="op-clock-row"><strong>'+d+' '+date.slice(8)+'</strong><div><p>'+displayTime(start)+'–'+displayTime(end)+' · '+minutes(busy)+' занятий · '+minutes(end-start-busy)+' перерыва</p><div class="op-clock-track" aria-hidden="true">'+slots.map(([s,e])=>'<i style="--left:'+((s-480)/4.8)+'%;--width:'+((e-s)/4.8)+'%"></i>').join('')+'</div></div></div>';
    }).join('')+'</div>';
    return '<div class="op-page">'+title('Нагрузка классов','Занятия, границы учебного дня и промежутки между ними')+reportDates()+
      '<div class="op-filters">'+select('Тип группы','classType',[['','Все типы'],'Класс','Учебная группа'],state.classType)+select('Траектория','trajectory',[['','Все траектории'],'Основная программа','Профильная группа'],state.trajectory)+select('Группа','className',visible,chosen)+'</div>'+
      (rangeCovered()?(chosen?'<div class="op-class-sheet">'+panel(escape(chosen),'<p>'+intervals.length+' опубликованных интервалов · '+days.length+' будних дней в диапазоне</p>'+grid,'aria-label="Недельный ритм группы"')+'<details class="op-panel"><summary>Занятия группы</summary>'+intervalTable(intervals)+'</details></div>':empty('Группы не найдены','Смените тип группы или траекторию.')):reportMissing())+
      '<p class="op-rule">Занятое время объединяет пересекающиеся интервалы. Урок с двумя преподавателями учитывается один раз. В синтетическом примере нет интервалов без времени.</p></div>';
  };
  const assessments = [
    ['24 ноября','Дробные выражения','Самостоятельная',22,18,1],['23 ноября','Рациональные дроби','Домашняя',22,16,4],
    ['20 ноября','Контрольная № 3','Контрольная',22,20,2],['19 ноября','Преобразование дробей','Самостоятельная',22,17,3],
    ['18 ноября','Подготовка к контрольной','Домашняя',22,14,5],['17 ноября','Практика по теме','Домашняя',22,11,6],
    ['16 ноября','Сокращение дробей','Контрольная',22,19,1],['15 ноября','Повторение темы','Самостоятельная',22,15,2],
    ['Без даты','Дополнительные задачи','Без категории',22,6,2]
  ];
  const journalNames = ['Алгебра · 8Б','Физика · 9А','История · 7А'];
  function journalStats() {
    const shift = journalNames.indexOf(state.journal);
    return assessments.filter((a,i)=>state.journalDate==='period'||(state.journalDate==='week'&&i<2)).map((a,i)=>{
      const n=state.subgroup?11:a[3], scored=Math.round(Math.max(0,a[4]-shift*(i%3+1))*n/a[3]), statuses=Math.min(n-scored,Math.round(a[5]*n/a[3]));
      const name = shift===1?['Закон Ома','Тепловые явления','Контрольная № 2','Лабораторная № 3','Задачи на сопротивление','Практика по теме','Разбор задач','Повторение темы','Дополнительные задачи'][i]:shift===2?['Эпоха реформ','Исторический источник','Контрольная № 1','Работа с картой','Подготовка к контрольной','Практика по теме','Термины','Повторение темы','Дополнительные задания'][i]:a[1];
      return { date:a[0], name, category:a[2], n, scored, statuses, blank:n-scored-statuses };
    });
  }
  const counts = rows => rows.reduce((totals,x)=>totals.map((v,i)=>v+[x.n,x.scored,x.statuses,x.blank][i]),[0,0,0,0]);
  const coverage = a => a[0]?Math.round(100*a[1]/a[0])+'%':'—';
  const fill = a => '<div class="op-fill" aria-hidden="true"><i style="width:'+(a[1]*100/a[0])+'%"></i><i style="width:'+(a[2]*100/a[0])+'%"></i></div>';
  screens.stats = () => {
    const rows = journalStats(), totals = counts(rows), active = rows.find((_,i)=>String(i)===state.journalDetail);
    return '<div class="op-page">'+title('Аналитика журналов','Текущие значения активных работ',button('К каталогу журналов →','go:catalog'))+
      '<div class="op-filters">'+select('Журнал','journal',journalNames,state.journal)+select('Группа','subgroup',[['','Все строки'],'Группа 1','Группа 2'],state.subgroup)+select('Даты работ','journalDate',[['period','Весь триместр'],['week','23–29 ноября'],['empty','26–27 ноября']],state.journalDate)+'</div>'+
      '<div class="op-rule"><strong>II триместр · действующий</strong> · Синхронизация 25 ноября, 08:12'+(state.journalDate!=='period'?' · 1 работа без даты вне выборки':' · 1 работа без даты')+'</div>'+
      (rows.length?'<section class="op-snapshot"><div><h2>Числовые результаты</h2><strong>'+coverage(totals)+'</strong><p>'+totals[1]+' из '+totals[0]+' подходящих ячеек</p></div><div>'+fill(totals)+'<div class="op-key"><span>Числа · '+totals[1]+'</span><span>Статусы · '+totals[2]+'</span><span>Пустые · '+totals[3]+'</span></div></div></section>'+
      panel('По категориям',distinct(rows.map(r=>r.category)).map(c=>{
        const part=counts(rows.filter(r=>r.category===c));
        return '<div class="op-category"><strong>'+c+'</strong>'+fill(part)+'<span>'+part[1]+' из '+part[0]+'</span></div>';
      }).join(''))+table('Работы в журнале',['Работа','Дата','Категория','Числа','Статусы','Пустые'],rows.map((r,i)=>[button(escape(r.name),'journalDetail:'+i,'op-name','aria-expanded="'+(state.journalDetail===String(i))+'"'),r.date,r.category,r.scored,r.statuses,r.blank]))+
      (active?panel(escape(active.name),'<p>'+active.date+' · '+active.category+' · '+active.n+' подходящих строк</p>'+button('Открыть журнал →','go:journal','primary'),'id="op-detail"'):''):empty('Нет работ в эти даты','Числовое покрытие недоступно. Выберите другой период.'))+
      '<details class="op-panel"><summary>Итоги триместра</summary><p>'+ (state.subgroup?'11':'22')+' итоговых ячеек по выбранным строкам. Итоги относятся ко всему триместру.</p>'+button('Открыть таблицу журнала →','go:journal')+'</details>'+
      '<details class="op-panel"><summary>Что входит в расчёт</summary><p>Одна подходящая строка, включая команду, даёт одну ячейку на работу. Число 0 входит в числовые результаты. Отметка со статусом учитывается отдельно от пустой ячейки. Итоги исключены из расчёта работ.</p></details></div>';
  };
  const persist = () => { try { localStorage.setItem('my-island-next-widgets',JSON.stringify(state.widgets)); } catch {} };
  const widgetName = type => type==='day'?'Расписание дня':'Нагрузка преподавателей';
  function widgetBody(w) {
    if (w.type==='day') {
      const day=w.date&&w.date.startsWith('2026-11-')?Number(w.date.slice(8))-23:w.date?-1:2;
      return '<p>'+(w.date?escape(w.date.split('-').reverse().join('.')):'Сегодня · 25 ноября')+'</p>'+ (day<0||day>6?empty('Нет опубликованного дня','Выберите дату из 23–29 ноября.'):timeline(day,true))+button('К оперативному дню →','widgetGo:'+w.id);
    }
    const rows=loadRows(w.teacher,w.start||'2026-11-23',w.end||'2026-11-29',w.activity,w.format,w.group);
    const ranking=distinct(rows.map(r=>r.name)).map(n=>[n,sum(rows.filter(r=>r.name===n))]).sort((a,b)=>b[1]-a[1]).slice(0,4);
    return '<p>'+(w.start||w.end?escape((w.start||'Начало недели')+' — '+(w.end||'Конец недели')):'Текущая неделя · 23–29 ноября')+(w.teacher?' · '+escape(w.teacher):'')+(w.activity?' · '+escape(w.activity):'')+(w.format?' · '+escape(w.format):'')+(w.group?' · '+escape(w.group):'')+'</p>'+
      (rows.length?ranking.map(([n,m])=>'<div class="op-widget-row"><strong>'+escape(n)+'</strong><span>'+minutes(m)+'</span></div>').join(''):empty('Нет интервалов','Проверьте диапазон и фильтры.'))+button('К нагрузке преподавателей →','widgetGo:'+w.id);
  }
  function widgetEditor(w, index) {
    const key = 'widgetInput:'+w.id+':';
    return '<fieldset class="op-widget-editor"><legend>Настройки виджета</legend><div class="op-filters">'+select('Размер',key+'size',[['compact','Компактный'],['wide','Широкий']],w.size)+
      (w.type==='day'?input('Дата (пусто — сегодня)',key+'date',w.date,'date'):select('Преподаватель',key+'teacher',[['','Все'],...names],w.teacher)+select('Вид деятельности',key+'activity',[['','Все виды'],'Урок','Консультация','Самостоятельная работа'],w.activity)+select('Формат',key+'format',[['','Все форматы'],'Очно','Онлайн'],w.format)+select('Группа',key+'group',[['','Все группы'],...groups],w.group)+input('С (пусто — с понедельника)',key+'start',w.start,'date')+input('По (пусто — до конца недели)',key+'end',w.end,'date'))+'</div>'+
      '<div class="op-actions">'+button('Выше','widgetUp:'+w.id,'',index===0?'disabled':'')+button('Ниже','widgetDown:'+w.id,'',index===state.widgets.length-1?'disabled':'')+button('Убрать','widgetRemove:'+w.id)+'</div></fieldset>';
  }
  screens.widgets = () => '<div class="op-page">'+title('Моё пространство','Раскладка и фильтры сохраняются для каждого виджета',button(state.widgetEdit?'Готово':'Настроить','widgetEdit','primary'))+
    (state.widgetEdit?'<div class="op-widget-toolbar">'+select('Добавить виджет','widgetAdd',[['','Выберите виджет'],['day',widgetName('day')],['load',widgetName('load')]],state.widgetAdd)+button('Добавить','widgetAdd','primary',!state.widgetAdd?'disabled':'')+button('Сбросить раскладку','widgetReset')+'</div>':'')+
    (state.widgets.length?'<div class="op-widget-grid">'+state.widgets.map((w,i)=>'<section class="op-panel op-widget '+w.size+'"><h2>'+widgetName(w.type)+'</h2>'+(state.widgetEdit?widgetEditor(w,i):'')+widgetBody(w)+'</section>').join('')+'</div>':empty('Здесь пока нет виджетов','Добавьте расписание дня или нагрузку преподавателей.',state.widgetEdit?'':button('Настроить','widgetEdit')))+'</div>';
  const diagnostics = () => '<div class="op-diag"><h3>Записи школьной базы</h3>'+table('Записи школьной базы',['Раздел','Записи','Переход'],[
    ['Аккаунты приложения',214,button('Люди →','go:people')],['Канонические личности',253,button('Люди →','go:people')],['Основные группы',18,button('Группы →','go:people')],['Активные членства',312,button('Группы →','go:people')],['Источники школы',7,'Источники'],['Запуски синхронизации',32,'Источники'],['Кандидаты изменений',13,'Изменения'],['Проблемы разрешения',3,'Проблемы'],['События аудита',14,'История']])+
    '<h3>Проверки схемы</h3><p>Открытые проблемы источников · 3. Активные кандидаты · 5. Исторические изменения · 8.</p><details><summary>Состав проверок базы</summary><p>Актуальная схема, события синхронизации, ревизии изменений.</p></details></div>';
  function googleCard() {
    const label = { grant:'Нужно переподключить', yes:'Подключено', access:'Требуется доступ к таблице', failed:'Ошибка проверки' }[state.google];
    return '<section class="op-panel op-google"><div class="op-panel-header"><h2>Подключение Google</h2>'+status(label,state.google==='yes'?'good':'warn')+'</div>'+
      '<p>'+ ({ grant:'Срок разрешения истёк. Подключите школьный аккаунт, чтобы обновлять журнал.',yes:'Доступ к журналу 9А подтверждён.',access:'Предоставьте школьному аккаунту доступ к исходной таблице журнала.',failed:'Не удалось проверить подключение. Повторите проверку.' })[state.google]+'</p>'+
      '<dl><dt>Аккаунт</dt><dd>school@example.org</dd><dt>Последняя проверка</dt><dd>25 ноября, 08:12</dd></dl>'+
      '<div class="op-actions">'+(state.google==='grant'||state.google==='failed'?button('Переподключить Google','reconnect','primary'):state.google==='access'?button('К источнику журнала →','go:catalog','primary'):'')+button('Проверить снова','checkGoogle')+'</div>'+
      (state.settingsNotice?'<p class="op-rule" role="status">'+escape(state.settingsNotice)+'</p>':'')+
      '<details><summary>Разрешения и источники</summary><p>Журнал: Алгебра · 9А. Экспорт расписания и синхронизация Classroom требуют отдельных разрешений.</p></details></section>';
  }
  const syncPanel = () => panel('Синхронизация','<dl><dt>Последняя успешная</dt><dd>25 ноября, 08:12</dd><dt>Последняя сверка</dt><dd>24 ноября, 17:45 · завершена</dd></dl>');
  const healthPanel = () => panel('Backend и школьная база','<dl><dt>Backend</dt><dd>'+status('Доступен','good')+'</dd><dt>Школьная база</dt><dd>'+status('Подключена','good')+'</dd></dl>');
  const announcements = () => panel('Информация и объявления','<form data-op-form="announce" class="op-form"><label class="op-field">Заголовок<input name="title" required></label><label class="op-field">Сообщение<textarea name="body" rows="4" required></textarea></label><label class="op-field">Аудитория<select name="audience"><option>Все</option><option>Ученики</option><option>Учителя</option></select></label><label class="op-check"><input type="checkbox" name="pinned"> Закрепить</label><button class="op-btn primary">Опубликовать</button></form><ul class="op-announcements">'+state.announcements.map(a=>'<li><strong>'+escape(a[0])+'</strong><p>'+escape(a[1])+'</p><small>'+escape(a[2])+' · Опубликовано'+(a[3]?' · Закреплено':'')+'</small></li>').join('')+'</ul>');
  const settingsViews = [['connection','Подключения'],['diagnostics','Диагностика'],['school','Информация школы']];
  screens.settings = variant => {
    const v=String(variant).toUpperCase();
    const core=googleCard()+syncPanel()+healthPanel();
    const diag=panel('Диагностика и технические сведения',diagnostics());
    const school=panel('Оформление','<p>Системная тема следует настройкам устройства.</p>')+announcements();
    if (v==='S') return '<div class="op-page op-settings op-settings-s">'+title('Настройки','Подключения и состояние школьных сервисов',button('Обновить','checkGoogle'))+
      healthPanel()+googleCard()+syncPanel()+'<details class="op-panel"><summary>Диагностика и технические сведения</summary>'+diagnostics()+'</details><details class="op-panel"><summary>Информация школы</summary>'+school+'</details></div>';
    if (v==='D') return '<div class="op-page op-settings op-settings-d">'+title('Настройки','Управление школьными сервисами',button('Обновить','checkGoogle'))+
      '<div class="op-settings-ledger"><div>'+googleCard()+'</div><div>'+syncPanel()+healthPanel()+'</div></div><details class="op-panel"><summary>Диагностика и технические сведения</summary>'+diagnostics()+'</details><details class="op-panel"><summary>Информация школы</summary>'+school+'</details></div>';
    return '<div class="op-page op-settings op-settings-c">'+title('Настройки','Управление школьными сервисами',button('Обновить','checkGoogle'))+
      '<div class="op-preferences">'+tabs(settingsViews,state.settings,'settings','Разделы настроек')+'<div class="op-stack">'+({connection:core,diagnostics:diag,school})[state.settings]+'</div></div></div>';
  };
  function previewShell(variant) {
    const v = String(variant).toUpperCase();
    const entities = state.role==='student'?students:distinct([...names,...(state.previewTeacher?[state.previewTeacher]:[])]);
    const eligible = entities.filter((n,i)=>(!state.query||n.toLocaleLowerCase('ru').includes(state.query.toLocaleLowerCase('ru'))) &&
      (!state.previewClass||groups[i%groups.length]===state.previewClass) && (!state.previewGroup||groups[(i+2)%groups.length]===state.previewGroup));
    const fields = select('Режим','role',[['student','Ученик'],['teacher','Учитель']],state.role)+
      input('Поиск человека','query',state.query,'search','placeholder="Имя или фамилия"')+
      select('Класс','previewClass',[['','Все классы'],...groups],state.previewClass)+select('Группа','previewGroup',[['','Все группы'],...groups],state.previewGroup)+
      select('Человек','person',[['','Выберите человека'],...eligible],eligible.includes(state.person)?state.person:'');
    const options = input('Дата','previewDate',state.previewDate,'date')+select('Ширина','previewWidth',['360','390','430'],state.previewWidth)+input('Высота','previewHeight',state.previewHeight,'number','min="480" max="1100" step="20"');
    const open = button('Открыть приложение','previewOpen','primary',eligible.includes(state.person)?'':'disabled');
    const close = state.previewOpen?button('Обновить данные','previewRefresh')+button(v==='S'?'Завершить предпросмотр':'Завершить','previewClose'):'';
    const scope = state.role==='student'?'Ученик':'Учитель';
    const summary = state.previewOpen ? '<dl><dt>Человек</dt><dd>'+escape(state.person)+'</dd><dt>Источник</dt><dd>Изолированная тестовая база</dd><dt>Группы</dt><dd>'+groups[Math.max(0,entities.indexOf(state.person))%groups.length]+'</dd><dt>Расписание</dt><dd>Опубликовано · 12 занятий за неделю</dd><dt>Журналы</dt><dd>2 сопоставленных журнала</dd><dt>Сессия до</dt><dd>25 ноября, 14:30</dd></dl><details><summary>Замечания источников</summary><p>За выбранный день одно занятие отложено.</p>'+button('Проверить публикацию →','go:today')+'</details>' : '<p>Выберите человека и откройте приложение.</p>';
    const diag = v==='S'?panel('Диагностика',summary):'<details class="op-panel op-preview-diag"><summary>Диагностика сеанса</summary>'+summary+'</details>';
    const show = state.previewOpen?'<div class="op-phone-caption">'+escape(state.person)+' · '+scope+' · '+escape(state.previewWidth)+' × '+escape(state.previewHeight)+'</div><p class="op-phone-note">Пример экрана для этой роли</p><div class="op-phone" role="region" aria-label="Область прокрутки Campus" tabindex="0" style="--phone-width:'+Number(state.previewWidth)+'px;--phone-height:'+Number(state.previewHeight)+'px"><img src="../assets/campus-'+state.role+'.png" alt="Исходный экран Campus в режиме '+(state.role==='student'?'ученика':'учителя')+'"></div>':empty('Выберите человека','Укажите ученика или преподавателя и откройте приложение.');
    const filters = '<div class="op-preview-fields">'+fields+'</div>'+(!eligible.length?'<p role="status">По этим условиям людей не найдено.</p>':'');
    const display = '<div class="op-preview-fields op-viewport-fields">'+options+'</div>';
    const actions = '<div class="op-actions">'+open+close+'</div>';
    const message = (state.previewMessage?'<p class="op-rule" role="status">'+escape(state.previewMessage)+'</p>':'');
    return '<div class="op-page op-preview op-preview-'+v.toLowerCase()+'">'+title('Предпросмотр','Проверка данных, доступных человеку в My Island')+
      '<div class="op-rule"><strong>Только чтение</strong> · Изолированная тестовая база</div>'+message+
      (v==='D'? '<details class="op-panel op-preview-picker" open><summary>Выбор человека и параметры экрана</summary>'+filters+display+actions+'</details><section class="op-stage" aria-label="Экран Campus">'+show+'</section>'+diag:
      '<div class="op-preview-grid"><div>'+panel('Выберите человека',filters+(v==='S'?display:'')+actions)+(v==='C'?panel('Параметры экрана',display):'')+diag+'</div><section class="op-stage" aria-label="Экран Campus">'+show+'</section></div>')+'</div>';
  }
  screens.preview = previewShell;
  window.schoolPreviewTeacher = name => {
    Object.assign(state, { role:'teacher', person:name, previewTeacher:name, query:'', previewClass:'', previewGroup:'', previewOpen:true, previewMessage:'' });
    window.schoolGo?.('preview');
  };

  function render(focus) {
    window.schoolDraw?.();
    if (focus) {
      const next = document.querySelector(focus);
      if (next) { if (!next.matches('button, input, select, summary, a')) next.tabIndex=-1; next.focus({preventScroll:true}); if (next.id==='op-detail') next.scrollIntoView({ block:'nearest' }); }
    }
  }
  const run = {
    day(arg) { state.day=Number(arg); state.issue=''; },
    issue(arg) { state.issue=arg===state.issue?'':arg; },
    teacher(arg) { const teacher=names[Number(arg)]; state.teacher=state.teacher===teacher?'':teacher; },
    journalDetail(arg) { state.journalDetail=arg===state.journalDetail?'':arg; },
    resetRange() { state.start='2026-11-23'; state.end='2026-11-27'; },
    widgetEdit() { state.widgetEdit=!state.widgetEdit; },
    widgetAdd() { if (!state.widgetAdd) return; state.widgets.push({id:state.nextWidget++,type:state.widgetAdd,size:'compact',date:'',teacher:'',start:'',end:'',activity:'',group:'',format:''}); state.widgetAdd=''; persist(); },
    widgetRemove(arg) { state.widgets=state.widgets.filter(w=>String(w.id)!==arg); persist(); },
    widgetReset() { state.widgets=initialWidgets().slice(0,2); persist(); },
    widgetUp(arg) { swapWidget(arg,-1); },
    widgetDown(arg) { swapWidget(arg,1); },
    settings(arg) { state.settings=arg; },
    reconnect() { state.google='yes'; state.settingsNotice='В макете подключение восстановлено.'; },
    checkGoogle() { state.settingsNotice='Проверка завершена. Состояние подключения обновлено.'; },
    previewOpen() { state.previewOpen=true; state.previewMessage=''; },
    previewClose() { state.previewOpen=false; state.previewMessage='Предпросмотр завершён.'; },
    previewRefresh() { state.previewMessage='Данные предпросмотра обновлены.'; }
  };
  function swapWidget(id, direction) {
    const index=state.widgets.findIndex(w=>String(w.id)===id);
    if (index<0||!state.widgets[index+direction]) return;
    [state.widgets[index],state.widgets[index+direction]]=[state.widgets[index+direction],state.widgets[index]];
    persist();
  }
  document.addEventListener('click', event => {
    const target=event.target.closest('[data-op]');
    if (!target) return;
    const [key,arg]=target.dataset.op.split(':');
    if (key==='go') { window.schoolGo?.(arg); return; }
    if (key==='widgetGo') {
      const widget=state.widgets.find(w=>String(w.id)===arg);
      if (!widget) return;
      if (widget.type==='day') {
        state.day=widget.date&&widget.date.startsWith('2026-11-')?Math.max(0,Math.min(6,Number(widget.date.slice(8))-23)):2;
        state.issue='';
        window.schoolGo?.('today');
      } else {
        state.start=widget.start||'2026-11-23';
        state.end=widget.end||'2026-11-29';
        state.teacherQuery=widget.teacher||'';
        state.teacherActivity=widget.activity||'';
        state.teacherGroup=widget.group||'';
        state.teacherFormat=widget.format||'';
        window.schoolGo?.('load');
      }
      return;
    }
    if (!run[key]) return;
    run[key](arg);
    const detail = (key==='issue'&&state.issue)||(key==='teacher'&&state.teacher)||(key==='journalDetail'&&state.journalDetail);
    render(detail?'#op-detail':'[data-op="'+target.dataset.op+'"]');
  });
  document.addEventListener('change', event => {
    const key=event.target.dataset.opInput;
    if (!key) return;
    const value=event.target.value;
    if (key.startsWith('widgetInput:')) {
      const [,id,property]=key.split(':');
      const widget=state.widgets.find(w=>String(w.id)===id);
      if (widget) { widget[property]=value; persist(); }
    } else {
      if (key==='role') { state.person=''; state.previewClass=''; state.previewGroup=''; state.previewOpen=false; }
      if (key==='person') { state.previewOpen=false; state.previewMessage=''; }
      if (key==='previewHeight') state[key]=String(Math.max(480,Math.min(1100,Number(value)||760)));
      else state[key]=value;
      if (['journal','subgroup','journalDate'].includes(key)) state.journalDetail='';
    }
    render('[data-op-input="'+key+'"]');
  });
  document.addEventListener('submit', event => {
    if (event.target.dataset.opForm!=='announce') return;
    event.preventDefault();
    const form=new FormData(event.target);
    state.announcements.unshift([form.get('title'),form.get('body'),form.get('audience'),form.has('pinned')]);
    render('[data-op-form="announce"] input');
  });
})();
