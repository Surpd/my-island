import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const { chromium, webkit } = await import(process.env.CAMPUS_PLAYWRIGHT
  ? pathToFileURL(process.env.CAMPUS_PLAYWRIGHT).href
  : 'playwright')

const base = process.env.CAMPUS_URL || 'http://127.0.0.1:4176'
const offline = new URL(base).protocol === 'file:'
const shots = process.env.CAMPUS_SHOTS
const worlds = ['junior', 'middle', 'senior', 'graduate']
const viewports = process.env.CAMPUS_FOCUS_ONLY ? [] : [
  [320, 640], [360, 800], [390, 640], [390, 844], [430, 932], [390, 490],
  [320, 640, 490], [390, 844, 560],
]
const fixtureControls = '#role, #age-group, #homeroom, #data-state'
const routes = ['schedule', 'work', 'results', 'news', 'profile']
const engine = process.env.CAMPUS_WEBKIT ? webkit : chromium
const browser = await engine.launch(
  process.env.CAMPUS_CHROME ? { executablePath: process.env.CAMPUS_CHROME } : {},
)

if (shots) await mkdir(shots, { recursive: true })

async function assertWithinScroller(room, locator, label) {
  await locator.scrollIntoViewIfNeeded()
  const frame = await room.locator('.room-scroll').boundingBox()
  const target = await locator.boundingBox()
  assert.ok(target && target.y >= frame.y - 1 &&
    target.y + target.height <= frame.y + frame.height + 1, `${label} is fully reachable inside the scroll area`)
}

async function assertOverviewLayout(page, label, compact) {
  const layout = await page.evaluate(() => {
    const root = document.querySelector('.campus')
    const limits = window.visualViewport
    const css = getComputedStyle(root)
    const number = (property) => parseFloat(css.getPropertyValue(property)) || 0
    const top = (limits?.offsetTop || 0) + number('--host-safe-top') + number('--host-content-top')
    const bottom = Math.min(limits?.height || innerHeight, number('--host-height') || Infinity) -
      number('--host-safe-bottom') - number('--host-content-bottom')
    const measure = (selector) => {
      const element = root.querySelector(selector)
      if (!element?.getClientRects().length) return null
      const bounds = element.getBoundingClientRect()
      const clip = element.closest('.lesson')?.getBoundingClientRect() ?? bounds
      const textRange = document.createRange()
      textRange.selectNodeContents(element)
      const textBounds = [...textRange.getClientRects()].filter((rect) => rect.width && rect.height)
      return {
        top: bounds.top, bottom: bounds.bottom,
        textOutside: textBounds.some((rect) => rect.left < bounds.left - 1 ||
          rect.right > bounds.right + 1 || rect.top < clip.top - 1 || rect.bottom > clip.bottom + 1),
      }
    }
    return { top, bottom, compact: root.dataset.compact === 'true',
      mast: measure('.mast'), stage: measure('.stage'), places: measure('#places'),
      card: measure('.lesson'), subject: measure('.lesson-name'), lastLine: measure('.lesson-meta'),
      time: measure('.lesson-meta time'), location: measure('.lesson-meta span'),
      horizontalOverflow: document.documentElement.scrollWidth - innerWidth }
  })
  assert.equal(layout.compact, compact, `${label} compact state`)
  assert.ok(layout.horizontalOverflow < 2, `${label} horizontal overflow`)
  assert.equal(layout.mast, null, `${label} has no custom masthead`)
  for (const name of ['stage', 'places', 'card', 'subject', 'lastLine', 'time', 'location']) {
    const block = layout[name]
    assert.ok(block && block.top >= layout.top - 1 && block.bottom <= layout.bottom + 1,
      `${label} / ${name} is fully visible ${JSON.stringify({ block, top: layout.top, bottom: layout.bottom })}`)
  }
  for (const name of ['subject', 'time', 'location']) {
    assert.equal(layout[name].textOutside, false, `${label} / ${name} has no clipping`)
  }
  if (layout.lastLine) assert.ok(layout.lastLine.bottom <= layout.card.bottom + 1, `${label} / last line fits card`)
  assert.ok(Math.abs(layout.stage.top - layout.top) <= 1,
    `${label} / map starts at the usable top (${layout.stage.top} vs ${layout.top})`)
  assert.ok(layout.stage.bottom <= layout.card.top + 1, `${label} / stage and card are separate`)
}

async function evidence(page, file) {
  if (!shots) return
  await page.evaluate(() => document.fonts.ready)
  await page.waitForFunction(() => document.getAnimations().every((animation) =>
    animation.effect?.getTiming().iterations === Infinity || animation.playState !== 'running'))
  await page.screenshot({ path: join(shots, file + '.png'), scale: 'device' })
}

async function waitOverview(page) {
  await page.waitForFunction(() => {
    const camera = document.querySelector('.camera')
    const campus = document.querySelector('.campus')
    return camera && !campus.dataset.travel && !camera.querySelector('.markers[inert]') &&
      new DOMMatrixReadOnly(getComputedStyle(camera).transform).a < 1.01
  })
}

async function openPreferences(page) {
  const preferences = page.locator('#preferences')
  const profile = page.locator('#room')
  await waitOverview(page)
  await page.locator('.marker[data-location="profile"]').tap()
  await profile.getByRole('button', { name: 'Настройки', exact: true }).tap()
  await preferences.waitFor({ state: 'visible' })
  assert.equal(await preferences.locator(fixtureControls).count(), 0, 'Account preferences exclude fixture controls')
}

async function closePreferences(page) {
  const room = page.locator('#room')
  await page.locator('#preferences').getByRole('button', { name: 'Закрыть настройки' }).tap()
  await room.waitFor({ state: 'visible' })
  await page.waitForFunction(() => document.activeElement?.id === 'profile-settings')
  await room.getByRole('button', { name: 'Вернуться на остров' }).tap()
  await room.waitFor({ state: 'hidden' })
  await waitOverview(page)
}

