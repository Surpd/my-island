const menuItems = [
  ['today','Сегодня','House'], ['schedule','Расписание','CalendarDays'], ['people','Ученики и группы','GraduationCap'],
  ['catalog','Журналы и оценки','BookOpen'], ['teachers','Преподаватели','UserRoundCog'], ['preview','Предпросмотр','Search'], ['settings','Настройки','Settings']
];
const parentSection = { journal:'catalog',profile:'teachers',load:'today',stats:'today',widgets:'today' };
const detailNames = {journal:'Открытый журнал',profile:'Карточка преподавателя',load:'Нагрузка преподавателей',stats:'Классы и журналы',widgets:'Моё пространство'};
const known = new Set([...menuItems.map(x=>x[0]), ...Object.keys(parentSection)]);
const variant = new URLSearchParams(location.search).get('v') === 'A' ? 'A' : 'B';
document.body.dataset.variant = variant;
function getScreen(){const key = location.hash.slice(1) || new URLSearchParams(location.search).get('screen') || 'catalog';return known.has(key)?key:'catalog';}
window.go = function(key){if(!known.has(key))return;if(location.hash==='#'+key)draw();else location.hash=key;};
function draw(){
  const screen=getScreen(), active=parentSection[screen]||screen;
    const toolsBar=document.querySelector('#prototype-tools');
  const net=new URLSearchParams(location.search);
  toolsBar.hidden=net.has('capture');
  toolsBar.innerHTML='<a href="index.html#'+(['catalog','journal','people','teachers','profile'].includes(screen)?screen:'future')+'">← Страница приёмки</a><span>Демонстрационные данные</span>'+(['catalog','journal','people','teachers','profile'].includes(screen)?['A','B'].map(v=>'<a class="switch-proto" '+(v===variant?'aria-current="true"':'')+' href="prototype.html?v='+v+'&screen='+screen+'">'+(v==='A'?'А · Бережная унификация':'Б · Рабочий контекст')+'</a>').join(''):'');
  const current=menuItems.find(x=>x[0]===active);
  document.body.dataset.screen=screen;
  document.querySelector('#school-menu').innerHTML='<div class="brand">My Island<span>.</span></div><small class="brand-caption">Школьное пространство</small><nav aria-label="Разделы школы">'+menuItems.map(([id,name,icon])=>'<button type="button" data-go="'+id+'" '+(id===active?'aria-current="page"':'')+'>'+designIcons[icon]+'<span>'+name+'</span>'+(id===active?designIcons.ChevronRight:'')+'</button>').join('')+'</nav><div class="side-foot"><span>Администратор</span><a href="index.html" target="_top">'+designIcons.ArrowLeft+' К сравнению</a></div>';
  document.querySelector('#trail').innerHTML='Админ '+designIcons.ChevronRight+' '+(parentSection[screen]?'<button type="button" data-go="'+active+'">'+current[1]+'</button> '+designIcons.ChevronRight+' '+detailNames[screen]:current[1]);
  const content = document.querySelector('.page-body');
  if(screen==='schedule')content.innerHTML='<h1 class="title">Конструктор расписания</h1><a class="ref-static" href="assets/inspector.png"><img src="assets/inspector.png" alt="Защищённый исходный конструктор, сетка и открытый инспектор"></a>';
  else if(['catalog','journal'].includes(screen))content.innerHTML=window.journalsView(screen,variant);
  else if(['people','teachers','profile'].includes(screen))content.innerHTML=window.peopleView(screen,variant);
  else content.innerHTML=window.futureView(screen,variant);
  window.scrollTo(0,0);
  document.title='My Island · '+(detailNames[screen]||current[1])+' · '+(variant==='A'?'А':'Б');
}
document.addEventListener('click',event=>{const target=event.target.closest('[data-go]');if(target)go(target.dataset.go);});
window.addEventListener('hashchange',draw);
draw();

document.querySelector('.skip').addEventListener('click',event=>{
  event.preventDefault();
  document.querySelector('#content').focus();
});
