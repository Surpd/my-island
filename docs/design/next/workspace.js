(() => {
 const entries=[['today','Сегодня','House'],['schedule','Расписание','CalendarDays'],['people','Ученики и группы','GraduationCap'],['catalog','Журналы и оценки','BookOpen'],['teachers','Преподаватели','UserRoundCog'],['preview','Предпросмотр','Search'],['settings','Настройки','Settings']];
 const more={journal:'Журнал',profile:'Профиль преподавателя',forms:'Редактирование',load:'Нагрузка преподавателей',classes:'Нагрузка классов',stats:'Аналитика журналов',widgets:'Моё пространство'};
 const names={...Object.fromEntries(entries.map(([k,n])=>[k,n])),...more};
 const parent={journal:'catalog',profile:'teachers',forms:'people',load:'today',classes:'today',stats:'catalog',widgets:'today'};
 const query=new URLSearchParams(location.search);
 const key=()=>Object.hasOwn(names,location.hash.slice(1))?location.hash.slice(1):Object.hasOwn(names,query.get('screen'))?query.get('screen'):'catalog';
 const variant=['C','D','S'].includes(query.get('v'))?query.get('v'):'C';
 const selector=document.querySelector('#direction');
 selector.value=variant;
 document.body.dataset.variant=variant;
 if(query.has('capture'))document.body.classList.add('capturing');
 window.schoolGo=k=>{if(Object.hasOwn(names,k)){if(key()===k)window.schoolDraw();else location.hash=k;}};
 window.schoolDraw=()=>{
   const k=key(), top=parent[k]||k;
   document.body.dataset.screen=k;
   selector.parentElement.hidden=['today','load','classes','stats','widgets','schedule'].includes(k);
   document.querySelector('#route-name').textContent=names[k];
   document.title=names[k]+' · My Island';
   document.querySelector('#school-nav').innerHTML=entries.map(([id,n,icon])=>'<a href="#'+id+'"'+(id===top?' aria-current="page"':'')+'>'+designIcons[icon]+'<span>'+n+'</span>'+(id===top?designIcons.ChevronRight:'')+'</a>').join('');
   const main=document.querySelector('#work');
   main.innerHTML=k==='schedule'?'<h1>Конструктор расписания</h1><a href="../assets/reference.png"><img width="1600" height="1100" class="locked-schedule" src="../assets/reference.png" alt="Защищённая исходная сетка конструктора"></a>':window.schoolSections[k](variant);
 };
 selector.onchange=()=>{const u=new URL(location.href);u.searchParams.set('v',selector.value);u.searchParams.set('screen',key());location.assign(u.href);};
 const menu=document.querySelector('.mobile-nav');menu.onclick=()=>menu.setAttribute('aria-expanded',String(menu.getAttribute('aria-expanded')!=='true'));
 window.addEventListener('hashchange',()=>{menu.setAttribute('aria-expanded','false');window.schoolDraw();document.querySelector('#work').focus({preventScroll:true});window.scrollTo(0,0);});
 document.querySelector('.skip').onclick=e=>{e.preventDefault();document.querySelector('#work').focus();};
 window.schoolDraw();
})();