async function selectFixture(page, world, role = 'student', state = 'ready', homeroom = true, viaProfile = false) {
  const hostViewport = await page.evaluate(() => window.campusTestHost?.geometry())
  if (viaProfile) {
    await waitOverview(page)
    await page.locator('.marker[data-location="profile"]').tap()
    await page.locator('#preview-tools').tap()
  } else {
    const url = new URL(base)
    url.searchParams.set('preview', '1')
    await page.goto(url.href)
  }
  await page.locator('#role').selectOption(role)
  if (role === 'student') await page.locator('#age-group').selectOption(world)
  else await page.locator('#homeroom').setChecked(homeroom)
  await page.locator('#data-state').selectOption(state)
  await page.locator('#open-preview-campus').tap()
  await page.waitForURL((url) => url.searchParams.get('campus') === '1')
  if (hostViewport) await page.evaluate((viewport) => window.campusTestHost.viewport(...viewport), hostViewport)
  await page.waitForFunction(([role, world]) => {
    const root = document.querySelector('.campus')
    return root?.dataset.role === role && root.dataset.world === world
  }, [role, role === 'teacher' ? 'faculty' : world])
  assert.equal(await page.locator(fixtureControls).count(), 0, 'Fixture controls stay outside the campus')
}

async function assertNormalMode(page) {
  const campus = page.locator('.campus')
  assert.equal(await campus.getAttribute('data-role'), 'student')
  assert.equal(await campus.getAttribute('data-world'), 'middle')
  assert.equal(await page.locator('#settings, .mast, ' + fixtureControls).count(), 0)
  await openPreferences(page)
  assert.equal(await page.locator('#preview-tools').count(), 0)
  await closePreferences(page)
}

const middleSurface = {
  schedule: [[45, 19], [56, 26]],
  work: [[21, 28], [24, 34]],
  results: [[80, 27], [83, 32]],
  news: [[15, 47], [15, 55]],
  profile: [[75, 60], [78, 65]],
}
const waterSamples = [[50, 6], [48, 90]]

async function tapArt(page, [x, y], route) {
  const art = await page.locator('.scene-image').boundingBox()
  const point = { x: art.x + art.width * x / 100, y: art.y + art.height * y / 100 }
  const hit = await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('[data-location]')?.dataset.location ?? null, point)
  assert.equal(hit, route, `Artwork coordinate ${x},${y} matches ${route ?? 'empty water'}`)
  await page.touchscreen.tap(point.x, point.y)
}

async function verifyArtwork(page) {
  const room = page.locator('#room')
  for (const [route, samples] of Object.entries(middleSurface)) {
    for (const sample of samples) {
      await waitOverview(page)
      await tapArt(page, sample, route)
      await page.waitForFunction((id) => document.querySelector('.campus').dataset.travel === id, route)
      await room.waitFor({ state: 'visible' })
      await room.getByRole('button', { name: 'Вернуться на остров' }).tap()
      await room.waitFor({ state: 'hidden' })
    }
  }
  await waitOverview(page)
  for (const sample of waterSamples) await tapArt(page, sample, null)
  assert.equal(await page.locator('.campus').getAttribute('data-travel'), null)
  assert.equal(await room.isVisible(), false, 'Empty water opens no route')
}

async function verifySmallerBrowser() {
  const page = await browser.newPage({
    viewport: { width: 320, height: 640 }, deviceScaleFactor: 2,
    isMobile: true, hasTouch: true, reducedMotion: 'reduce', timezoneId: 'Europe/Moscow',
  })
  try {
    await page.clock.setFixedTime(new Date('2026-11-28T09:00:00+03:00'))
    await page.goto(base)
    await page.waitForFunction(() => document.querySelector('.scene-image')?.dataset.loaded === 'true')
    assert.equal(await page.evaluate(() => devicePixelRatio), 2)
    await assertOverviewLayout(page, 'Browser 320×640 / DPR 2', false)
    await page.setViewportSize({ width: 320, height: 490 })
    await page.waitForFunction(() => document.querySelector('.campus')?.dataset.compact === 'true')
    await assertOverviewLayout(page, 'Browser 320×490 / DPR 2', true)
    await page.locator('.marker[data-location="schedule"]').tap()
    await page.locator('#room').waitFor({ state: 'visible' })
    await page.locator('#room button[aria-expanded="true"]').click()
    assert.equal(await page.locator('#room').getAttribute('data-expanded'), 'false')
    await page.keyboard.press('Escape')
    await page.locator('#room').waitFor({ state: 'hidden' })
    console.log('[Browser / DPR 2] 320×640 and 320×490, reduced motion and working sheet')
  } finally {
    await page.close()
  }
}

async function verifyNightLoad() {
  if (offline) return
  for (const coldNight of [true, false]) {
    const page = await browser.newPage({
      viewport: { width: 390, height: 844 }, deviceScaleFactor: 3,
      isMobile: true, hasTouch: true, timezoneId: 'Europe/Moscow',
    })
    let release
    const delay = new Promise((resolve) => { release = resolve })
    try {
      await page.clock.setFixedTime(new Date(coldNight ? '2026-11-28T22:00:00+03:00' : '2026-11-28T09:00:00+03:00'))
      const requested = page.waitForRequest((request) => request.resourceType() === 'image' && request.url().includes('middle-night.webp'))
      await page.route(/\/(?:hd-)?middle-night\.webp(?:\?|$)/, async (route) => {
        if (route.request().resourceType() === 'image') await delay
        await route.continue()
      })
      await page.goto(base, { waitUntil: 'domcontentloaded' })
      const campus = page.locator('.campus')
      let initialGeometry
      if (!coldNight) {
        await page.waitForFunction(() => document.querySelector('.scene-image')?.dataset.loaded === 'true')
        initialGeometry = await page.locator('.building-hit[data-location="schedule"]').getAttribute('points')
        await openPreferences(page)
        await page.locator('#lighting').selectOption('night')
        await closePreferences(page)
      }
      await requested
      await page.waitForFunction(() => document.querySelector('.campus')?.hasAttribute('data-lighting-pending'))
      if (!coldNight) {
        assert.equal(await campus.getAttribute('data-theme'), 'day')
        assert.equal(await page.locator('.building-hit[data-location="schedule"]').getAttribute('points'), initialGeometry)
        await tapArt(page, middleSurface.schedule[1], 'schedule')
        await page.locator('#room').waitFor({ state: 'visible' })
      }
      const leakedDay = await page.locator('.scene-image').evaluateAll((images) =>
        images.some((image) => image.complete && image.naturalWidth && image.currentSrc.includes('tide-student') &&
          Number(getComputedStyle(image).opacity) > .05))
      assert.equal(leakedDay, !coldNight, coldNight ? 'Cold night never flashes the day image' : 'Day stays visible while the night image loads')
      release()
      await page.waitForFunction(() => {
        const root = document.querySelector('.campus')
        const image = root?.querySelector('.scene-image')
        return root?.dataset.theme === 'night' && !root.hasAttribute('data-lighting-pending') &&
          image?.currentSrc.includes('middle-night') && image.complete && image.naturalWidth > 0
      })
      if (!coldNight) assert.equal(await page.locator('#room').isVisible(), true, 'Theme swap preserves open work')
    } finally {
      release()
      await page.close()
    }
  }
  console.log('[Browser / night images] cold cache and delayed request, no day flash, previous scene and route preserved until swap')
}

