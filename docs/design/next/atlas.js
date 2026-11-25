const core={
catalog:['Каталог журналов','Для обзора предметов и покрытия источниками предпочтителен С. Проверьте, как быстро получается найти нужный класс и период.'],
journal:['Журнал с оценками','С для сверки результатов нескольких учеников. Д стоит проверить при разборе одной работы и её пересдач.'],
people:['Ученики и группы','Сохраняйте доску С как вход в раздел. Для поиска конкретного ученика и перехода к его группам сравните маршрут Д.'],
teachers:['Преподаватели','Для общего обхода преподавателей подходит реестр С. Д полезен, когда исходная точка — предмет или назначение.'],
profile:['Профиль преподавателя','Д для составления расписания: сначала неделя преподавателя, затем назначения. С удобнее, когда главная задача — проверить распределение по группам.'],
forms:['Редактирование','С для короткой правки на одном листе. Д для последовательного ввода с итоговой проверкой. Сравните исправление ошибки и возврат без потери введённого.'],
settings:['Настройки','С для возвращения к одной настройке. Д для обхода нескольких служб. Сбой подключения должен вести к объяснению и действию восстановления.'],
preview:['Административный просмотр','С для широкого монитора, Д с верхними параметрами для узкого окна. Перед сменой роли сверьте имя, дату и видимую область Campus.']};
const future={today:['Сегодня','Начните с опубликованного дня. Выберите изменение, проверьте затронутое занятие и готовность следующей недели.'],load:['Нагрузка преподавателей','Сопоставьте опубликованную неделю с шаблоном и откройте занятия конкретного преподавателя.'],classes:['Нагрузка классов','Проверьте учебный день и окна по нужному классу и учебной подгруппе.'],stats:['Аналитика журналов','Сверьте заполненность с источником, затем перейдите к отдельной работе.'],widgets:['Персональное пространство','Соберите собственную раскладку из доступных показателей и настройте экземпляры отдельно.']};
const dirs={S:'Исходный образец',C:'С · Обзор',D:'Д · По задаче'};
const proto=(id,v)=>'next/workspace.html?v='+v+'&screen='+id;
const shot=(id,v)=>'next/shots/'+v+'-'+id+'.png';
const sel=document.querySelector('#screen'),tabs=document.querySelector('#tabs');
sel.innerHTML=Object.entries(core).map(([k,[n]])=>'<option value="'+k+'">'+n+'</option>').join('');
function show(id){
 if(!Object.hasOwn(core,id))id='catalog';
 sel.value=id;
 tabs.innerHTML=Object.entries(core).map(([k,[n]])=>'<button data-key="'+k+'" aria-pressed="'+(k===id)+'">'+n+'</button>').join('');
 document.querySelector('#plates').innerHTML=Object.entries(dirs).map(([v,n])=>'<article><h3>'+n+'</h3><button data-image="'+shot(id,v)+'" data-title="'+core[id][0]+' · '+n+'" aria-label="Увеличить '+n+'"><img src="'+shot(id,v)+'" alt="'+core[id][0]+' · '+n+'"></button><a href="'+proto(id,v)+'">Открыть '+(v==='S'?'образец':'прототип')+' ↗</a></article>').join('');
 document.querySelector('#rec').textContent=core[id][1];
}
function choose(id){show(id);history.replaceState(null,'','#'+id)}
tabs.onclick=e=>{const b=e.target.closest('[data-key]');if(b){choose(b.dataset.key);tabs.querySelector('[aria-pressed=true]').focus({preventScroll:true})}};
sel.onchange=()=>choose(sel.value);
document.querySelector('#future-list').innerHTML=Object.entries(future).map(([k,[n,s]])=>'<article><a href="'+proto(k,'C')+'"><img loading="lazy" src="'+shot(k,'C')+'" alt="Открыть '+n+'"></a><div><h3><a href="'+proto(k,'C')+'">'+n+' ↗</a></h3><p>'+s+'</p></div></article>').join('');
const dialog=document.querySelector('dialog');
document.querySelector('#plates').onclick=e=>{const b=e.target.closest('[data-image]');if(!b)return;document.querySelector('#shot-title').textContent=b.dataset.title;document.querySelector('#actual').href=b.dataset.image;const img=document.querySelector('#zoom-img');img.alt=b.dataset.title;img.src=b.dataset.image;dialog.showModal()};
document.querySelector('#close').onclick=()=>dialog.close();show(location.hash.slice(1));

window.addEventListener('hashchange',()=>{const key=location.hash.slice(1);if(Object.hasOwn(core,key))show(key)});
