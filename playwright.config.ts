import { defineConfig, devices } from '@playwright/test';

const frontendPort = 3011;
const apiPort = 8011;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 12_000 },
  globalSetup: './e2e/global-setup.ts',
  reporter: 'line',
  outputDir: '.local-test/playwright-results',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: `http://localhost:${frontendPort}`,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  webServer: {
    command: 'node scripts/dev-local.mjs',
    env: { MY_ISLAND_TEST_PROFILE: 'e2e', MY_ISLAND_TEST_API_PORT: String(apiPort), MY_ISLAND_TEST_FRONTEND_PORT: String(frontendPort) },
    url: `http://localhost:${frontendPort}`,
    timeout: 180_000,
    reuseExistingServer: process.env.MY_ISLAND_E2E_REUSE_SERVER === '1',
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
