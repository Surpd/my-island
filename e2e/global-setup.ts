import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import type { FullConfig } from '@playwright/test';

export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0]?.use.baseURL;
  const parsed = new URL(String(baseURL || ''));
  if (parsed.protocol !== 'http:' || parsed.hostname !== 'localhost' || parsed.port !== '3011') {
    throw new Error('E2E stopped: frontend target must be exactly http://localhost:3011 (isolated test port)');
  }
  const root = resolve(process.cwd());
  const runner = ['-m', 'backend.scripts.local_test_env'];
  const env = { ...process.env, MY_ISLAND_TEST_PROFILE: 'e2e', MY_ISLAND_TEST_API_PORT: '8011', MY_ISLAND_TEST_FRONTEND_PORT: '3011' };
  // This preflight validates local-test mode, loopback assumptions, absent
  // integrations, and the exact marked SQLite path before resetting anything.
  execFileSync('python', [...runner, 'preflight'], { cwd: root, stdio: 'inherit', env });
  execFileSync('python', [...runner, 'reset'], { cwd: root, stdio: 'inherit', env });
}
