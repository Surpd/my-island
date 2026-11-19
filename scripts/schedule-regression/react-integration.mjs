import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
const ROOT='.local-test/consolidation/results',API='http://127.0.0.1:8041',URL='http://localhost:3041/admin#/admin/schedule/constructor';
const auth=JSON.parse(await fs.readFile('.local-test/consolidation/auth.json','utf8'));
const fixture=JSON.parse(await fs.readFile('.local-test/consolidation/fixture.json','utf8'));
const headers={'Content-Type':'application/json','X-School-Admin-Session':auth.admin_browser_session,'X-Admin-Browser-Session':auth.admin_browser_session};
async function call(path,method='GET',body,extraHeaders={}){const requestHeaders={...headers,...extraHeaders};const r=await fetch(API+path,body===undefined?{method,headers:requestHeaders}:{method,headers:requestHeaders,body:JSON.stringify(body)});const value=await r.json();assert(r.ok,`${method} ${path}: ${r.status} ${JSON.stringify(value)}`);return value;}
for(let attempt=0;attempt<60;attempt++){try{const response=await fetch(URL);if(response.ok)break;}catch{}await new Promise(resolve=>setTimeout(resolve,500));}
const browser=await chromium.launch();
const context=await browser.newContext({viewport:{width:1366,height:768}});
const requests=[],errors=[],blocked=[],shots=[],steps=[];const report={steps,shots,requests,errors,blocked,transport:'Real React + isolated HTTP SQLite backend; no response mocks'};
await context.addInitScript(token=>{sessionStorage.setItem('my_island_admin_session',token);sessionStorage.setItem('my_island_school_admin_session',token);},auth.admin_browser_session);
await context.route('**/*',route=>{const u=new globalThis.URL(route.request().url());if(['data:','blob:'].includes(u.protocol)||(['127.0.0.1','localhost'].includes(u.hostname)&&['8041','3041'].includes(u.port)))return route.continue();blocked.push(u.hostname);return route.abort();});
context.on('response',r=>{if(r.url().startsWith(API+'/api/'))requests.push({method:r.request().method(),path:new globalThis.URL(r.url()).pathname,status:r.status()});});
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
async function shot(name){await page.waitForTimeout(200);await page.screenshot({path:`${ROOT}/screenshots/${name}.png`,fullPage:false});shots.push(name);}
async function open(name){await page.goto(URL);await page.locator('.constructor-start,.constructor-grid-wrap').first().waitFor();if(name==='Elective block full-path check'){await page.getByRole('button',{name:/Недели/}).click();const row=page.locator('.constructor-week-row').filter({hasText:/2 ноября.*8 ноября 2026/});await row.getByRole('button',{name:'Открыть черновик',exact:true}).click();}else{if(await page.locator('.constructor-grid-wrap').count()){await page.locator('.constructor-draft-menu > summary').click();await page.getByRole('button',{name:/К шаблону и неделям|Все черновики/}).click();}const card=page.locator('.constructor-draft').filter({has:page.getByText(name,{exact:true})}).first();if(await card.count()&&!await card.isVisible()){const section=card.locator('xpath=ancestor::details[1]');if(await section.count())await section.locator('summary').click();}await card.locator('.constructor-draft-open').click();}await page.locator('.constructor-grid-wrap').waitFor();}
async function publication(scope){await page.getByRole('button',{name:'Открыть публикацию расписания'}).click();await page.getByRole('button',{name:'Проверить изменения',exact:true}).click();await page.getByLabel('Проверка публикации',{exact:true}).waitFor();await shot(`publication-${scope}-preview-1366`);await page.locator('.constructor-publication-confirm input').check();await page.getByRole('button',{name:scope==='week'?/Опубликовать.*неделю/:/Опубликовать шаблон/}).click();await page.getByText(/Опубликовано.*неделя с/).waitFor();await shot(`publication-${scope}-success-1366`);await page.getByRole('button',{name:'К конструктору',exact:true}).click();}
try{
 await fs.mkdir(`${ROOT}/screenshots`,{recursive:true});
 await open('Elective block full-path check');
 await page.getByRole('button',{name:'Таблица недели',exact:true}).click();
 const bulk=page.locator('.constructor-elective-bulk-map');await bulk.locator('summary').click();
 for(let lane=1;lane<=4;lane++)await page.getByRole('checkbox',{name:new RegExp(`Course choice lane ${lane}`)}).check();
 await page.getByLabel('Название общего блока для сопоставления').fill('Курс по выбору');
 await bulk.getByRole('button',{name:'Предпросмотр · 4 направлений',exact:true}).click();
 const choicePreview=page.getByLabel('Предпросмотр сопоставления курсов');await choicePreview.waitFor();
 assert((await choicePreview.innerText()).includes('5 класс'));assert((await choicePreview.innerText()).includes('6 класс'));
 await choicePreview.getByRole('button',{name:'Подтвердить сопоставление выбранных направлений',exact:true}).click();
 await bulk.getByRole('button',{name:'Отметить направления с цифровым признаком · 4',exact:true}).click();
 await bulk.getByRole('button',{name:'Предпросмотр · 4 направлений',exact:true}).click();
 const digitalPreview=page.getByLabel('Предпросмотр сопоставления курсов');await digitalPreview.waitFor();
 for(const value of ['F54 · Programming','G54 · Graphics','F55 · Media','ШУМ · Медиамастерская','7-А synthetic','7-Б synthetic','8-А synthetic'])assert((await digitalPreview.innerText()).includes(value),value);
 await digitalPreview.getByRole('button',{name:'Подтвердить сопоставление выбранных направлений',exact:true}).click();
 await page.getByRole('button',{name:'Закрыть таблицу недели',exact:true}).click();
 const electiveId=fixture.elective_workflow_draft_id;
 await Promise.all([page.waitForResponse(r=>r.url().endsWith(electiveId)&&r.request().method()==='PUT'&&r.status()===200),page.getByRole('button',{name:'Сохранить черновик',exact:true}).first().click()]);
 const mapped=await call('/api/admin/schedule/experiments/school/'+electiveId);
 const choiceLanes=mapped.payload.lessons.filter(x=>x.id.startsWith('e2e-choice-'));
 const digitalLanes=mapped.payload.lessons.filter(x=>x.id.startsWith('e2e-digital-'));
 assert.equal(new Set(choiceLanes.map(x=>x.blockId)).size,1);assert.equal(new Set(digitalLanes.map(x=>x.blockId)).size,1);
 assert.notEqual(choiceLanes[0].blockId,digitalLanes[0].blockId);assert(digitalLanes.every(x=>x.blockName==='Цифровой трек'));
 steps.push('Admin UI bulk mapping preview → one confirmation per block → saved common IDs read back from isolated SQLite');
 const week=fixture.elective_workflow_week;
 const publishPath=`/api/admin/schedule/constructor/${electiveId}/publication`;
 const preview=await call(publishPath+'/preview','POST',{scope_kind:'week',week_start:week,expected_revision:mapped.revision});
 assert(preview.can_publish,JSON.stringify(preview.blockers));
 const diagnosticCodes=(preview.diagnostics||[]).map(x=>x.code);
 assert(diagnosticCodes.includes('teacher_overlap'));assert(diagnosticCodes.includes('room_overlap'));
 assert.equal(preview.summary.clubs_hidden,1);
 await shot('elective-blocks-preview-1366');
 await publication('week');
 const adminHeaders={'X-Dev-Auth':'admin:1'};
 async function campus(role,identityId){
   const started=await call('/api/admin/previews','POST',{target_identity_id:identityId,role},adminHeaders);
   const authRole=role==='student'?'student':'teacher';
   return call(`/api/${authRole}/schedule?start_day=${week}&end_day=${week}`,'GET',undefined,{'X-Dev-Auth':'admin:1','X-Preview-Id':started.id});
 }
 for(const label of ['5-А synthetic','6-А synthetic']){
   const body=await campus('student',fixture.students[label][0]);
   const cards=body.items.filter(x=>x.activity_type==='elective');assert.equal(cards.length,1,label);
   assert.equal(cards[0].activity,'Курс по выбору');assert.equal(cards[0].start_time,'09:00');
   assert(!JSON.stringify(cards[0]).includes('Course choice lane'));
 }
 for(const label of ['7-А synthetic','7-Б synthetic','8-А synthetic']){
   const body=await campus('student',fixture.students[label][0]);
   const cards=body.items.filter(x=>x.activity_type==='elective');assert.equal(cards.length,2,label);
   assert(cards.every(x=>x.activity==='Цифровой трек'));
   assert.deepEqual(cards.map(x=>x.start_time).sort(),['09:55','10:55']);
   assert(!JSON.stringify(cards).includes('Медиамастерская'));assert(!JSON.stringify(cards).includes('F54'));
   assert(!body.items.some(x=>String(x.activity||'').includes('Theatre club')));assert.deepEqual(body.optional_activities,[]);
 }
 for(let lane=0;lane<4;lane++){
   const subject=['F54 · Programming','G54 · Graphics','F55 · Media','ШУМ · Медиамастерская'][lane];
   const teacherId=fixture.teachers[4+lane];const body=await campus('teacher',teacherId);
   const items=body.items.filter(x=>x.activity===subject);assert.equal(items.length,1,subject);
   assert(body.items.every(x=>!x.activity.startsWith('F54 · Programming')||teacherId===fixture.teachers[4]));
 }
 steps.push('Published isolated week → Student Campus: 5–6 one neutral block; 7–8 one digital card per slot; clubs absent; Teacher Campus retains each teacher lane');
 steps.push('Preview retains actual teacher and room collision diagnostics');
 await page.getByRole('button',{name:'Таблица недели',exact:true}).click();await page.screenshot({path:`${ROOT}/screenshots/elective-blocks-admin-table-1366.png`,fullPage:false});
 report.draftId=electiveId;report.publishedWeek=week;
 assert.deepEqual(errors,[]);report.status='PASS';
}catch(error){report.status='FAIL';report.failure=String(error.stack||error);await shot('failure');throw error;}finally{await fs.writeFile(`${ROOT}/react-integration-report.json`,JSON.stringify(report,null,2));await context.close();await browser.close();}
