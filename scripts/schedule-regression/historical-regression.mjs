import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {directory,payload,draft} from './historical-fixture.mjs';
const auth=JSON.parse(await fs.readFile('.local-test/consolidation/auth.json','utf8'));
const browser=await chromium.launch();const results=[];const mutations=[];const external=[];
const days=['Понедельник','Вторник','Среда','Четверг','Пятница'];
async function setup({schoolError=false,previewStatus='incomplete'}={}){
 const context=await browser.newContext({viewport:{width:1920,height:1080}});await context.addInitScript(token=>{sessionStorage.setItem('my_island_admin_session',token);sessionStorage.setItem('my_island_school_admin_session',token);},auth.admin_browser_session);const page=await context.newPage();
 await context.route('**/*',async route=>{const req=route.request(),u=new URL(req.url()),p=u.pathname;let value;
  if(u.protocol.startsWith('http')&&!['127.0.0.1','localhost'].includes(u.hostname)){external.push(u.hostname);return route.abort();}
  if(p.endsWith('/experiments/capabilities'))value={demo_available:true,school_available:true};
  else if(p.endsWith('/experiments/school/directory')){if(schoolError)return route.fulfill({status:503,json:{detail:'Audit directory unavailable'}});value=directory;}
  else if(p.match(/\/experiments(?:\/school)?$/))value={items:[{...draft,payload:undefined,draft_kind:'user',lesson_count:386,event_count:0,day_count:5,group_count:47}]};
  else if(p.match(/\/experiments(?:\/school)?\/audit-full-template$/)&&req.method()==='GET')value=draft;
  else if(p.endsWith('/groups'))value={items:directory.groups};
  else if(p.endsWith('/people'))value={items:u.searchParams.get('kind')==='teacher'?directory.teachers:[]};
  else if(p.endsWith('/activities'))value={items:[]};
  else if(p.endsWith('/observability'))value={blocks:[]};
  else if(p.endsWith('/audience-preview')){const ls=req.postDataJSON().payload?.lessons||[];value={lessons:Object.fromEntries(ls.map(l=>[l.id,{status:previewStatus,reason:'Изолированная проверка preview',class_ids:l.classIds||[],...(previewStatus==='ambiguous'?{needs_recalculation:true,student_ids:['old-student'],proposed_student_ids:['new-student'],blockers:[{lesson_id:payload.lessons[0].id,subject:payload.lessons[0].subject,reason:'Аудит blocker'}]}:{})}]))};}
  if(value!==undefined)return route.fulfill({json:value});
  if(!['GET','OPTIONS'].includes(req.method())){mutations.push({method:req.method(),path:p});return route.fulfill({status:403,json:{detail:'Stage3A: mutation blocked'}});}
  return route.continue();
 });
 page.on('dialog',d=>d.dismiss());await page.goto('http://localhost:3041/admin#/admin/schedule/constructor');await page.getByRole('button',{name:/АУДИТ · копия школьного шаблона/}).waitFor();
 return {context,page};
}
async function open(page){await page.getByRole('button',{name:/АУДИТ · копия школьного шаблона/}).click();await page.locator('.constructor-card').first().waitFor();await page.waitForTimeout(800);}
async function test(name,fn,options){if(process.env.TEST_MATCH&&!name.includes(process.env.TEST_MATCH))return;let env;try{env=await setup(options);await fn(env.page);if(name.startsWith('T03/')){await env.page.getByLabel('Объект недельного просмотра').selectOption('');await env.page.getByRole('button',{name:'День',exact:true}).click();await env.page.getByRole('button',{name:'Понедельник',exact:true}).click();for(const [width,height]of [[1920,1080],[1366,768],[390,844]]){await env.page.setViewportSize({width,height});await env.page.evaluate(()=>window.scrollTo(0,0));await env.page.screenshot({path:`.local-test/consolidation/results/screenshots/historical-386-${width}.png`});}}results.push({name,status:'passed'});console.log('PASS',name);}catch(e){results.push({name,status:'failed',error:e.message});console.log('FAIL',name,e.message);}finally{await env?.context.close();}}
await test('T01 — demo/school workspaces and directory separation',async p=>{await p.getByRole('button',{name:/^Школа/}).click();assert.match(await p.locator('.constructor-start').innerText(),/Школьные черновики/);await p.getByRole('button',{name:/^Демо/}).click();await open(p);assert.equal(await p.locator('.constructor-head').count(),7);});
await test('T01 — directory error disables school and retains demo',async p=>{assert.equal(await p.getByRole('button',{name:/^Школа/}).isDisabled(),true);assert.match(await p.locator('.constructor-status').innerText(),/временно недоступен/);await open(p);},{schoolError:true});
await test('T03/T05 — full template 386 IDs / 427 projections; day/week and targets',async p=>{await open(p);const unique=new Set();let projections=0;for(const day of days){await p.getByRole('button',{name:day,exact:true}).click();const ids=await p.locator('[data-lesson-id]').evaluateAll(es=>es.map(e=>e.dataset.lessonId));ids.forEach(id=>unique.add(id));projections+=ids.length;}assert.equal(unique.size,386);assert.equal(projections,427);await p.getByRole('button',{name:'Неделя',exact:true}).click();assert.equal(await p.locator('[data-lesson-id]').count(),427);await p.getByRole('button',{name:'Фильтры',exact:true}).click();const t=p.getByLabel('Объект недельного просмотра');const os=await t.locator('option').evaluateAll(es=>es.map(e=>e.value));for(const prefix of ['parallel:','group:','teacher:'])assert(os.some(v=>v.startsWith(prefix)));assert(os.includes(payload.classes[0]));await t.selectOption('teacher:'+directory.teachers[0].id);const ids=await p.locator('[data-lesson-id]').evaluateAll(es=>es.map(e=>e.dataset.lessonId));assert(ids.length>0);assert(ids.every(id=>{const l=payload.lessons.find(x=>x.id===id);return l.teacherId===directory.teachers[0].id||l.teacherIds?.includes(directory.teachers[0].id);}));});
await test('T04 — selection, quick focus, range keys, edit undo/redo; zero saves',async p=>{await open(p);await p.locator('[data-cell]').first().click({position:{x:2,y:2}});await p.locator('.constructor-cell-entry').waitFor();assert.equal(await p.locator('.constructor-cell-entry').evaluate(e=>document.activeElement===e),true);await p.locator('[data-cell]').first().focus();await p.keyboard.press('Shift+ArrowRight');assert(await p.locator('.constructor-cell.selected').count()>=2);await p.locator('.constructor-card').first().click();const input=p.locator('.constructor-inspector label').filter({hasText:'Предмет'}).locator('input');const old=await input.inputValue();await input.fill('Аудит локальная правка');await p.getByRole('button',{name:'Отменить',exact:true}).click();assert.equal(await input.inputValue(),old);await p.getByRole('button',{name:'Повторить',exact:true}).click();assert.equal(await input.inputValue(),'Аудит локальная правка');await p.getByRole('button',{name:'Закрыть панель',exact:true}).click();const vm=p.locator('details.constructor-view-menu');await vm.locator('summary').click();await p.getByLabel('Масштаб', {exact:true}).selectOption('90');assert.equal(await p.getByLabel('Масштаб',{exact:true}).inputValue(),'90');await p.getByRole('button',{name:'Полный экран'}).click();assert(await p.locator('.constructor.is-fullscreen').count());await p.getByRole('button',{name:'Выйти из полного экрана'}).click();});
await test('T06 — filters/search/color mode preserve teacher fill',async p=>{await open(p);await p.getByRole('button',{name:'Фильтры',exact:true}).click();const card=p.locator('.constructor-card').first();const id=await card.getAttribute('data-lesson-id');const fill=await card.evaluate(e=>e.style.backgroundColor);const l=payload.lessons.find(x=>x.id===id);await p.getByLabel('Фильтр по учителю').selectOption(l.teacherId);assert(await p.locator('.constructor-card').count()>0);await p.getByLabel('Фильтр по учителю').selectOption('');assert.equal(await p.locator(`[data-lesson-id="${id}"]`).first().evaluate(e=>e.style.backgroundColor),fill);await p.getByLabel('Найти занятие').fill(l.subject);assert(await p.locator('.constructor-card').count()>0);await p.getByLabel('Найти занятие').fill('');await p.getByLabel('Режим цвета').selectOption('none');assert.equal(await p.locator(`[data-lesson-id="${id}"]`).first().evaluate(e=>e.style.backgroundColor),'');await p.getByLabel('Режим цвета').selectOption('teacher');assert.equal(await p.locator(`[data-lesson-id="${id}"]`).first().evaluate(e=>e.style.backgroundColor),fill);});
await test('T10 — all creation modes/activity modes and existing inspector fields',async p=>{await open(p);await p.locator('[data-cell]').first().click({position:{x:2,y:2}});for(const label of ['Быстрый ввод','Форма','Событие','Обед','Нет урока']){await p.getByRole('group',{name:'Способ создания',exact:true}).getByRole('button',{name:label,exact:true}).click();assert(await p.locator('.constructor-inspector').isVisible());}for(const label of ['Урок','Кружок','Курс по выбору','Событие']){await p.getByRole('group',{name:'Тип активности',exact:true}).getByRole('button',{name:label,exact:true}).click();}await p.locator('.constructor-card').first().click();for(const label of ['Найти преподавателя','Преподаватель','Цвет преподавателя','Кабинет','День','Время'])assert(await p.locator('.constructor-inspector label').filter({hasText:label}).count()>0);assert(await p.getByRole('button',{name:'Удалить занятие',exact:true}).count());assert.equal(await p.getByRole('region',{name:'Участники занятия'}).getByRole('button',{pressed:false}).count()+await p.getByRole('region',{name:'Участники занятия'}).getByRole('button',{pressed:true}).count(),4);});
await test('T11 — ambiguous preview, saved snapshot warning and diagnostic jumps',async p=>{await open(p);await p.locator('.constructor-card').first().click();const region=p.getByRole('region',{name:'Участники занятия'});assert.match(await region.innerText(),/Изменился расчёт участников/);assert(await region.getByRole('button',{name:'Пересчитать и подтвердить состав'}).count());assert(await region.getByRole('button',{name:/Аудит blocker/}).count());await p.getByRole('button',{name:/Диагностика/}).click();assert(await p.getByLabel('Важность проблемы').count());assert(await p.getByLabel('Тип проблемы').count());assert(await p.getByRole('button',{name:'Показать в расписании',exact:true}).count()>0);},{previewStatus:'ambiguous'});
await test('T15 — 1920/1366/390 controls and inspector remain reachable',async p=>{await open(p);for(const [width,height]of [[1920,1080],[1366,768],[390,844]]){await p.setViewportSize({width,height});assert.equal(await p.locator('.constructor-command-bar').count(),1);if(width===390)assert.equal(await p.getByRole('button',{name:'Сохранить черновик',exact:true}).count(),1);assert.equal(await p.getByRole('button',{name:'День',exact:true}).count(),1);assert.equal(await p.getByRole('button',{name:'Неделя',exact:true}).count(),1);await p.locator('details.constructor-draft-menu summary').click();assert(await p.getByRole('button',{name:'Экспортировать в Google Sheets',exact:true}).count());await p.locator('.constructor-card').first().click();await p.locator('.constructor-inspector').scrollIntoViewIfNeeded();assert(await p.locator('.constructor-inspector').isVisible());await p.getByRole('button',{name:'Закрыть панель',exact:true}).click();const metrics=await p.locator('.constructor-grid-wrap').evaluate(e=>({w:e.clientWidth,scroll:e.scrollWidth}));assert(metrics.w>0&&metrics.scroll>=metrics.w);}const font=await p.locator('.constructor').evaluate(e=>getComputedStyle(e).fontFamily);assert.match(font,/Campus Golos/);});
await test('T04 — copy/paste, undo and drag preserve identity locally',async p=>{
 await open(p);const card=p.locator('.constructor-card').first();const id=await card.getAttribute('data-lesson-id');const initial=await p.locator('.constructor-card').count();
 await card.click();await p.locator('.constructor-edit-tools').getByRole('button',{name:'Копировать',exact:true}).click();
 await p.locator('[data-cell="1:0"]').click({position:{x:2,y:2}});await p.locator('.constructor-edit-tools').getByRole('button',{name:'Вставить',exact:true}).click();assert.equal(await p.locator('.constructor-card').count(),initial+1);
 await p.getByRole('button',{name:'Отменить',exact:true}).click();assert.equal(await p.locator('.constructor-card').count(),initial);
 await p.getByRole('button',{name:'Закрыть панель',exact:true}).click();
 await p.locator(`[data-lesson-id="${id}"]`).first().dragTo(p.locator('[data-cell="2:0"]'),{targetPosition:{x:4,y:4}});
 assert.equal(await p.locator(`[data-cell="2:0"] [data-lesson-id="${id}"]`).count(),1);assert.equal(await p.locator(`[data-cell="0:0"] [data-lesson-id="${id}"]`).count(),0);
 await p.getByRole('button',{name:'Отменить',exact:true}).click();assert.equal(await p.locator(`[data-cell="0:0"] [data-lesson-id="${id}"]`).count(),1);
});
await test('T11 — preview error keeps retry reachable',async p=>{await open(p);await p.locator('.constructor-card').first().click();const retry=p.getByRole('region',{name:'Участники занятия'}).getByRole('button',{name:'Повторить расчёт',exact:true});assert(await retry.count());await retry.click();await p.waitForTimeout(850);assert(await retry.count());},{previewStatus:'error'});
await test('T03 — class/parallel/group targets retain original column structure',async p=>{
 await open(p);await p.getByRole('button',{name:'Неделя',exact:true}).click();await p.getByRole('button',{name:'Фильтры',exact:true}).click();const target=p.getByLabel('Объект недельного просмотра');const columns=await p.locator('.constructor-head').allTextContents();
 const groupLesson=payload.lessons.find(l=>l.groupId&&!payload.classes.includes(l.groupId));
 for(const value of [payload.classes[0],'parallel:5','group:'+groupLesson.groupId]){await target.selectOption(value);assert.deepEqual(await p.locator('.constructor-head').allTextContents(),columns);assert(await p.locator('.constructor-card').count()>0);}
 await target.selectOption('');assert.equal(await p.locator('.constructor-card').count(),427);
});
await test('T15 — command bar, native view/draft menus and compact mobile labels',async p=>{
 await open(p);
 const bar=p.locator('.constructor-command-bar');assert.equal(await bar.count(),1);
 const view=p.locator('details.constructor-view-menu'),draftMenu=p.locator('details.constructor-draft-menu');
 assert.equal(await view.count(),1);assert.equal(await draftMenu.count(),1);
 await view.locator('summary').click();assert(await view.evaluate(e=>e.open));
 for(const label of ['Плотность сетки','Масштаб'])assert(await view.getByLabel(label).count()>0,label);
 for(const label of ['Полный экран','Каталог кружков / курсов'])assert(await view.getByRole('button',{name:new RegExp(label)}).count()>0,label);
 await draftMenu.locator('summary').click();assert(await draftMenu.evaluate(e=>e.open));
 for(const label of ['Черновик','Все черновики','Экспортировать в Google Sheets'])assert(await draftMenu.getByText(label,{exact:false}).count()>0,label);
 assert.equal(await draftMenu.getByLabel('Название черновика').count(),1);
 await p.setViewportSize({width:390,height:844});
 assert.equal((await view.locator('summary').innerText()).trim(),'Ещё');
 await view.locator('summary').click();assert(await view.evaluate(e=>e.open));
});
await test('T15 — mobile inspector dialog traps focus and restores scroll on close',async p=>{
 await open(p);await p.setViewportSize({width:390,height:844});
 await p.locator('.constructor-card').first().click();
 const dialog=p.locator('aside.constructor-inspector[role="dialog"][aria-label="Редактор занятия"]');
 assert.equal(await dialog.count(),1);assert(await dialog.isVisible());
 assert(await p.locator('body').evaluate(e=>e.classList.contains('constructor-editor-open')));
 await p.keyboard.press('Shift+Tab');assert(await dialog.evaluate(e=>e.contains(document.activeElement)));
 await p.keyboard.press('Escape');assert.equal(await dialog.isVisible().catch(()=>false),false);
 assert.equal(await p.locator('body').evaluate(e=>e.classList.contains('constructor-editor-open')),false);
});
await test('T15 — desktop inspector remains a labelled aside',async p=>{
 await open(p);await p.locator('.constructor-card').first().click();
 const panel=p.locator('aside.constructor-inspector[aria-label="Редактор занятия"]');
 assert.equal(await panel.count(),1);assert(await panel.isVisible());
});
assert.equal(mutations.length,0,'No schedule mutation may reach even the blocking fallback');
await fs.writeFile('.local-test/consolidation/results/'+(process.env.TEST_MATCH?'targeted-results.json':'regression-results.json'),JSON.stringify({scope:'Historical display/interaction regression with intercepted fixtures; not memberships or backend proof',results,mutations,blockedExternalHosts:[...new Set(external)]},null,2));await browser.close();if(results.some(r=>r.status!=='passed'))process.exitCode=1;
