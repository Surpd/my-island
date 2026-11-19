import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
const ROOT='.local-test/consolidation/results',API='http://127.0.0.1:8041',URL='http://localhost:3041/admin#/admin/schedule/constructor';
const auth=JSON.parse(await fs.readFile('.local-test/consolidation/auth.json','utf8'));
const fixture=JSON.parse(await fs.readFile('.local-test/consolidation/fixture.json','utf8'));
assert.equal(auth.base_url, API);
assert.equal(auth.synthetic && fixture.synthetic, true);
const headers={'Content-Type':'application/json','X-School-Admin-Session':auth.admin_browser_session,'X-Admin-Browser-Session':auth.admin_browser_session};
async function call(path,method='GET',body,extraHeaders={}){const requestHeaders={...headers,...extraHeaders};const r=await fetch(API+path,body===undefined?{method,headers:requestHeaders}:{method,headers:requestHeaders,body:JSON.stringify(body)});const value=await r.json();assert(r.ok,`${method} ${path}: ${r.status} ${JSON.stringify(value)}`);return value;}
for(let attempt=0;attempt<60;attempt++){try{const response=await fetch(URL);if(response.ok)break;}catch{}await new Promise(resolve=>setTimeout(resolve,500));}
const browser=await chromium.launch({ executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH });
const requests=[],errors=[],blocked=[],shots=[],steps=[];const report={steps,shots,requests,errors,blocked,transport:'Real React + isolated HTTP SQLite backend; no response mocks'};
async function isolatedContext(width, height) {
  const isolated = await browser.newContext({ viewport: { width, height } });
  await isolated.addInitScript(token => {
    sessionStorage.setItem('my_island_admin_session', token);
    sessionStorage.setItem('my_island_school_admin_session', token);
  }, auth.admin_browser_session);
  await isolated.route('**/*', route => {
    const url = new globalThis.URL(route.request().url());
    if (['data:', 'blob:'].includes(url.protocol) || (['127.0.0.1', 'localhost'].includes(url.hostname) && ['8041', '3041'].includes(url.port))) return route.continue();
    blocked.push(url.hostname);
    return route.abort();
  });
  return isolated;
}
const context = await isolatedContext(1366, 768);
context.on('response',r=>{if(r.url().startsWith(API+'/api/'))requests.push({method:r.request().method(),path:new globalThis.URL(r.url()).pathname,status:r.status()});});
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
page.setDefaultTimeout(15_000);
async function shot(name){await page.waitForTimeout(200);await page.screenshot({path:`${ROOT}/screenshots/${name}.png`,fullPage:false});shots.push(name);}
async function open(name){await page.goto(URL);await page.locator('.constructor-start,.constructor-grid-wrap').first().waitFor();if(await page.locator('.constructor-grid-wrap').count()){await page.locator('.constructor-draft-menu > summary').click();await page.getByRole('button',{name:/К шаблону и неделям|Все черновики/}).click();}if(name==='Elective block full-path check'){await page.getByRole('button',{name:/Недели/}).click();const row=page.locator('.constructor-week-row').filter({hasText:/2 ноября.*8 ноября 2026/});await row.getByRole('button',{name:'Открыть черновик',exact:true}).click();}else{const card=page.locator('.constructor-draft').filter({has:page.getByText(name,{exact:true})}).first();if(await card.count()&&!await card.isVisible()){const section=card.locator('xpath=ancestor::details[1]');if(await section.count())await section.locator('summary').click();}await card.locator('.constructor-draft-open').click();}await page.locator('.constructor-grid-wrap').waitFor();}
async function publication(draftId) {
  const reviewStarted = performance.now();
  await page.getByRole('button', { name: 'Проверить расписание', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Проверка расписания' });
  await dialog.getByRole('heading', { name: 'Проверка перед публикацией' }).waitFor();
  report.previewVisibleMs = Math.round(performance.now() - reviewStarted);
  await dialog.getByText('Что изменится для школы', { exact: true }).click();
  await dialog.getByText('Сравнить с опубликованной версией', { exact: true }).click();
  await dialog.getByRole('searchbox', { name: 'Найти в замечаниях' }).fill('Required lesson 5');
  await dialog.locator('.constructor-publication-diagnostics li').filter({ hasText: 'Required lesson 5' }).first().waitFor();
  await dialog.getByRole('searchbox', { name: 'Найти в замечаниях' }).clear();
  const confirm = dialog.getByRole('checkbox', { name: /Подтверждаю публикацию/ });
  const publish = dialog.getByRole('button', { name: /Опубликовать.*неделю/ });
  assert(await publish.isDisabled(), 'publication requires separate confirmation');
  await dialog.getByText('Что изменится для школы', { exact: true }).click();
  await shot('school-review-1366');
  for (const [width, height] of [[390, 844], [320, 760]]) {
    const mobile = await isolatedContext(width, height);
    try {
      const mobilePage = await mobile.newPage();
      await mobilePage.goto(`http://localhost:3041/admin/schedule/constructor?draft=${draftId}`);
      await mobilePage.getByRole('button', { name: 'Проверить расписание', exact: true }).click();
      const mobileReview = mobilePage.getByRole('dialog', { name: 'Проверка расписания' });
      await mobileReview.getByRole('heading', { name: 'Проверка перед публикацией' }).waitFor();
      const box = await mobileReview.boundingBox();
      assert(box && box.x >= 0 && box.x + box.width <= width + 1 && box.y >= 0 && box.y + box.height <= height + 1);
      const documentWidth = await mobilePage.evaluate(() => document.documentElement.scrollWidth);
      assert(documentWidth <= width + 1, `review overflows page at ${width}px: ${documentWidth}px`);
      await mobilePage.screenshot({ path: `${ROOT}/screenshots/school-review-${width}.png` });
      shots.push(`school-review-${width}`);
    } finally {
      await mobile.close();
    }
  }
  await confirm.check();
  const [publishedResponse] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/publication/publish')),
    publish.click(),
  ]);
  assert(publishedResponse.ok(), await publishedResponse.text());
  assert.equal((await publishedResponse.json()).runtime_readback.verified, true);
  await dialog.getByText(/Проверка после публикации пройдена/).waitFor();
  await shot('publication-week-success-1366');
  await dialog.getByRole('button', { name: 'К конструктору', exact: true }).click();
}

