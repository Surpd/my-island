import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const { chromium } = await import(process.env.CAMPUS_PLAYWRIGHT
  ? pathToFileURL(process.env.CAMPUS_PLAYWRIGHT).href
  : 'playwright')

const base = process.env.CAMPUS_URL || 'http://127.0.0.1:4176'
const offline = new URL(base).protocol === 'file:'
const shots = process.env.CAMPUS_SHOTS
const worlds = ['junior', 'middle', 'senior', 'graduate']
const viewports = process.env.CAMPUS_FOCUS_ONLY ? [] : [
  [320, 640], [360, 640], [390, 844], [430, 932],
  [320, 640, 490], [390, 844, 560],
]
const routes = ['schedule', 'work', 'results', 'news', 'profile']
const browser = await chromium.launch(
  process.env.CAMPUS_CHROME ? { executablePath: process.env.CAMPUS_CHROME } : {},
)

if (shots) await mkdir(shots, { recursive: true })

try {
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
    deviceScaleFactor: 3, timezoneId: 'Europe/Moscow',
  })
  if (offline) await page.context().setOffline(true)
  await page.clock.setFixedTime(new Date('2026-11-28T09:00:00+03:00'))
  page.setDefaultTimeout(10000)
  const errors = []
  let routeChecks = 0
  const targetFailures = []
  page.on('pageerror', (error) => errors.push(error.message))

  await page.addInitScript(() => {
    const state = { shown: false, callbacks: new Set(), events: new Map(),
      top: 0, bottom: 0, height: 0, active: true }
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
      press: () => state.callbacks.forEach((callback) => callback()),
      viewport(height, top = 0, bottom = 0) {
        Object.assign(state, { height, top, bottom })
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
        get contentSafeAreaInset() { return { top: 0, bottom: 0, left: 0, right: 0 } },
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
    await place(id).tap()
    assert.equal(await campus.getAttribute('data-travel'), id, `Touch on ${id} starts its route`)
    await room.waitFor({ state: 'visible' })
  }
  const close = async () => {
    await page.locator('#back').click()
    await room.waitFor({ state: 'hidden' })
  }
  const snapshot = async (name) => {
    if (!shots) return
    await page.evaluate(async () => {
      await document.fonts.ready
      await Promise.all(Array.from(document.images, (image) => image.complete && image.naturalWidth ? image.decode() : Promise.resolve()))
    })
    await page.screenshot({ path: join(shots, `${name}.png`), animations: 'disabled', scale: 'css' })
  }
  const assertScene = async (theme) => {
    const selector = theme === 'night' ? 'img.scene-night' : 'img.scene-image:not(.scene-night)'
    await page.waitForFunction((selector) => {
      const image = document.querySelector(selector)
      return image?.complete && image.naturalWidth > 0 && image.naturalHeight > 0
    }, selector)
  }
  const assertNoOverflow = async (label) => {
    const dimensions = await page.evaluate(() => {
      const modal = document.querySelector('dialog#room[open]')
      const view = document.documentElement
      return {
        viewportOverflow: view.scrollWidth - view.clientWidth,
        panelOverflow: modal ? modal.scrollWidth - modal.clientWidth : 0,
      }
    })
    assert.ok(dimensions.viewportOverflow < 2 && dimensions.panelOverflow < 2,
      `${label} overflow ${JSON.stringify(dimensions)}`)
  }
  const cameraScale = () => page.locator('.camera').evaluate((element) =>
    new DOMMatrixReadOnly(getComputedStyle(element).transform).a)
  const prefs = page.locator('dialog#preferences')
  const openSettings = () => page.locator('#settings').tap()
  const closeSettings = async () => {
    await prefs.getByRole('button', { name: 'Закрыть настройки' }).tap()
    await prefs.waitFor({ state: 'hidden' })
  }
  const expectCampus = (attributes) => page.waitForFunction((values) => {
    const island = document.querySelector('.campus')
    return Object.entries(values).every(([key, value]) => island?.dataset[key] === value)
  }, attributes)
  const selectDemo = async (world, role, theme) => {
    await openSettings()
    await page.locator('#role').selectOption(role)
    if (role === 'student') await page.locator('#age-group').selectOption(world)
    if (theme) await page.locator('#lighting').selectOption(theme)
    await closeSettings()
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
    assert.ok(await markers.count() >= 5, `${label} has the main locations`)
    const problems = await markers.evaluateAll((buttons, viewport) => buttons.flatMap((button) => {
      const { left, top, right, bottom, width, height } = button.getBoundingClientRect()
      const id = button.dataset.location
      const issues = []
      if (width < 43.5 || height < 43.5) issues.push(`${id} ${width.toFixed(1)}×${height.toFixed(1)}`)
      if (left < -1 || right > innerWidth + 1 || top < viewport.top - 1 || bottom > viewport.height - viewport.bottom + 1) {
        issues.push(`${id} outside usable viewport (${top.toFixed(0)}–${bottom.toFixed(0)})`)
      }
      const hit = document.elementFromPoint(left + width / 2, top + height / 2)
      if (!button.contains(hit)) issues.push(`${id} center covered by ${hit?.outerHTML.slice(0, 100)}`)
      for (const other of buttons.slice(buttons.indexOf(button) + 1)) {
        const otherRect = other.getBoundingClientRect()
        if (Math.min(right, otherRect.right) - Math.max(left, otherRect.left) > 1 &&
            Math.min(bottom, otherRect.bottom) - Math.max(top, otherRect.top) > 1) {
          issues.push(`${id}/${other.dataset.location} overlap`)
        }
      }
      return issues
    }), { height, top, bottom })
    if (problems.length) targetFailures.push(`${label} — ${problems.join(', ')}`)
  }

  await page.goto(base)
  if (shots && process.env.CAMPUS_TOUR) {
    const device = await page.context().newCDPSession(page)
    const density = (deviceScaleFactor) => device.send('Emulation.setDeviceMetricsOverride', {
      width: 390, height: 844, mobile: true, deviceScaleFactor,
    })
    await density(2)
    await page.reload()
    assert.equal(await page.evaluate(() => devicePixelRatio), 2)
    await selectDemo('junior', 'student', 'day')
    let frame = 0
    const takeFrames = async (count) => {
      for (let n = 0; n < count; n++) {
        await page.screenshot({ path: join(shots, `tour-${String(frame++).padStart(3, '0')}.png`), scale: 'css' })
        await page.waitForTimeout(45)
      }
    }
    await takeFrames(7)
    await openSettings()
    await takeFrames(7)
    await page.locator('#age-group').selectOption('graduate')
    await closeSettings()
    await takeFrames(9)
    await openSettings()
    await page.locator('#lighting').selectOption('night')
    await closeSettings()
    await takeFrames(9)
    await assertScene('night')
    await snapshot('390-graduate-night')
    await place('schedule').dispatchEvent('click')
    await takeFrames(12)
    await room.waitFor({ state: 'visible' })
    await takeFrames(8)
    await snapshot('390-graduate-night-schedule')
    await close()
    await takeFrames(10)
    await density(3)
    await device.detach()
  }
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
      await assertTargets(label, compact || height, top, bottom)
      if (!compact && width === 390) {
        await assertScene('day')
        await selectDemo(world, role, 'night')
        await assertScene('night')
        await assertNoOverflow(`${label} / night`)
        await assertTargets(`${label} / night`, height, 0, 0)
        await selectDemo(world, role, 'day')
      }
      if (!compact && width === 390) await snapshot(`390-${world}-day`)
      if (!compact && width === 320 && (world === 'junior' || role === 'teacher')) await snapshot(`320-${world}-day`)
      if (compact === 490 && (world === 'middle' || role === 'teacher')) await snapshot(`320-compact-${world}-day`)
      for (const route of role === 'teacher' ? [...routes, 'homeroom'] : routes) {
        await open(route)
        assert.ok(await room.locator('.ci-interior').isVisible(), label)
        await assertNoOverflow(`${label} / ${route}`)
        if (width === 390 && !compact && route === 'schedule' && role === 'student') {
          const currentLesson = { junior: 'Математика', middle: 'Алгебра', senior: 'Алгебра и начала анализа', graduate: 'Алгебра и начала анализа' }
          await room.getByRole('button', { name: new RegExp(`По расписанию сейчас.*${currentLesson[world]}`) }).waitFor()
        }
        if (width === 390 && !compact && route === 'schedule' && (world === 'middle' || role === 'teacher')) {
          await snapshot(`390-${world}-schedule`)
        }
        await close()
        routeChecks += 1
      }
    }
  }
  if (routeChecks) console.log(`[${routeChecks}] world × viewport × building openings and touch targets checked`)

  await page.evaluate(() => window.campusTestHost.viewport(700))
  await page.setViewportSize({ width: 390, height: 700 })
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await selectDemo('middle', 'student', 'day')
  await openSettings()
  await page.locator('#lighting').selectOption('auto')
  await page.locator('#age-group').selectOption('graduate')
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
  const roomBox = await room.boundingBox()
  assert.ok(roomBox.y >= 25 && roomBox.y + roomBox.height <= 491, 'Panel respects compact safe area')
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
  assert.ok(await weekend.isVisible(), 'Weekly agenda has a scrollable final day')
  assert.ok(await room.evaluate((element) =>
    [element, ...element.querySelectorAll('*')].some((child) => child.scrollTop > 0)), 'Panel visibly scrolled')
  await close()

  await open('results')
  await room.getByRole('button', { name: /Алгебра/ }).click()
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
  assert.ok(await cameraScale() > 1.01, 'Camera zoomed toward the selected building at 390px DPR 3')
  await room.getByRole('button', { name: /Лаборатория по физике/ }).waitFor()
  const sampling = await campus.locator('img.scene-image').first().evaluate((img) => ({
    source: img.naturalWidth,
    zoomedDeviceWidth: Math.round(img.getBoundingClientRect().width * devicePixelRatio),
    dpr: devicePixelRatio,
  }))
  assert.equal(sampling.dpr, 3)
  assert.ok(sampling.source + 2 >= sampling.zoomedDeviceWidth, `Zoom raster sampling ${JSON.stringify(sampling)}`)
  const detail = room.locator('.room-detail img')
  const detailSampling = await detail.evaluate(async (img) => {
    await img.decode()
    return {
      source: img.naturalWidth,
      deviceWidth: Math.round(img.getBoundingClientRect().width * devicePixelRatio),
    }
  })
  assert.ok(detailSampling.source >= detailSampling.deviceWidth, `Detail raster sampling ${JSON.stringify(detailSampling)}`)
  console.log(`[DPR 3] map ${sampling.source} source px across ${sampling.zoomedDeviceWidth} display px, detail ${detailSampling.source} across ${detailSampling.deviceWidth}`)
  if (shots) await page.screenshot({ path: join(shots, '390-motion-panel-dpr3.png'), scale: 'device' })
  await close()
  await expectOverview()

  await page.setViewportSize({ width: 430, height: 932 })
  await page.evaluate(() => window.campusTestHost.viewport(932))
  await place('news').tap()
  const inspection = await page.waitForFunction(() => {
    const lens = document.querySelector('.arrival-lens')
    const image = lens?.querySelector('img')
    if (!image?.complete || !image.naturalWidth || Number(getComputedStyle(lens).opacity) < 0.75) return false
    const bounds = image.getBoundingClientRect()
    const stage = lens.closest('.stage').getBoundingClientRect()
    return {
      native: Math.min(image.naturalWidth, image.naturalHeight),
      display: Math.ceil(Math.max(bounds.width, bounds.height) * devicePixelRatio),
      inside: bounds.left >= stage.left && bounds.right <= stage.right &&
        bounds.top >= stage.top && bounds.bottom <= stage.bottom,
      roomOpen: document.querySelector('#room').open,
      dpr: devicePixelRatio,
    }
  }).then((handle) => handle.jsonValue())
  assert.equal(inspection.dpr, 3)
  assert.ok(inspection.inside && !inspection.roomOpen, 'Inspection lens is visible within the map before the panel')
  assert.ok(inspection.native >= inspection.display, `Inspection lens sampling ${JSON.stringify(inspection)}`)
  console.log(`[DPR 3] inspection lens ${inspection.native} source px across ${inspection.display} display px at 430px`)
  await room.waitFor({ state: 'visible' })
  await close()
  await expectOverview()
  await page.setViewportSize({ width: 390, height: 700 })
  await page.evaluate(() => window.campusTestHost.viewport(700))

  await page.emulateMedia({ reducedMotion: 'reduce' })
  await open('schedule')
  assert.equal(await campus.locator('.arrival-lens').count(), 0, 'Reduced motion skips the inspection lens')
  assert.ok(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches))
  assert.equal(await cameraScale(), 1, 'Reduced-motion entry keeps the overview scale')
  await room.getByRole('button', { name: 'Суббота, 28 ноября' }).click()
  await room.getByText('Занятий нет').waitFor()
  await page.keyboard.press('Escape')
  await room.waitFor({ state: 'hidden' })

  await open('work')
  await room.getByRole('button', { name: 'Просрочено', exact: true }).click()
  await room.getByRole('button', { name: /Отчёт по лабораторной работе/ }).waitFor()
  await room.getByRole('button', { name: 'Сдано', exact: true }).click()
  await room.getByRole('button', { name: /Таблицы и формулы/ }).click()
  assert.equal(await room.getByRole('link', { name: 'Открыть Google Classroom' }).getAttribute('href'), 'https://classroom.google.com/')
  await page.locator('#back').click()
  await close()

  await selectDemo('teacher', 'teacher', 'day')
  await openSettings()
  await page.locator('#homeroom').uncheck()
  await closeSettings()
  assert.equal(await place('homeroom').count(), 0, 'Homeroom hidden for an unassigned teacher')
  await openSettings()
  await page.locator('#homeroom').check()
  await closeSettings()
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

  await openSettings()
  await page.locator('#data-state').selectOption('empty')
  await closeSettings()
  await open('work')
  await room.getByText('Данных пока нет').waitFor()
  await close()

  await openSettings()
  await page.locator('#data-state').selectOption('error')
  await closeSettings()
  await open('work')
  await room.getByRole('alert').waitFor()
  await room.getByRole('button', { name: 'Повторить загрузку' }).click()
  await room.getByRole('button', { name: /Системы линейных уравнений/ }).waitFor()
  await close()

  await openSettings()
  await page.locator('#data-state').selectOption('loading')
  await closeSettings()
  await open('work')
  await room.getByRole('status').getByText('Загружаем данные').waitFor()
  await close()

  await openSettings()
  await page.locator('#data-state').selectOption('unpublished')
  await closeSettings()
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
  assert.deepEqual(targetFailures, [], 'Visible touch targets have no overlap and fit the usable viewport')
  console.log('[OK] touch swipe, nested and browser back, keyboard/list, Telegram BackButton, rapid taps, interrupted entry, reduced motion, data states and timed night boundary')
} finally {
  await browser.close()
}