async function recordTour(page) {
  if (!shots || !process.env.CAMPUS_TOUR || process.env.CAMPUS_WEBKIT) return
  assert.equal(await page.evaluate(() => devicePixelRatio), 3)
  await page.waitForFunction(() => document.querySelector('.campus')?.dataset.motion === 'full')
  const directory = process.env.CAMPUS_TOUR_FRAMES || join(shots, 'tour-frames')
  await mkdir(directory, { recursive: true })
  const frames = []
  const room = page.locator('#room')
  let finished = false
  const capture = async () => {
    while (!finished) {
      const time = Date.now()
      const bytes = await page.screenshot({ type: 'jpeg', quality: 86, scale: 'device' })
      frames.push(time)
      await writeFile(join(directory, `tour-${String(frames.length).padStart(3, '0')}.jpg`), bytes)
    }
  }
  await waitOverview(page)
  const captureTask = capture()
  try {
    await page.waitForTimeout(500)
    await tapArt(page, middleSurface.schedule[1], 'schedule')
    await page.waitForFunction(() => {
      const matrix = new DOMMatrixReadOnly(getComputedStyle(document.querySelector('.camera')).transform)
      return matrix.a >= 1.25 && !document.querySelector('#room').open
    })
    await room.waitFor({ state: 'visible' })
    await page.waitForTimeout(900)
    await room.getByRole('button', { name: 'Вернуться на остров' }).tap()
    await waitOverview(page)
    await page.waitForTimeout(500)
  } finally {
    finished = true
    await captureTask
    await writeFile(join(directory, 'timestamps.json'), JSON.stringify(frames))
  }
}

