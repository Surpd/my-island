import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import { chromium } from 'playwright'

const base = process.env.CAMPUS_URL || 'http://127.0.0.1:4176'
const shots = process.env.CAMPUS_SHOTS
const concepts = ['tide', 'press', 'orbit']
const roles = ['student', 'teacher']
const widths = process.env.CAMPUS_FOCUS_ONLY ? [] : [360, 390, 430, 1366]
const routes = ['schedule', 'work', 'results', 'news', 'profile']
const browser = await chromium.launch(
  process.env.CAMPUS_CHROME ? { executablePath: process.env.CAMPUS_CHROME } : {},
)

if (shots) await mkdir(shots, { recursive: true })

try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
  page.setDefaultTimeout(10000)
  const errors = []
  let routeChecks = 0
  page.on('pageerror', (error) => errors.push(error.message))

  await page.addInitScript(() => {
    const state = { shown: false, callbacks: new Set() }
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
    }
    window.Telegram = {
      WebApp: { BackButton, ready() {}, expand() {}, onEvent() {}, offEvent() {} },
    }
  })

  const room = page.locator('dialog#room')
  const campus = page.locator('[data-theme][data-role]')
  const place = (id) => campus.locator(`button[data-location="${id}"]`).filter({ visible: true }).first()
  const open = async (id) => {
    await place(id).click()
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
      await Promise.all(Array.from(document.images, (image) => image.decode()))
    })
    await page.screenshot({ path: join(shots, `${name}.png`), animations: 'disabled' })
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
  const selectDemo = async (concept, role) => {
    await page.locator('#concept').selectOption(concept)
    await page.locator('#role').selectOption(role)
    await page.locator(`[data-theme="${concept}"][data-role="${role}"]`).waitFor({ state: 'visible' })
  }
  const openSettings = () => page.locator('#settings').click()
  const closeSettings = async () => {
    await page.keyboard.press('Escape')
    await page.locator('dialog#preferences').waitFor({ state: 'hidden' })
  }

  await page.goto(base)
  if (process.env.CAMPUS_FAST) await page.emulateMedia({ reducedMotion: 'reduce' })
  for (const width of widths) {
    await page.setViewportSize({ width, height: width > 800 ? 900 : 844 })
    for (const concept of concepts) {
      for (const role of roles) {
        await selectDemo(concept, role)
        await assertNoOverflow(`${width} / ${concept} / ${role} island`)
        if (width === 390 || width === 1366) await snapshot(`${width}-${concept}-${role}-island`)

        for (const route of routes) {
          const label = `${width} / ${concept} / ${role} / ${route}`
          await open(route)
          assert.ok(await room.locator('.ci-interior').isVisible(), label)
          await assertNoOverflow(label)
          if (width === 390 && route === 'schedule') await snapshot(`${width}-${concept}-${role}-schedule`)
          await close()
          routeChecks += 1
        }

        if (role === 'teacher') {
          await openSettings()
          const homeroom = page.locator('#homeroom')
          await homeroom.uncheck()
          assert.equal(await place('homeroom').count(), 0, 'Homeroom hidden for an unassigned teacher')
          await homeroom.check()
          await closeSettings()
          await open('homeroom')
          assert.ok(await room.getByRole('heading', { name: '8Б', exact: true }).isVisible())
          await assertNoOverflow(`${width} / ${concept} / teacher / homeroom`)
          await close()
          routeChecks += 1
        }
      }
    }
  }
  if (routeChecks) console.log(`[${routeChecks}] concept × role × viewport building openings checked`)

  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.setViewportSize({ width: 390, height: 700 })
  await selectDemo('tide', 'student')
  await open('schedule')
  await room.getByRole('button', { name: 'Неделя', exact: true }).click()
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
  await page.waitForFunction(() => new DOMMatrixReadOnly(getComputedStyle(document.querySelector('.camera')).transform).a < 1.01)
  await place('schedule').dispatchEvent('click')
  await page.waitForFunction(() => new DOMMatrixReadOnly(getComputedStyle(document.querySelector('.camera')).transform).a > 1.01)
  assert.ok(!(await room.isVisible()), 'Camera travels before the room opens')
  await page.keyboard.press('Escape')
  await room.waitFor({ state: 'hidden' })
  await open('news')
  assert.ok(await cameraScale() > 1.15, 'Camera zoomed toward the selected building')
  await room.getByRole('button', { name: /Лаборатория по физике/ }).waitFor()
  await close()
  await page.waitForFunction(() => new DOMMatrixReadOnly(getComputedStyle(document.querySelector('.camera')).transform).a < 1.01)

  await page.emulateMedia({ reducedMotion: 'reduce' })
  await open('schedule')
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

  await selectDemo('tide', 'teacher')
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
  await selectDemo('tide', 'student')

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

  assert.deepEqual(errors, [], 'Browser runtime errors')
  console.log('[OK] scroll, nested and browser back, keyboard/list, Telegram BackButton, interrupted entry, reduced motion, states')
} finally {
  await browser.close()
}
