// Offline Chromium acceptance against an isolated template. All API reads are local fixtures.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFile, mkdir } from 'node:fs/promises';
import { build } from 'esbuild';
import { chromium } from 'playwright';

const { payload, groups } = JSON.parse(await readFile(process.argv[2], 'utf8'));
const sheets = JSON.parse(
  execFileSync(
    process.env.PYTHON || 'python',
    ['-m', 'scripts.schedule-regression.lane_week_sheet', process.argv[2]],
    { encoding: 'utf8' },
  ),
);
const out = process.argv[3];
await mkdir(out, { recursive: true });
const teachers = [
  ...new Set(
    payload.lessons
      .flatMap((l) => [l.teacherId, ...(l.teacherIds || [])])
      .filter(Boolean),
  ),
].map((id, i) => ({ id, display_name: 'Педагог ' + (i + 1) }));
const bundle = await build({
  stdin: {
    contents: `
 import {createRoot} from 'react-dom/client';
 import {ScheduleConstructor} from './components/schedule-constructor';
 const api=async(path)=> {
   const {payload,groups,teachers}=window.fixture;
   if(path.endsWith('/capabilities')) return {school_available:true,consolidated_schedule:true};
   if(path.endsWith('/directory')) return {groups,teachers,students:[],rooms:[]};
   if(path.endsWith('/archive-copy')) return {id:'archive-copy',name:'II триместр · локальная копия',revision:99,payload};
   return {items:[],blocks:[],weeks:[],lessons:{},exceptions:{}};
 };
 createRoot(document.querySelector('#root')).render(<ScheduleConstructor api={api}/>);
`,
    loader: 'tsx',
    resolveDir: process.cwd(),
  },
  bundle: true,
  write: false,
  format: 'esm',
  outfile: 'app.js',
  plugins: [
    {
      name: 'offline-link',
      setup(b) {
        b.onResolve({ filter: /^next\/link$/ }, () => ({
          namespace: 'fixture',
          path: 'link',
        }));
        b.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({
          contents: "export default 'a'",
        }));
      },
    },
  ],
  jsx: 'automatic',
  define: { 'process.env.NODE_ENV': '"production"' },
});
const css =
  (await readFile('app/globals.css', 'utf8')).replace(
    "@import 'tailwindcss';",
    '',
  ) +
  '\n' +
  (await readFile('app/workspace.css', 'utf8')) +
  '\n' +
  (bundle.outputFiles.find((f) => f.path.endsWith('.css'))?.text || '');
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || '/usr/bin/chromium',
});
try {
  const page = await browser.newPage({
    viewport: { width: 1366, height: 768 },
  });
  page.on('pageerror', (e) => console.error(e.message));
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/')
      return route.fulfill({
        contentType: 'text/html',
        body: '<meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1"><style>*{box-sizing:border-box}body{margin:0}</style><link rel=stylesheet href=/screen.css><main class="campus-shell admin-workspace"><div id=root></div></main><script type=module src=/app.js></script>',
      });
    if (path === '/app.js')
      return route.fulfill({
        contentType: 'text/javascript',
        body: bundle.outputFiles[0].text,
      });
    if (path === '/screen.css')
      return route.fulfill({ contentType: 'text/css', body: css });
    if (/^\/fonts\/[\w.-]+\/[\w.-]+\.ttf$/.test(path))
      return route.fulfill({ path: 'public' + path });
    return route.abort();
  });
  await page.addInitScript(
    (data) => {
      window.fixture = data;
    },
    { payload, groups, teachers },
  );
  await page.goto('http://localhost/?draft=archive-copy');
  const grid = page.locator('.constructor-grid-wrap');
  await grid.waitFor();
  const seen = new Set(),
    dayPlacements = new Map();
  let slots = 0;
  for (const [day, label] of [
    'Понедельник',
    'Вторник',
    'Среда',
    'Четверг',
    'Пятница',
  ].entries()) {
    await page.getByRole('button', { name: label, exact: true }).click();
    const cells = await page.locator('[data-cell]').evaluateAll((elements) =>
      elements.map((el) => ({
        key: el.dataset.cell,
        grade: Number(el.getAttribute('aria-label').match(/^\d+/)[0]),
        columns: getComputedStyle(
          el.querySelector('.constructor-cell-lessons'),
        ).gridTemplateColumns.split(' ').length,
        cards: [...el.querySelectorAll('[data-lesson-id]')].map((card) => {
          const r = card.getBoundingClientRect();
          return {
            id: card.dataset.lessonId,
            col: Number(card.style.gridColumnStart) - 1,
            span: Number(card.style.gridColumnEnd.replace('span ', '')),
            x: r.x,
            right: r.right,
            y: r.y,
          };
        }),
      })),
    );
    for (const { cards, key, grade, columns } of cells) {
      const order = cards.sort((a, b) => a.x - b.x);
      for (const [i, c] of order.entries()) {
        if (i)
          assert.ok(
            c.x >= order[i - 1].right - 1 && Math.abs(c.y - order[i - 1].y) < 2,
            'stack or overlap',
          );
        const match = sheets[[grade, day, key.split(':')[0]].join('/')][c.id];
        assert.ok(match);
        assert.equal(match[0], Math.floor((c.col * match[2]) / columns));
        assert.equal(
          match[1],
          Math.floor(((c.col + c.span) * match[2]) / columns),
        );
        seen.add(c.id);
      }
      dayPlacements.set(
        day + '/' + key,
        order.map((c) => c.id),
      );
      slots++;
    }
    if ([0, 1, 2, 4].includes(day)) {
      for (const [grade, period] of day === 0
        ? [
            [7, 1],
            [9, 3],
            [9, 4],
          ]
        : day === 1
          ? [[9, 3]]
          : day === 2
            ? [[7, 5]]
            : [[9, 4]]) {
        const cell = page
          .locator('[role=gridcell]')
          .filter({ has: page.locator('.constructor-card') });
        const target = cell.and(
          page.getByRole('gridcell', {
            name:
              grade + ' класс, ' + label + ', ' + payload.slots[period].start,
            exact: true,
          }),
        );
        await target.scrollIntoViewIfNeeded();
        await target.screenshot({
          path:
            out +
            '/grade-' +
            grade +
            '-day-' +
            day +
            '-slot-' +
            period +
            '.png',
        });
      }
    }
  }
  assert.equal(slots, 315);
  assert.equal(seen.size, payload.lessons.length);
  await page.getByRole('button', { name: 'Неделя', exact: true }).click();
  const week = await page.locator('[data-cell]').evaluateAll((elements) =>
    elements.map((el) => ({
      key: el.dataset.cell,
      cards: [...el.querySelectorAll('[data-lesson-id]')]
        .sort(
          (a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x,
        )
        .map((c) => c.dataset.lessonId),
    })),
  );
  assert.equal(week.length, 315);
  for (const { key, cards } of week) {
    const [row, col] = key.split(':').map(Number);
    assert.deepEqual(
      cards,
      dayPlacements.get(
        Math.floor(row / payload.slots.length) +
          '/' +
          (row % payload.slots.length) +
          ':' +
          col,
      ),
    );
  }
  await page.getByRole('button', { name: 'День', exact: true }).click();
  await page.getByRole('button', { name: 'Понедельник', exact: true }).click();
  const physical = payload.lessons.find(
    (l) =>
      l.day === 0 &&
      l.room &&
      /[0-9]/.test(l.room) &&
      l.teacherId &&
      !l.activityType &&
      !l.specialMark,
  );
  const sample = page
    .locator('[data-lesson-id=' + JSON.stringify(physical.id) + ']')
    .first();
  await sample.dblclick();
  const inspector = page.locator('.constructor-inspector.is-open');
  const room = inspector.getByRole('combobox', {
    name: 'Кабинет',
    exact: true,
  });
  const before = await room.inputValue();
  await inspector.getByText('Формат занятия', { exact: true }).click();
  const online = inspector.getByRole('checkbox', {
    name: 'Этот урок проводится онлайн',
  });
  await online.check();
  assert.equal(await room.isDisabled(), true);
  await inspector
    .locator('details')
    .filter({ has: page.getByRole('checkbox',{name:'Этот урок проводится онлайн'}) })
    .screenshot({ path: out + '/online-format.png' });
  await online.uncheck();
  assert.equal(await room.inputValue(), before);
  assert.equal(
    await page
      .getByText('Есть несохранённые изменения', { exact: true })
      .count(),
    0,
  );
  const kinds = inspector.getByRole('group', {
    name: 'Вид занятия',
    exact: true,
  });
  await kinds.getByRole('button', { name: 'Практикум' }).click();
  await inspector
    .getByRole('button', { name: 'Только отвечает за задание' })
    .click();
  assert.equal(
    await kinds
      .getByRole('button', { name: 'Практикум' })
      .getAttribute('aria-pressed'),
    'true',
  );
  await kinds.getByRole('button', { name: 'Консультация' }).click();
  await inspector
    .getByRole('alert')
    .filter({ hasText: 'Выберите преподавателя консультации' })
    .waitFor();
  await page
    .getByRole('button', { name: 'Закрыть панель', exact: true })
    .click();
  await page.getByRole('button', { name: 'Пятница', exact: true }).click();
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.getByRole('button', { name: 'Пятница', exact: true }).click();
  const onlineClass = page
    .getByRole('gridcell', { name: /^7 класс,/ })
    .filter({ has: page.locator('.constructor-card') })
    .first();
  await onlineClass.locator('.constructor-card').first().dblclick();
  const automatic = inspector.getByText('Онлайн по режиму дня класса', {
    exact: true,
  });
  await automatic.waitFor();
  assert.equal(
    await inspector
      .getByRole('checkbox', { name: 'Этот урок проводится онлайн' })
      .count(),
    0,
  );
  await automatic.screenshot({ path: out + '/online-day.png' });
  await page
    .getByRole('button', { name: 'Закрыть панель', exact: true })
    .click();
  for (const [width, height, grade] of [
    [1366, 768, 10],
    [1366, 768, 11],
    [1440, 900, 10],
    [1440, 900, 11],
    [390, 844, 11],
  ]) {
    await page.setViewportSize({ width, height });
    const target = page
      .getByRole('gridcell', { name: new RegExp('^' + grade + ' класс,') })
      .filter({ has: page.locator('.constructor-card') })
      .first();
    await target.locator('.constructor-card').first().dblclick();
    const drawer = page.locator('.constructor-inspector.is-open');
    await drawer.waitFor();
    await drawer.getByRole('heading').first().scrollIntoViewIfNeeded();
    await page.evaluate(
      () =>
        new Promise((r) =>
          requestAnimationFrame(() => requestAnimationFrame(r)),
        ),
    );
    if (width >= 1024) {
      const w = await grid.boundingBox(),
        h = await target.boundingBox(),
        d = await drawer.boundingBox();
      assert.ok(
        h.x >= w.x - 1 && h.x + h.width <= d.x + 1,
        'selected grade obscured ' + JSON.stringify({ h, w, d }),
      );
    }
    await page.screenshot({
      path: out + '/inspector-' + grade + '-' + width + '.png',
    });
    await page
      .getByRole('button', { name: 'Закрыть панель', exact: true })
      .click();
  }
  console.log(
    JSON.stringify({ browserGradeSlots: slots, distinctCards: seen.size }),
  );
} finally {
  await browser.close();
}
