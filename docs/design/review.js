const screens = {
 catalog: ['Каталог журналов','BookOpen','Класс и период расположены над списком предметов.','Сохраняет знакомый вход через класс и период; уплотняет список и действия источника.','Собирает выбор журнала и контекст записи в одном рабочем пространстве.','Выберите предмет и откройте конкретный журнал.'],
 journal: ['Журнал с оценками','Rows3','Фрагмент действующей матрицы II периода. Ноль и отсутствие результата имеют разное значение.','Сохраняет матрицу оценок и акцент на строке ученика.','Связывает выбранную работу с её подробностями, сохраняя контекст группы.','Выберите работу или ячейку и посмотрите её расшифровку.'],
 people: ['Ученики и группы','GraduationCap','Классы и учебные группы собраны на общей доске.','Упорядочивает доску и фильтры, оставляя привычную точку входа.','Выбор класса определяет состав рабочей области и доступные подробности.','Найдите класс, затем откройте состав группы или карточку ученика.'],
 teachers: ['Преподаватели','UserRoundCog','Текущий каталог предлагает таблицу и переход в профиль человека.','Сохраняет табличный обзор и полную карточку преподавателя.','Делает предмет, группы и текущий выбор опорой для работы со списком.','Найдите преподавателя по предмету и перейдите к его назначениям.'],
 profile: ['Профиль преподавателя','Layers','На странице профиля собраны личные данные и связанные назначения.','Объединяет подписи полей, заголовок и действия формы.','Выносит рабочие назначения вперёд и сохраняет доступ к связанному каталогу.','Проверьте назначения и откройте редактирование профиля.']
};
const future = [
 ['today','Сегодня','День завуча, изменения и отдельный черновик следующей недели.'],
 ['load','Нагрузка преподавателей','Минуты и занятия по опубликованной неделе с детализацией.'],
 ['stats','Классы и журналы','Нагрузка класса и заполненность журналов в разных единицах.'],
 ['widgets','Персональное пространство','Одна раскладка для текущих задач и несколько представлений общих данных.']
];
const nav=document.querySelector('#screen-nav'),comparison=document.querySelector('#comparison-body');
let selected='';
function show(key,changeHash=false){
 if(!screens[key])key='catalog';
 selected=key;
 const [label,,current,a,b,scenario]=screens[key];
 nav.innerHTML=Object.entries(screens).map(([k,[name,icon]])=>'<button aria-pressed="'+(k===key)+'" data-screen="'+k+'">'+designIcons[icon]+name+'</button>').join('');
 const variants=[['current','Сейчас','Исходный интерфейс',current,'assets/'+key+'.png'],['a','А','Бережная унификация',a,'screens/A-'+key+'.png'],['b','Б','Рабочий контекст',b,'screens/B-'+key+'.png']];
 comparison.innerHTML='<div class="triptych">'+variants.map(([v,letter,title,note,src])=>'<article class="plate" data-direction="'+v+'"><header class="plate-head"><span class="plate-letter">'+letter+'</span><h3>'+title+'</h3></header><button class="sheet" data-zoom="'+src+'" data-name="'+label+' · '+title+'" aria-label="Увеличить: '+label+', '+title+'"><img src="'+src+'" alt="'+label+' · '+title+'"></button><div class="plate-foot"><p>'+note+'</p>'+(v==='current'?'<button class="muted-link" data-zoom="'+src+'" data-name="'+label+' · текущий экран">Рассмотреть снимок ↗</button>':'<a class="action-link" href="prototype.html?v='+v.toUpperCase()+'&amp;screen='+key+'">Открыть прототип ↗</a>')+'</div></article>').join('')+'</div><p class="scenario"><strong>Проверка сценария</strong><span>'+scenario+'</span></p>';
 if(changeHash)history.replaceState(null,'','#'+key);
}
nav.addEventListener('click',e=>{const b=e.target.closest('[data-screen]');if(b){show(b.dataset.screen,true);nav.querySelector('[aria-pressed=true]').focus({preventScroll:true});}});
const dialog=document.querySelector('#viewer');
document.addEventListener('click',e=>{const b=e.target.closest('[data-zoom]');if(!b)return;document.querySelector('#viewer-title').textContent=b.dataset.name;const img=document.querySelector('#viewer-img');img.alt=b.dataset.name;img.src=b.dataset.zoom;document.querySelector('#viewer-file').href=b.dataset.zoom;dialog.showModal();});
document.querySelector('#close-view').onclick=()=>dialog.close();
document.querySelector('#concepts').innerHTML=future.map(([id,name,note])=>'<article><a class="concept-pic" href="prototype.html?v=B&amp;screen='+id+'"><img loading="lazy" src="screens/B-'+id+'.png" alt="'+name+' · открыть концепцию"></a><h3>'+name+'</h3><p>'+note+'</p><a class="action-link" href="prototype.html?v=B&amp;screen='+id+'">Открыть ↗</a></article>').join('');
show(location.hash.slice(1));
document.querySelector('#sample-book').innerHTML=designIcons.BookOpen;
const refCaptions={
  reference:'Текущий конструктор на тестовых данных проекта.',
  'full-week':'Архивный снимок заполненной недели. Видны плотность сетки и цвета занятий.',
  inspector:'Текущий конструктор с открытым инспектором урока.'
};
document.querySelector('#ref-nav').addEventListener('click',event=>{
  const choice=event.target.closest('[data-ref]');
  if(!choice)return;
  const key=choice.dataset.ref,url='assets/'+key+'.png',shot=document.querySelector('.ref-shot');
  document.querySelectorAll('[data-ref]').forEach(el=>el.setAttribute('aria-pressed',String(el===choice)));
  shot.dataset.zoom=url;shot.dataset.name=choice.textContent;
  shot.querySelector('img').src=url;shot.querySelector('img').alt=refCaptions[key];
  document.querySelector('#ref-original').href=url;
  document.querySelector('#ref-source').textContent=refCaptions[key];
});