async function campusPreview(role, identityId, day, expectedSubject, forbiddenSubject, screenshot) {
  const query = new URLSearchParams({ kind: role, identity_id: identityId, day });
  await page.goto(`http://localhost:3041/admin/preview?${query}`);
  const iframe = page.getByTitle('Предпросмотр My Island');
  const campus = page.frameLocator('iframe[title="Предпросмотр My Island"]');
  await campus.getByRole('button', { name: 'Расписание', exact: true }).first().click();
  await campus.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  const subjectPattern = new RegExp(`^${expectedSubject.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`);
  await campus.locator('strong:visible').filter({ hasText: subjectPattern }).first().waitFor();
  assert.equal(await campus.getByText(forbiddenSubject, { exact: true }).count(), 0);
  if (screenshot) await iframe.screenshot({ path: `${ROOT}/screenshots/${screenshot}.png` });
  await page.getByRole('button', { name: 'Завершить предпросмотр', exact: true }).click();
}
try{
 await fs.mkdir(`${ROOT}/screenshots`,{recursive:true});
 const editable = await call(`/api/admin/schedule/experiments/school/${fixture.elective_workflow_draft_id}`);
 const classId = fixture.classes['7-А synthetic'];
 const groupId = fixture.math_groups['7-А synthetic:A'];
 editable.payload.lessons = editable.payload.lessons.filter(lesson => lesson.id !== 'e2e-ordinary-draft');
 editable.payload.lessons.push({ id: 'e2e-ordinary-draft', day: 0, period: 3, subject: 'Учебный практикум',
   classId, classIds: [classId], teacherId: fixture.teachers[10], room: '15',
   audience: { kind: 'classes', class_ids: [classId] } });
 await call(`/api/admin/schedule/experiments/school/${editable.id}`, 'PUT', {
   expected_revision: editable.revision, payload: editable.payload, name: editable.name,
 });
 await page.goto(`http://localhost:3041/admin/schedule/constructor?draft=${editable.id}&lesson=e2e-ordinary-draft`);
 await page.locator('.constructor-inspector').getByRole('combobox', { name: 'Предмет', exact: true }).waitFor();
 assert.equal(await page.locator('.constructor-inspector').getByRole('combobox', { name: 'Предмет', exact: true }).inputValue(), 'Учебный практикум');
 await open('Elective block full-path check');
 await shot('school-constructor-1366');
 await page.locator('.constructor-card[data-lesson-id="e2e-ordinary-draft"]').first().dblclick();
 const inspector = page.locator('.constructor-inspector');
 await inspector.getByRole('combobox', { name: 'Предмет', exact: true }).fill('Практикум QA');
 await inspector.getByRole('combobox', { name: 'Преподаватель', exact: true }).selectOption(fixture.teachers[9]);
 const picker = inspector.getByRole('region', { name: 'Участники занятия' });
 await picker.getByRole('button', { name: /Учебные группы/ }).click();
 await picker.locator(`input[type="checkbox"][value="${groupId}"]`).check();
 await page.getByRole('button', { name: 'Закрыть панель', exact: true }).click();
 await page.getByRole('button', { name: 'Проверить расписание', exact: true }).click();
 const dirtyReview = page.getByRole('dialog', { name: 'Проверка расписания' });
 await dirtyReview.getByText(/несохранённые правки/).waitFor();
 const [savedResponse] = await Promise.all([
   page.waitForResponse(response => response.url().endsWith(editable.id) && response.request().method() === 'PUT'),
   dirtyReview.getByRole('button', { name: 'Сохранить и проверить', exact: true }).click(),
 ]);
 assert(savedResponse.ok(), await savedResponse.text());
 const savedOrdinary = (await savedResponse.json()).payload.lessons.find(lesson => lesson.id === 'e2e-ordinary-draft');
 assert.equal(savedOrdinary.subject, 'Практикум QA');
 assert.equal(String(savedOrdinary.teacherId), String(fixture.teachers[9]));
 assert.deepEqual(savedOrdinary.audience, { kind: 'groups', group_ids: [groupId] });
 await dirtyReview.getByRole('button', { name: 'Закрыть проверку' }).click();
 steps.push('Edited subject, teacher and group; explicitly saved dirty draft before checking without publishing');
 const devAdmin = { 'X-Dev-Auth': 'admin:1' };
 const newMember = await call('/api/admin/identities', 'POST', {
   display_name: `Synthetic new study-group member ${Date.now()}`, kind: 'student', class_name: '7-А synthetic',
 }, devAdmin);
 for (const rosterId of [classId, groupId]) {
   await call('/api/admin/memberships', 'POST', { group_id: rosterId, identity_id: String(newMember.id) }, devAdmin);
 }
 await page.getByRole('button', { name: 'Проверить расписание', exact: true }).click();
 await dirtyReview.getByText(/^Безопасные исправления/).click();
 const updateRosters = dirtyReview.getByRole('button', { name: 'Подтвердить проверенные составы', exact: true });
 const [rosterResponse] = await Promise.all([
   page.waitForResponse(response => response.url().endsWith('/audiences/apply')),
   updateRosters.click(),
 ]);
 assert(rosterResponse.ok(), await rosterResponse.text());
 const rosterResult = await rosterResponse.json();
 const confirmedDraft = rosterResult.draft ?? rosterResult;
 const confirmedRoster = confirmedDraft.payload.lessons.find(lesson => lesson.id === 'e2e-ordinary-draft').audience_snapshot.student_ids;
 assert(confirmedRoster.map(String).includes(String(newMember.id)));
 await dirtyReview.getByText(/Составы обновлены для/).waitFor();
 await shot('school-review-rosters-1366');
 await dirtyReview.getByRole('button', { name: 'Закрыть проверку' }).click();
 steps.push('Review detected a stale roster after an isolated directory change and applied the named safe update');
 await page.getByRole('button',{name:'Таблица недели',exact:true}).click();
 const bulk=page.locator('.constructor-elective-bulk-map');await bulk.locator('summary').click();
 for(let lane=1;lane<=4;lane++)await page.getByRole('checkbox',{name:new RegExp(`Course choice lane ${lane}`)}).check();
 await page.getByLabel('Целевой общий блок').selectOption('legacy-choice-5-6');
 await bulk.getByRole('button',{name:'Предпросмотр · 4 направлений',exact:true}).click();
 const choicePreview=page.getByLabel('Предпросмотр сопоставления курсов');await choicePreview.waitFor();
 assert((await choicePreview.innerText()).includes('5 класс'));assert((await choicePreview.innerText()).includes('6 класс'));
 await choicePreview.getByRole('button',{name:'Подтвердить сопоставление выбранных направлений',exact:true}).click();
 await page.locator('tr').filter({hasText:'Legacy workshop lane'}).getByRole('button',{name:'Открыть',exact:true}).click();
 await page.getByLabel('Название общего блока',{exact:true}).fill('Мастерские');
 await page.getByRole('button', { name: 'Закрыть панель', exact: true }).click();
 await Promise.all([page.waitForResponse(r=>r.url().endsWith(fixture.elective_workflow_draft_id)&&r.request().method()==='PUT'&&r.status()===200),page.getByRole('button',{name:'Сохранить черновик',exact:true}).first().click()]);
 await page.getByRole('button',{name:'Таблица недели',exact:true}).click();
 await page.locator('.constructor-elective-bulk-map').locator('summary').click();
 await bulk.getByRole('button',{name:'Отметить направления с цифровым признаком · 4',exact:true}).click();
 await page.getByLabel('Целевой общий блок').selectOption('__new__');
 await bulk.getByRole('button',{name:'Предпросмотр · 4 направлений',exact:true}).click();
 const digitalPreview=page.getByLabel('Предпросмотр сопоставления курсов');await digitalPreview.waitFor();
 for(const value of ['F54 · Programming','G54 · Graphics','F55 · Media','ШУМ · Медиамастерская','7-А synthetic','7-Б synthetic','8-А synthetic'])assert((await digitalPreview.innerText()).includes(value),value);
 await digitalPreview.getByRole('button',{name:'Подтвердить сопоставление выбранных направлений',exact:true}).click();
 await page.getByRole('button',{name:'Закрыть таблицу недели',exact:true}).click();
 const electiveId=fixture.elective_workflow_draft_id;
 await Promise.all([page.waitForResponse(r=>r.url().endsWith(electiveId)&&r.request().method()==='PUT'&&r.status()===200),page.getByRole('button',{name:'Сохранить черновик',exact:true}).first().click()]);
 if(await page.getByRole('button',{name:'К сетке',exact:true}).count())await page.getByRole('button',{name:'К сетке',exact:true}).click();
 if(await page.getByRole('button',{name:'Закрыть панель',exact:true}).count())await page.getByRole('button',{name:'Закрыть панель',exact:true}).click();
 const mapped=await call('/api/admin/schedule/experiments/school/'+electiveId);
 const choiceLanes=mapped.payload.lessons.filter(x=>x.id.startsWith('e2e-choice-'));
 const legacyLane=mapped.payload.lessons.find(x=>x.id==='e2e-legacy-choice');
 const digitalLanes=mapped.payload.lessons.filter(x=>x.id.startsWith('e2e-digital-'));
 assert.equal(new Set([...choiceLanes,legacyLane].map(x=>x.blockId)).size,1);assert.equal(new Set(digitalLanes.map(x=>x.blockId)).size,1);
 assert.notEqual(choiceLanes[0].blockId,digitalLanes[0].blockId);assert(digitalLanes.every(x=>x.blockName==='Цифровой трек'));
 assert.equal(legacyLane.activityType,'elective');assert.equal(legacyLane.blockName,'Мастерские');
 await open('Elective block full-path check');
 await page.getByRole('button',{name:'Таблица недели',exact:true}).click();
 assert((await page.getByRole('dialog').innerText()).includes('Legacy workshop lane'));
 await page.getByRole('button',{name:'Закрыть таблицу недели',exact:true}).click();
 steps.push('Recognized legacy label → arbitrary block rename → save/re-open keeps explicit type; digital bulk mapping saved in isolated SQLite');
 const week=fixture.elective_workflow_week;
 const publishPath=`/api/admin/schedule/constructor/${electiveId}/publication`;
 const preview=await call(publishPath+'/preview','POST',{scope_kind:'week',week_start:week,expected_revision:mapped.revision});
 assert(preview.can_publish,JSON.stringify(preview.blockers));
 const diagnosticCodes=(preview.diagnostics||[]).map(x=>x.code);
 assert(diagnosticCodes.includes('teacher_overlap'));assert(diagnosticCodes.includes('room_overlap'));
 assert.equal(preview.summary.clubs_hidden,1);
 await shot('elective-blocks-preview-1366');
 await publication(electiveId);
 const adminHeaders={'X-Dev-Auth':'admin:1'};
 async function campus(role,identityId){
   const started=await call('/api/admin/previews','POST',{target_identity_id:identityId,role},adminHeaders);
   const authRole=role==='student'?'student':'teacher';
   return call(`/api/${authRole}/schedule?start_day=${week}&end_day=${week}`,'GET',undefined,{'X-Dev-Auth':'admin:1','X-Preview-Id':started.id});
 }
 for(const label of ['5-А synthetic','6-А synthetic']){
   const body=await campus('student',fixture.students[label][0]);
   const cards=body.items.filter(x=>x.activity_type==='elective');assert.equal(cards.length,1,label);
   assert.equal(cards[0].activity,'Мастерские');assert.equal(cards[0].start_time,'09:00');
   assert(!JSON.stringify(cards[0]).includes('Course choice lane'));assert(!JSON.stringify(cards[0]).includes('Legacy workshop lane'));
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
 const legacyTeacher=fixture.teachers[14];const legacyTeacherCampus=await campus('teacher',legacyTeacher);
 assert.equal(legacyTeacherCampus.items.filter(x=>x.activity==='Legacy workshop lane').length,1);
 const groupStudent = await campus('student', fixture.students['7-А synthetic'][0]);
 const peerStudent = await campus('student', fixture.students['7-А synthetic'][6]);
 assert(groupStudent.items.some(item => item.activity === 'Практикум QA'));
 assert(!peerStudent.items.some(item => item.activity === 'Практикум QA'));
 const reassignedTeacher = await campus('teacher', fixture.teachers[9]);
 assert(reassignedTeacher.items.some(item => item.activity === 'Практикум QA' && item.room === '15'));
 const originalTeacher = await campus('teacher', fixture.teachers[10]);
 assert(!originalTeacher.items.some(item => item.activity === 'Практикум QA'));
 steps.push('Published isolated week → Student Campus: 5–6 one neutral block; 7–8 one digital card per slot; clubs absent; Teacher Campus retains each teacher lane');
 steps.push('Preview retains actual teacher and room collision diagnostics');
 await page.getByRole('button',{name:'Таблица недели',exact:true}).click();await page.screenshot({path:`${ROOT}/screenshots/elective-blocks-admin-table-1366.png`,fullPage:false});
 await campusPreview('student', fixture.students['5-А synthetic'][0], week, 'Мастерские', 'Course choice lane 1', 'student-campus-shared-elective-390');
 await campusPreview('teacher', fixture.teachers[4], week, 'F54 · Programming', 'G54 · Graphics', 'teacher-campus-own-elective-390');
 steps.push('Opened real Student and Teacher Mini App frames at 390px and verified neutral student label and teacher-specific direction');
 report.draftId=electiveId;report.publishedWeek=week;
 assert.deepEqual(errors,[]);report.status='PASS';
}catch(error){report.status='FAIL';report.failure=String(error.stack||error);await shot('failure');throw error;}finally{await fs.writeFile(`${ROOT}/react-integration-report.json`,JSON.stringify(report,null,2));await context.close();await browser.close();}