async function verifyBrowserOverview() {
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 3,
    isMobile: true, hasTouch: true, timezoneId: 'Europe/Moscow',
  })
  try {
    await page.clock.setFixedTime(new Date('2026-11-28T09:00:00+03:00'))
    await page.goto(base)
    await page.locator('.campus').waitFor()
    assert.equal(await page.evaluate(() => typeof window.Telegram), 'undefined', 'Browser overview has no Telegram stub')
    const campus = page.locator('.campus')
    page.setDefaultTimeout(7000)
    const room = page.locator('#room')
    async function change(id, value) {
      await openPreferences(page)
      const control = page.locator('#' + id)
      if (typeof value === 'boolean') await control.setChecked(value)
      else await control.selectOption(value)
      await closePreferences(page)
    }
    async function waitScene(file) {
      await page.waitForFunction((name) => [...document.querySelectorAll('.scene-image')].some((image) =>
        new URL(image.currentSrc, location.href).pathname.split('/').at(-1).replace(/^hd-/, '') === name &&
        image.complete && image.naturalWidth && Number(getComputedStyle(image).opacity) > .95), file)
    }
    await waitScene('tide-student.webp')
    await page.waitForFunction(() => {
      const marker = document.querySelector('.marker-tag')
      return marker && Number(getComputedStyle(marker.closest('.markers')).opacity) > .95
    })
    await assertOverviewLayout(page, 'Browser 390×844', false)
    await evidence(page, 'harbour-mobile')
    await assertNormalMode(page)
    await selectFixture(page, 'graduate')
    for (const [width, height] of [[320, 640], [360, 800], [390, 640], [390, 490], [430, 932], [390, 844]]) {
      await page.setViewportSize({ width, height })
      await page.waitForFunction((expected) =>
        (document.querySelector('.campus').dataset.compact === 'true') === expected, height < 510)
      await page.evaluate(() => document.fonts.ready)
      await assertOverviewLayout(page, `Browser ${width}×${height} / graduate`, height < 510)
    }
    await change('large-text', true)
    await assertOverviewLayout(page, 'Browser 390×844 / graduate / large text', false)
    await evidence(page, 'harbour-large-text')
    await page.locator('.lesson').tap()
    await room.waitFor({ state: 'visible' })
    await room.getByRole('button', { name: 'Неделя', exact: true }).tap()
    await assertWithinScroller(room, room.getByRole('heading', { name: 'Воскресенье, 29 ноября' }), 'Large-text weekly final day')
    assert.equal(await room.locator('.room-scroll').evaluate((node) => node.scrollWidth - node.clientWidth), 0)
    await room.getByRole('button', { name: 'Вернуться на остров' }).tap()
    await room.waitFor({ state: 'hidden' })
    await page.setViewportSize({ width: 390, height: 420 })
    await page.locator('.lesson-meta').scrollIntoViewIfNeeded()
    const enlarged = await page.locator('.lesson-meta').boundingBox()
    assert.ok(enlarged && enlarged.y >= 0 && enlarged.y + enlarged.height <= 421,
      'Large text keeps the class or room reachable in a short browser viewport')
    await page.setViewportSize({ width: 390, height: 844 })
    await page.evaluate(() => window.scrollTo(0, 0))
    await change('large-text', false)
    await change('lighting', 'night')
    await waitScene('graduate-night.webp')
    await evidence(page, 'harbour-night')
    await change('lighting', 'day')
    await selectFixture(page, 'middle', 'student', 'ready', true, true)
    await page.goto(base)
    await assertNormalMode(page)
    await waitScene('tide-student.webp')
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await verifyArtwork(page)
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await recordTour(page)
    await page.locator('.marker[data-location="schedule"]').tap()
    await page.waitForFunction(() => {
      const camera = document.querySelector('.camera')
      const stage = camera.closest('.stage').getBoundingClientRect()
      const building = camera.querySelector('.building-hit[data-location="schedule"]').getBoundingClientRect()
      const zoom = new DOMMatrixReadOnly(getComputedStyle(camera).transform).a
      return zoom >= 1.25 && !document.querySelector('#room').open &&
        building.top >= stage.top - 1 && building.bottom <= stage.bottom + 1 &&
        Math.abs((building.left + building.right) / 2 - (stage.left + stage.right) / 2) < stage.width / 6
    })
    await room.waitFor({ state: 'visible' })
    assert.equal(await room.getAttribute('data-expanded'), 'true', 'Scheduling opens in working height')
    await room.getByRole('button', { name: 'Свернуть раздел' }).tap()
    assert.equal(await room.getAttribute('data-expanded'), 'false')
    const initial = await room.boundingBox()
    await room.getByRole('button', { name: 'Развернуть раздел' }).tap()
    const expand = await room.boundingBox()
    assert.equal(await room.getAttribute('data-expanded'), 'true')
    assert.ok(expand.height > initial.height + 48, `Expanded panel gains vertical working space (${initial.height} → ${expand.height})`)
    await evidence(page, 'harbour-schedule-expanded')
    const scroller = room.locator('.room-scroll')
    const saturday = room.getByRole('button', { name: 'Суббота, 28 ноября' })
    await saturday.tap()
    await saturday.and(room.locator('[aria-pressed="true"]')).waitFor()
    await room.getByRole('button', { name: 'Неделя', exact: true }).tap()
    const weekend = room.getByRole('heading', { name: 'Воскресенье, 29 ноября' })
    await assertWithinScroller(room, weekend, 'Expanded weekly final day')
    const position = await scroller.evaluate((node) => node.scrollTop)
    assert.ok(position > 0)
    assert.equal(await room.getByRole('button', { name: 'Настройки', exact: true }).count(), 0)
    await room.getByRole('button', { name: 'Свернуть раздел' }).tap()
    assert.equal(await room.getAttribute('data-expanded'), 'false')
    await room.getByRole('button', { name: 'Развернуть раздел' }).tap()
    assert.equal(await room.getByRole('button', { name: 'Неделя', exact: true }).getAttribute('aria-pressed'), 'true')
    await page.waitForFunction((value) => Math.abs(document.querySelector('.room-scroll').scrollTop - value) <= 2, position)
    await room.getByRole('button', { name: 'День', exact: true }).tap()
    await saturday.and(room.locator('[aria-pressed="true"]')).waitFor()
    await page.goBack()
    await room.waitFor({ state: 'hidden' })
    await selectFixture(page, 'teacher', 'teacher')
    await openPreferences(page)
    await evidence(page, 'harbour-settings')
    await closePreferences(page)
    await page.locator('.marker[data-location="work"]').tap()
    await room.waitFor({ state: 'visible' })
    await room.getByRole('button', { name: /8Б · математика/ }).tap()
    await room.getByRole('button', { name: 'Состав', exact: true }).tap()
    await room.getByRole('button', { name: /Кира Смирнова/ }).waitFor()
    await evidence(page, 'harbour-group-nested')
    await room.getByRole('button', { name: /Кира Смирнова/ }).tap()
    await room.getByRole('heading', { name: 'Индивидуальные результаты' }).waitFor()
    await room.getByRole('button', { name: 'Вернуться на остров' }).tap()
    await room.waitFor({ state: 'hidden' })
    assert.equal(await campus.getAttribute('data-role'), 'teacher')
    const normal = new URL(base)
    normal.searchParams.set('role', 'teacher')
    normal.searchParams.set('stage', 'senior')
    normal.searchParams.set('state', 'error')
    await page.goto(normal.href)
    await assertNormalMode(page)
    console.log('[Browser / no Telegram] full-card vertical bounds, no clipping, simulated viewport-height changes, expanded panel, last row, selection/scroll/settings/back, large text, nested close')
  } finally {
    await page.close()
  }
}

