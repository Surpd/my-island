import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const children = new Set();
let stopping;
let requestedExitCode = 0;

function waitForClose(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null || child.signalCode !== null) return resolve();
    child.once('close', resolve);
  });
}

async function stop(code = 0) {
  requestedExitCode = Math.max(requestedExitCode, code);
  if (stopping) return stopping;
  stopping = (async () => {
    const running = [...children].filter((child) => child.exitCode === null && child.signalCode === null);
    for (const child of running) child.kill('SIGTERM');
    // Give servers a chance to close listeners and flush their output.
    const closed = Promise.all(running.map(waitForClose));
    let graceTimer;
    const grace = new Promise((resolve) => { graceTimer = setTimeout(resolve, 5000); });
    await Promise.race([closed, grace]);
    clearTimeout(graceTimer);
    for (const child of running) {
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    }
    await Promise.all(running.map(waitForClose));
    process.exitCode = requestedExitCode;
  })();
  return stopping;
}

function start(command, args, env, label) {
  const child = spawn(command, args, { cwd: root, env, stdio: 'inherit', windowsHide: true });
  children.add(child);
  child.on('error', (error) => {
    console.error(`${label} failed to start: ${error.message}`);
    void stop(1);
  });
  child.on('close', (code, signal) => {
    children.delete(child);
    if (!stopping) void stop(code === 0 && !signal ? 1 : code ?? 1);
  });
  return child;
}

const testProfile = process.env.MY_ISLAND_TEST_PROFILE;
if (testProfile && !['manual', 'e2e', 'schedule'].includes(testProfile)) {
  throw new Error('MY_ISLAND_TEST_PROFILE must be manual, e2e or schedule');
}
const schoolMode = !testProfile;
const testApiPort = Number(process.env.MY_ISLAND_TEST_API_PORT || '8011');
const testFrontendPort = Number(process.env.MY_ISLAND_TEST_FRONTEND_PORT || '3011');
if (!schoolMode && (!Number.isInteger(testApiPort) || testApiPort < 1024 || !Number.isInteger(testFrontendPort) || testFrontendPort < 1024)) {
  throw new Error('Test API and frontend ports must be valid unprivileged ports');
}
const localEnv = {
  ...process.env,
  APP_ENV: 'development',
  DEV_AUTH_ENABLED: schoolMode ? 'false' : 'true',
  MY_ISLAND_LOCAL_TEST_MODE: schoolMode ? 'false' : 'true',
  MY_ISLAND_SCHOOL_WORKSPACE: schoolMode ? '1' : '',
  MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE: schoolMode ? '1' : '',
  VITE_API_BASE_URL: `http://127.0.0.1:${schoolMode ? 8000 : testApiPort}`,
  VITE_SCHOOL_DIRECTORY_MODE: schoolMode ? 'school' : 'demo',
  VITE_DATA_SOURCE_LABEL: schoolMode ? 'Школьная база · Supabase' : 'Тестовая база · SQLite',
  VITE_READ_ONLY_SCHOOL_WORKSPACE: schoolMode ? 'true' : 'false',
  SCHEDULE_BACKEND: 'canonical',
  CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV: 'false',
  CLOUDFLARE_INCLUDE_PROCESS_ENV: 'false',
};
if (schoolMode && process.env.DATABASE_URL) localEnv.DATABASE_URL = process.env.DATABASE_URL;
else delete localEnv.DATABASE_URL;
if (schoolMode) delete localEnv.MY_ISLAND_TEST_PROFILE;
if (schoolMode) {
  console.log('My Island local school mode: http://localhost:3000');
  console.log('Source: approved Supabase project; school records are read-only and Telegram admin code is required.');
  start('python', ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000'], localEnv, 'School API');
} else {
  console.log(`My Island isolated ${testProfile} test mode: http://localhost:${testFrontendPort}`);
  console.log('Source: marked .local-test SQLite; test role links are enabled only on loopback.');
  localEnv.MY_ISLAND_TEST_API_PORT = String(testApiPort);
  localEnv.MY_ISLAND_TEST_FRONTEND_PORT = String(testFrontendPort);
  start('python', ['-m', 'backend.scripts.local_test_env', 'api'], localEnv, 'Local test API');
}
start(process.execPath, [
  path.join(root, 'node_modules', 'vinext', 'dist', 'cli.js'),
  'dev', '--hostname', '127.0.0.1', '--port', String(schoolMode ? 3000 : testFrontendPort),
], localEnv, 'Frontend');

process.once('SIGINT', () => void stop(130));
process.once('SIGTERM', () => void stop(143));
