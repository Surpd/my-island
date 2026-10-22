// Optional visual review of the normal local school. The user performs login.
// Keep this session separate from test profiles and never export its tokens.
import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
const output = path.resolve('.local-test/private/school-ui-review');
await mkdir(output, { recursive: true });
const context = await chromium.launchPersistentContext(path.resolve('.local-test/private/school-review-profile'), {
  headless: false, viewport: { width: 1366, height: 768 },
  args: ['--remote-debugging-address=127.0.0.1', '--remote-debugging-port=9333'],
});
const page = context.pages()[0] || await context.newPage();
page.setDefaultTimeout(20_000);
await page.goto('http://localhost:3000/admin/people');
await page.locator('.admin-login-card,.school-group-card,.admin-error').first().waitFor({ timeout: 60_000 }).catch(() => {});
await page.screenshot({ path: path.join(output, '01-school-entry.png'), timeout: 15_000 });
console.log('Normal school UI open on localhost:3000. User login is required. Read-only visual review on loopback CDP 9333.');
await new Promise(resolve => context.on('close', resolve));