async function verifySimulatedTelegram() {
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
    deviceScaleFactor: 3, timezoneId: 'Europe/Moscow',
  })
  if (offline) await page.context().setOffline(true)
  await page.clock.setFixedTime(new Date('2026-11-28T09:00:00+03:00'))
  page.setDefaultTimeout(10000)
  console.log('[Simulated Telegram SDK] safe area, activation and BackButton scenarios')
  const errors = []
  let routeChecks = 0
  const targetFailures = []
  page.on('pageerror', (error) => errors.push(error.message))

  await page.addInitScript(() => {
    const state = { shown: false, callbacks: new Set(), events: new Map(),
      top: 0, bottom: 0, contentTop: 0, contentBottom: 0, height: 0, active: true }
    const emit = (name, data) => state.events.get(name)?.forEach((callback) => callback(data))
    const BackButton = {
      get isVisible() { return state.shown },
      show() { state.shown = true; return this },
      hide() { state.shown = false; return this },
      onClick(callback) { state.callbacks.add(callback); return this },
      offClick(callback) { state.callbacks.delete(callback); return this },
    }
    window.campusTestHost = {
      get shown() { return state.shown },
      geometry: () => [state.height, state.top, state.bottom, state.contentTop, state.contentBottom],
      press: () => state.callbacks.forEach((callback) => callback()),
      viewport(height, top = 0, bottom = 0, contentTop = 0, contentBottom = 0) {
        Object.assign(state, { height, top, bottom, contentTop, contentBottom })
        emit('viewportChanged', { isStateStable: true })
        emit('safeAreaChanged')
        emit('contentSafeAreaChanged')
      },
      active(active) {
        state.active = active
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => active ? 'visible' : 'hidden' })
        document.dispatchEvent(new Event('visibilitychange'))
        emit(active ? 'activated' : 'deactivated')
        emit(active ? 'activate' : 'deactivate')
      },
    }
    window.Telegram = {
      WebApp: {
        BackButton, ready() {}, expand() {},
        get isActive() { return state.active },
        get viewportHeight() { return state.height || innerHeight },
        get viewportStableHeight() { return state.height || innerHeight },
        get safeAreaInset() { return { top: state.top, bottom: state.bottom, left: 0, right: 0 } },
        get contentSafeAreaInset() { return { top: state.contentTop, bottom: state.contentBottom, left: 0, right: 0 } },
        onEvent(name, callback) {
          if (!state.events.has(name)) state.events.set(name, new Set())
          state.events.get(name).add(callback)
        },
        offEvent(name, callback) { state.events.get(name)?.delete(callback) },
      },
    }
  })

  const room = page.locator('dialog#room')
  const campus = page.locator('.campus')
  const markers = campus.locator('.marker[data-location]')
  const place = (id) => markers.filter({ visible: true }).and(page.locator(`[data-location="${id}"]`))
  const expectOverview = () => page.waitForFunction(() => {
    const camera = document.querySelector('.camera')
    return camera && !camera.querySelector('.markers[inert]') &&
      new DOMMatrixReadOnly(getComputedStyle(camera).transform).a < 1.01
  })
  const open = async (id) => {
    await expectOverview()
    await place(id).locator('.marker-tag').tap()
    assert.equal(await campus.getAttribute('data-travel'), id, `Touch on ${id} starts its route`)
    await room.waitFor({ state: 'visible' })
  }
  const close = async () => {
    await page.locator('#back').click()
    await room.waitFor({ state: 'hidden' })
  }
  const assertScene = (theme) => page.waitForFunction((value) => {
    const campus = document.querySelector('.campus')
    const image = campus?.querySelector('img.scene-image')
    return campus?.dataset.theme === value && image?.dataset.loaded === 'true' &&
      image.complete && image.naturalWidth > 0 && image.naturalHeight > 0
  }, theme)
  const assertNoOverflow = async (label) => {
    const dimensions = await page.evaluate(() => {
      const modal = document.querySelector('dialog#room[open]')
      const view = document.documentElement
      return {
        viewportOverflow: view.scrollWidth - view.clientWidth,
        panelOverflow: modal ? modal.scrollWidth - modal.clientWidth : 0,
        scrollerOverflow: modal ? modal.querySelector('.room-scroll').scrollWidth - modal.querySelector('.room-scroll').clientWidth : 0,
      }
    })
    assert.ok(Object.values(dimensions).every((overflow) => overflow < 2),
      `${label} overflow ${JSON.stringify(dimensions)}`)
  }
  const cameraScale = () => page.locator('.camera').evaluate((element) =>
    new DOMMatrixReadOnly(getComputedStyle(element).transform).a)
  const openSettings = () => openPreferences(page)
  const closeSettings = () => closePreferences(page)
  const expectCampus = (attributes) => page.waitForFunction((values) => {
    const island = document.querySelector('.campus')
    return Object.entries(values).every(([key, value]) => island?.dataset[key] === value)
  }, attributes)
  const selectDemo = async (world, role, theme, state = 'ready', homeroom = true) => {
    if (theme && await page.locator('.campus').getAttribute('data-theme') !== theme) {
      await openSettings()
      await page.locator('#lighting').selectOption(theme)
      await closeSettings()
    }
    await selectFixture(page, world, role, state, homeroom)
    await expectCampus({ world: role === 'student' ? world : 'faculty', role, ...(theme ? { theme } : {}) })
  }
  const assertContrast = async () => {
    const ratios = await campus.evaluate((element) => {
      const style = getComputedStyle(element)
      const luminance = (variable) => {
        const rgb = style.getPropertyValue(variable).trim().slice(1).match(/../g)
        return rgb.map((hex) => parseInt(hex, 16) / 255)
          .map((channel) => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
          .reduce((total, channel, index) => total + channel * [0.2126, 0.7152, 0.0722][index], 0)
      }
      return [
        ['island title', '--ink', '--paper'], ['secondary text', '--muted', '--paper'],
        ['map label', '--map-ink', '--map-label'], ['panel text', '--ink', '--surface'],
        ['panel secondary text', '--muted', '--surface'],
      ].map(([label, foreground, background]) => {
        const colors = [luminance(foreground), luminance(background)]
        return [label, (Math.max(...colors) + 0.05) / (Math.min(...colors) + 0.05)]
      })
    })
    for (const [label, ratio] of ratios) assert.ok(ratio >= 4.5, `${label} contrast ${ratio.toFixed(2)}:1`)
    return Math.min(...ratios.map(([, ratio]) => ratio))
  }
  const assertTargets = async (label, height, top, bottom) => {
    await page.evaluate(() => window.scrollTo(0, 0))
    await expectOverview()
    await page.waitForFunction(() => {
      const campus = document.querySelector('.campus')
      return !campus.hasAttribute('data-lighting-pending') &&
        campus.querySelector('.scene-frame')?.dataset.sceneWorld === campus.dataset.world
    })
    assert.ok(await markers.count() >= 5, `${label} has the main locations`)
    const problems = await markers.evaluateAll((buttons, viewport) => buttons.flatMap((button) => {
      const id = button.dataset.location
      const tag = button.querySelector('.marker-tag')
      const polygon = button.closest('.markers').querySelector(`.building-hit[data-location="${id}"]`)
      const chip = tag.getBoundingClientRect()
      const building = polygon.getBoundingClientRect()
      const left = Math.min(chip.left, building.left)
      const right = Math.max(chip.right, building.right)
      const top = Math.min(chip.top, building.top)
      const bottom = Math.max(chip.bottom, building.bottom)
      const issues = []
      if (right - left < 43.5 || bottom - top < 43.5) {
        issues.push(`${id} interactive region ${Math.round(right - left)}×${Math.round(bottom - top)}`)
      }
      if (left < -1 || right > innerWidth + 1 || top < viewport.top - 1 || bottom > viewport.height - viewport.bottom + 1) {
        issues.push(`${id} outside usable viewport (${top.toFixed(0)}–${bottom.toFixed(0)})`)
      }
      const hit = document.elementFromPoint(chip.left + chip.width / 2, chip.top + chip.height / 2)
      if (!tag.contains(hit)) issues.push(`${id} tag center covered by ${hit?.tagName}`)
      const points = Array.from({ length: polygon.points.length }, (_, index) => polygon.points.getItem(index))
      const center = points.reduce((acc, vertex) => ({ x: acc.x + vertex.x / points.length, y: acc.y + vertex.y / points.length }), { x: 0, y: 0 })
      const hitTests = points.map((point, index) => {
        const next = points[(index + 1) % points.length]
        return new DOMPoint((point.x + next.x + center.x * 2) / 4, (point.y + next.y + center.y * 2) / 4)
      }).filter((point) => polygon.isPointInFill(point))
      if (hitTests.length < 2 || !hitTests.some((point) => {
        const screen = point.matrixTransform(polygon.getScreenCTM())
        return document.elementFromPoint(screen.x, screen.y)?.closest('[data-location]')?.dataset.location === id
      })) issues.push(`${id} artwork has no reachable sample`)
      for (const other of buttons.slice(buttons.indexOf(button) + 1)) {
        const otherChip = other.querySelector('.marker-tag').getBoundingClientRect()
        if (Math.min(chip.right, otherChip.right) - Math.max(chip.left, otherChip.left) > 1 &&
            Math.min(chip.bottom, otherChip.bottom) - Math.max(chip.top, otherChip.top) > 1) {
          issues.push(`${id}/${other.dataset.location} tag overlap`)
        }
      }
      for (const other of button.closest('.markers').querySelectorAll('.building-hit')) {
        if (other === polygon) continue
        const inverse = other.getScreenCTM().inverse()
        let covered = 0
        for (let row = 1; row <= 4; row++) {
          for (let col = 1; col <= 8; col++) {
            const point = new DOMPoint(chip.left + chip.width * col / 9, chip.top + chip.height * row / 5)
            if (other.isPointInFill(point.matrixTransform(inverse))) covered++
          }
        }
        if (covered >= 2) issues.push(`${id} tag masks ${other.dataset.location} artwork`)
      }
      return issues
    }), { height, top, bottom })
    if (problems.length) targetFailures.push(`${label} — ${problems.join(', ')}`)
  }

  await page.goto(base)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  for (const [width, height, compact] of viewports) {
    await page.setViewportSize({ width, height })
    const top = compact ? 24 : 0
    const bottom = compact ? 18 : 0
    await page.evaluate(([available, top, bottom]) => window.campusTestHost.viewport(available, top, bottom),
      [compact || height, top, bottom])
    for (const [world, role] of [...worlds.map((world) => [world, 'student']), ['teacher', 'teacher']]) {
      const label = `${width}×${height}${compact ? ` / compact ${compact}` : ''} / ${world}`
      await selectDemo(world, role, 'day')
      await assertNoOverflow(label)
      await assertOverviewLayout(page, label, (compact || height) < 510)
      await assertTargets(label, compact || height, top, bottom)
      if (width === 390 && height === 844 && !compact) {
        await assertScene('day')
        await selectDemo(world, role, 'night')
        await assertScene('night')
        await assertNoOverflow(`${label} / night`)
        await assertOverviewLayout(page, `${label} / night`, false)
        await assertTargets(`${label} / night`, height, 0, 0)
        await selectDemo(world, role, 'day')
      }
      if (process.env.CAMPUS_TARGETS_ONLY) continue
      const available = role === 'teacher' ? [...routes, 'homeroom'] : routes
      for (const route of available) {
        await open(route)
        assert.ok(await room.locator('.ci-interior').isVisible(), label)
        await assertNoOverflow(`${label} / ${route}`)
        if (width === 390 && !compact && route === 'schedule' && role === 'student') {
          const currentLesson = { junior: 'Математика', middle: 'Алгебра', senior: 'Алгебра и начала анализа', graduate: 'Алгебра и начала анализа' }
          await room.getByRole('button', { name: new RegExp(`По расписанию сейчас.*${currentLesson[world]}`) }).waitFor()
        }
        await close()
        routeChecks += 1
      }
    }
  }
  assert.deepEqual(targetFailures, [], 'Visible touch targets have no overlap and fit the usable viewport')
  if (process.env.CAMPUS_TARGETS_ONLY) {
    console.log('[Touch regions] each map target fits the usable viewport')
    return
  }
  if (routeChecks) console.log(`[${routeChecks}] world × viewport × building openings and touch targets checked`)

  await page.setViewportSize({ width: 390, height: 640 })
  await page.evaluate(() => window.campusTestHost.viewport(490, 18, 8, 30, 2))
  await page.waitForFunction(() => {
    const campus = document.querySelector('.campus')
    const style = getComputedStyle(campus)
    return campus.dataset.compact === 'true' && parseFloat(style.paddingTop) === 48 &&
      parseFloat(style.paddingBottom) === 10
  })
  await assertOverviewLayout(page, 'Simulated Telegram, separate physical/content insets', true)
  await page.evaluate(() => window.campusTestHost.viewport(700))
  await page.setViewportSize({ width: 390, height: 700 })
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await selectDemo('graduate', 'student', 'day')
  await openSettings()
  await page.locator('#lighting').selectOption('auto')
  await page.locator('#large-text').check()
  await page.locator('#reduce-motion').check()
  await closeSettings()
  await expectCampus({ world: 'graduate', theme: 'day', motion: 'reduce' })
  const dayContrast = await assertContrast()
  await page.clock.setFixedTime(new Date('2026-11-28T22:00:00+03:00'))
  await page.evaluate(() => { window.campusTestHost.active(false); window.campusTestHost.active(true) })
  await expectCampus({ theme: 'night' })
  const nightContrast = await assertContrast()
  console.log(`[OK] tested title/text/marker/panel palette pairs at ≥${Math.min(dayContrast, nightContrast).toFixed(2)}:1`)
  await open('schedule')
  await page.evaluate(() => window.campusTestHost.viewport(510, 26, 20))
  await page.waitForFunction(() => {
    const css = getComputedStyle(document.querySelector('.campus'))
    return css.getPropertyValue('--host-height').trim() === '510px' &&
      css.getPropertyValue('--host-safe-top').trim() === '26px' &&
      css.getPropertyValue('--host-safe-bottom').trim() === '20px'
  })
  await page.waitForFunction(() => {
    const bounds = document.querySelector('#room').getBoundingClientRect()
    const controls = [...document.querySelectorAll('.room-head button')].map((button) => button.getBoundingClientRect())
    const scroll = document.querySelector('.room-scroll')
    return bounds.top >= 25 && bounds.bottom <= 511 &&
      controls.every((button) => button.top >= 25 && button.bottom <= 491) &&
      parseFloat(getComputedStyle(scroll).paddingBottom) >= 20
  })
  await assertNoOverflow('Active panel after Telegram viewport resize')
  await page.evaluate(() => window.campusTestHost.active(false))
  await expectCampus({ active: 'false' })
  await page.evaluate(() => window.campusTestHost.active(true))
  await expectCampus({ active: 'true' })
  assert.ok(await room.isVisible(), 'Active panel survives a simulated Telegram background and reopen')
  await page.evaluate(() => window.campusTestHost.press())
  await room.waitFor({ state: 'hidden' })
  await page.reload()
  await expectCampus({ world: 'graduate', theme: 'night', motion: 'reduce' })
  await openSettings()
  assert.ok(await page.locator('#large-text').isChecked(), 'Large text persists after reload')
  assert.ok(await page.locator('#reduce-motion').isChecked(), 'Reduced motion persists after reload')
  assert.equal(await page.locator('#lighting').inputValue(), 'auto')
  await page.locator('#lighting').selectOption('day')
  await page.locator('#large-text').uncheck()
  await page.locator('#reduce-motion').uncheck()
  await closeSettings()
  await expectCampus({ theme: 'day', motion: 'full' })
  console.log('[OK] clock-selected day/night, manual override, preference reload, host resize and reopening')

  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.setViewportSize({ width: 390, height: 700 })
  await selectDemo('middle', 'student', 'day')
  await open('schedule')
  await room.getByRole('button', { name: 'Неделя', exact: true }).tap()
  const scrollArea = await room.locator('.room-scroll').boundingBox()
  const touch = await page.context().newCDPSession(page)
  const swipeX = Math.round(scrollArea.x + scrollArea.width / 2)
  const swipeY = Math.round(scrollArea.y + scrollArea.height * 0.8)
  await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: swipeX, y: swipeY }] })
  for (let step = 1; step <= 8; step++) {
    await touch.send('Input.dispatchTouchEvent', {
      type: 'touchMove', touchPoints: [{ x: swipeX, y: swipeY - step * 22 }],
    })
    await page.waitForTimeout(30)
  }
  // Release at rest so the following tap is distinct from stopping a fling.
  await page.waitForTimeout(250)
  await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await touch.detach()
  await page.waitForFunction(() => document.querySelector('.room-scroll')?.scrollTop > 0)
  const weekend = room.getByRole('heading', { name: 'Воскресенье, 29 ноября' })
  await weekend.scrollIntoViewIfNeeded()
  await assertWithinScroller(room, weekend, 'Weekly agenda final day')
  assert.ok(await room.evaluate((element) =>
    [element, ...element.querySelectorAll('*')].some((child) => child.scrollTop > 0)), 'Panel visibly scrolled')
  await close()

  await open('results')
  const algebra = room.getByRole('button', { name: /Алгебра/ })
  const archive = room.getByRole('button', { name: 'Архив · I триместр', exact: true })
  await archive.click()
  await algebra.click()
  await room.getByText('I триместр · 2026/27', { exact: true }).waitFor()
  assert.equal(await room.getByRole('heading', { name: 'Алгебра', exact: true }).count(), 1)
  await page.goBack()
  await archive.waitFor()
  assert.equal(await archive.getAttribute('aria-pressed'), 'true')
  await room.getByRole('button', { name: 'II триместр', exact: true }).click()
  await algebra.click()
  await room.getByRole('button', { name: /Квадратные корни/ }).click()
  await room.getByText('Назначена в демо', { exact: true }).waitFor()
  await page.goBack()
  await room.getByRole('heading', { name: 'Алгебра', exact: true }).waitFor()
  await page.locator('#back').click()
  await room.getByRole('button', { name: /Алгебра/ }).waitFor()
  await page.goBack()
  await room.waitFor({ state: 'hidden' })

  await page.locator('#places').click()
  const places = page.locator('dialog#directory')
  await places.getByRole('heading', { name: 'Места острова' }).waitFor()
  await places.getByRole('button', { name: /Расписание/ }).click()
  await room.waitFor({ state: 'visible' })
  await close()
  await page.locator('#places').click()
  await page.keyboard.press('Escape')
  await places.waitFor({ state: 'hidden' })
  await page.waitForFunction(() => document.activeElement?.id === 'places')

  const schedule = place('schedule')
  await expectOverview()
  await schedule.focus()
  await page.waitForFunction(() => document.activeElement?.dataset.location === 'schedule')
  await page.keyboard.press('Enter')
  await room.waitFor({ state: 'visible' })
  await page.waitForFunction(() => document.activeElement?.id === 'room-name')
  await page.keyboard.press('Tab')
  await page.waitForFunction(() => {
    const element = document.activeElement
    const style = getComputedStyle(element)
    return element.closest('#room') && element.matches(':focus-visible') && style.outlineStyle !== 'none'
  })
  assert.ok(await page.evaluate(() => window.campusTestHost.shown), 'Telegram BackButton becomes visible')
  await room.getByRole('button', { name: /По расписанию сейчас.*Алгебра/ }).click()
  await page.evaluate(() => window.campusTestHost.press())
  await room.getByRole('heading', { name: 'Вторник, 24 ноября' }).waitFor()
  await page.evaluate(() => window.campusTestHost.press())
  await room.waitFor({ state: 'hidden' })
  await page.waitForFunction(() => !window.campusTestHost.shown)
  await page.waitForFunction(() => document.activeElement?.dataset.location === 'schedule')

  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await expectOverview()
  await page.evaluate(() => {
    document.querySelector('.marker[data-location="schedule"]').click()
    document.querySelector('.marker[data-location="news"]').click()
  })
  await expectCampus({ travel: 'schedule' })
  await page.waitForFunction(() => new DOMMatrixReadOnly(getComputedStyle(document.querySelector('.camera')).transform).a > 1.01)
  assert.ok(!(await room.isVisible()), 'Camera travels before the room opens')
  await page.evaluate(() => window.campusTestHost.press())
  await page.waitForFunction(() => !window.campusTestHost.shown)
  await room.waitFor({ state: 'hidden' })
  await open('news')
  assert.ok(await cameraScale() >= 1.15, 'Camera meaningfully approaches the selected building at 390px DPR 3')
  await room.getByRole('button', { name: /Лаборатория по физике/ }).waitFor()
  const sampling = await campus.locator('img.scene-image').first().evaluate((img) => ({
    source: img.naturalWidth,
    zoomedDeviceWidth: Math.round(img.getBoundingClientRect().width * devicePixelRatio),
    dpr: devicePixelRatio,
  }))
  assert.equal(sampling.dpr, 3)
  assert.ok(sampling.source + 2 >= sampling.zoomedDeviceWidth, `Zoom raster sampling ${JSON.stringify(sampling)}`)
  console.log(`[DPR 3] map ${sampling.source} source px across ${sampling.zoomedDeviceWidth} display px`)
  await close()
  await expectOverview()

  await page.setViewportSize({ width: 390, height: 700 })
  await page.evaluate(() => window.campusTestHost.viewport(700))

  await page.emulateMedia({ reducedMotion: 'reduce' })
  await open('schedule')
  assert.ok(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches))
  assert.equal(await cameraScale(), 1, 'Reduced-motion entry keeps the overview scale')
  await room.getByRole('button', { name: 'День', exact: true }).click()
  await room.getByRole('button', { name: 'Суббота, 28 ноября' }).click()
  await room.getByText('Занятий нет').waitFor()
  await page.keyboard.press('Escape')
  await room.waitFor({ state: 'hidden' })

  await open('work')
  await room.getByRole('button', { name: 'Просрочено', exact: true }).click()
  await room.getByRole('button', { name: /Отчёт по лабораторной работе/ }).waitFor()
  await room.getByRole('button', { name: 'Сдано', exact: true }).click()
  await room.getByRole('button', { name: /Таблицы и формулы/ }).click()
  const classroomLink = room.getByRole('link', { name: 'Открыть Google Classroom' })
  assert.equal(await classroomLink.getAttribute('href'), 'https://classroom.google.com/')
  await assertWithinScroller(room, classroomLink, 'Homework lower action')
  await page.locator('#back').click()
  assert.equal(await room.getByRole('button', { name: 'Сдано', exact: true }).getAttribute('aria-pressed'), 'true')
  await close()

  await selectDemo('teacher', 'teacher', 'day', 'ready', false)
  assert.equal(await place('homeroom').count(), 0, 'Homeroom hidden for an unassigned teacher')
  await selectDemo('teacher', 'teacher', 'day')
  await open('work')
  await room.getByRole('button', { name: /8Б · математика/ }).click()
  await room.getByRole('button', { name: 'Аналитика', exact: true }).click()
  await room.getByText('7 из 8', { exact: true }).waitFor()
  await room.getByRole('button', { name: 'Состав', exact: true }).click()
  await room.getByRole('button', { name: /Кира Смирнова/ }).click()
  await room.getByRole('heading', { name: 'Индивидуальные результаты' }).waitFor()
  await page.locator('#back').click()
  await page.locator('#back').click()
  await close()
  await open('results')
  await room.getByRole('button', { name: /8Б · математика/ }).click()
  await room.getByRole('link', { name: 'Открыть Google Classroom' }).waitFor()
  await page.locator('#back').click()
  await close()
  await selectDemo('middle', 'student', 'day')

  await selectDemo('middle', 'student', 'day', 'empty')
  await open('work')
  await room.getByText('Данных пока нет').waitFor()
  await close()

  await selectDemo('middle', 'student', 'day', 'error')
  await open('work')
  await room.getByRole('alert').waitFor()
  await room.getByRole('button', { name: 'Повторить загрузку' }).click()
  await room.getByRole('button', { name: 'К сдаче', exact: true }).click()
  await room.getByRole('button', { name: /Системы линейных уравнений/ }).waitFor()
  await close()

  await selectDemo('middle', 'student', 'day', 'loading')
  await open('work')
  await room.getByRole('status').getByText('Загружаем данные').waitFor()
  await close()

  await selectDemo('middle', 'student', 'day', 'unpublished')
  await open('schedule')
  await room.getByRole('status').getByText('Неделя не опубликована').waitFor()
  await close()

  const clockPage = await browser.newPage({ timezoneId: 'Europe/Moscow', reducedMotion: 'reduce' })
  try {
    if (offline) await clockPage.context().setOffline(true)
    await clockPage.clock.install({ time: new Date('2026-11-28T19:59:50+03:00') })
    await clockPage.goto(base)
    const current = clockPage.locator('.campus')
    assert.equal(await current.getAttribute('data-theme'), 'day')
    await clockPage.clock.fastForward(11_000)
    await clockPage.waitForFunction(() => document.querySelector('.campus')?.dataset.theme === 'night')
  } finally {
    await clockPage.close()
  }

  assert.deepEqual(errors, [], 'Browser runtime errors')
  console.log('[OK] touch swipe, nested and browser back, keyboard/list, Telegram BackButton, rapid taps, interrupted entry, reduced motion, data states and timed night boundary')
}

try {
  if (!offline && !process.env.CAMPUS_TARGETS_ONLY) {
    await verifyBrowserOverview()
    await verifySmallerBrowser()
  }
  if (!process.env.CAMPUS_TARGETS_ONLY) await verifyNightLoad()
  if (!process.env.CAMPUS_WEBKIT) await verifySimulatedTelegram()
} finally {
  await browser.close()
}
