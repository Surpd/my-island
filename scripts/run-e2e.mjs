import { cp, mkdir, lstat, readlink, readdir, rm, stat, symlink, unlink } from 'node:fs/promises';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const workspace = path.join(projectRoot, '.local-test', 'e2e-workspace');
const frontendPort = 3011;
const apiPort = 8011;
const reuseExistingServer = process.env.MY_ISLAND_E2E_REUSE_SERVER === '1';
const execFileAsync = promisify(execFile);
const ownedServerEnv = {
  ...process.env,
  MY_ISLAND_TEST_PROFILE: 'e2e',
  MY_ISLAND_CONSOLIDATED_SCHEDULE: '1',
  MY_ISLAND_TEST_FRONTEND_PORT: String(frontendPort),
  MY_ISLAND_TEST_API_PORT: String(apiPort),
};
delete ownedServerEnv.MY_ISLAND_E2E_REUSE_SERVER;
const ignored = new Set([
  '.git', '.local-test', 'node_modules', '.next', '.vinext', '.wrangler', 'dist', 'data', 'test-results',
  'archive', 'artifacts', 'docs', 'site-scaffold', '.playwright-mcp',
  '.aws', '.venv', 'venv', '.secrets', 'secrets', 'certs',
  '.release-worktree', '.openai',
  'schedule-studio-draft-inspector.png', 'schedule-studio-main.png',
]);
function shouldCopy(source) {
  const name = path.basename(source);
  const lower = name.toLowerCase();
  return !ignored.has(name)
    && !lower.startsWith('.sites-') && !lower.startsWith('.site-')
    && !lower.endsWith('.tar')
    && name !== '.env' && !name.startsWith('.env.')
    && !name.startsWith('.dev.vars')
    && !lower.startsWith('credentials') && !lower.includes('-credentials.')
    && !lower.startsWith('service-account')
    && !lower.endsWith('.pem') && !lower.endsWith('.key');
}

async function assertPortAvailable(port) {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', resolve);
  });
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}

async function isListening(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const finish = (value) => { socket.destroy(); resolve(value); };
    socket.setTimeout(500);
    socket.once('connect', () => finish(true));
    socket.once('timeout', () => finish(false));
    socket.once('error', () => finish(false));
  });
}

async function waitForChildClose(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  let timer;
  await Promise.race([
    new Promise((resolve) => child.once('close', resolve)),
    new Promise((resolve) => { timer = setTimeout(resolve, timeoutMs); }),
  ]);
  clearTimeout(timer);
}

async function waitForOwnedServer(server) {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    if (signalSent) throw new Error(`Interrupted while starting the isolated E2E server (${signalSent})`);
    if (server.spawnError) throw new Error(`Isolated E2E server failed to spawn: ${server.spawnError.message}`);
    if (server.exitCode !== null || server.signalCode !== null) {
      throw new Error(`Isolated E2E server exited before readiness (code ${server.exitCode}, signal ${server.signalCode})`);
    }
    const [apiReady, frontendReady] = await Promise.all([isListening(apiPort), isListening(frontendPort)]);
    if (apiReady && frontendReady) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Isolated E2E server did not become ready on ${apiPort} and ${frontendPort} within 180 seconds`);
}

/** @returns {Promise<void>} */
async function stopWindowsTree(child, label) {
  if (!child?.pid) return;
  try {
    await execFileAsync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
      windowsHide: true,
      timeout: 15_000,
    });
  } catch (error) {
    if (child.exitCode === null && child.signalCode === null) {
      throw new Error(`Could not stop ${label} process tree: ${error.message}`);
    }
  }
}

async function stopOwnedServer(server) {
  if (!server?.pid) return;
  if (process.platform === 'win32') {
    // Playwright cannot clean up this process tree on Windows: its SIGTERM
    // kills only the webServer parent, leaving API/Wrangler descendants alive.
    try { await stopWindowsTree(server, 'isolated E2E server'); }
    catch (error) {
      const [apiListening, frontendListening] = await Promise.all([isListening(apiPort), isListening(frontendPort)]);
      if (apiListening || frontendListening) {
        throw error;
      }
    }
  } else if (server.exitCode === null && server.signalCode === null) {
    server.kill('SIGTERM');
  }

  if (server.exitCode === null && server.signalCode === null) {
    await waitForChildClose(server, 10_000);
  }
  if (server.exitCode === null && server.signalCode === null) {
    server.kill('SIGKILL');
    await waitForChildClose(server, 5_000);
  }
  if (server.exitCode === null && server.signalCode === null) {
    throw new Error(`Isolated E2E server process ${server.pid} did not exit after cleanup`);
  }
}

if (!reuseExistingServer) {
  await assertPortAvailable(apiPort).catch((error) => {
    if (error.code === 'EADDRINUSE') throw new Error(`E2E API port ${apiPort} is already occupied. Stop that server or set MY_ISLAND_E2E_REUSE_SERVER=1 to explicitly reuse it.`);
    throw error;
  });
  await assertPortAvailable(frontendPort).catch((error) => {
    if (error.code === 'EADDRINUSE') throw new Error(`E2E frontend port ${frontendPort} is already occupied. Stop that server or set MY_ISLAND_E2E_REUSE_SERVER=1 to explicitly reuse it.`);
    throw error;
  });
}

await mkdir(workspace, { recursive: true });
for (const entry of await readdir(workspace)) {
  if (entry === '.env' || entry.startsWith('.env.') || entry.startsWith('.dev.vars')) {
    await rm(path.join(workspace, entry), { force: true, recursive: true });
  }
}
for (const entry of await readdir(projectRoot, { withFileTypes: true })) {
  if (!shouldCopy(path.join(projectRoot, entry.name))) continue;
  await cp(path.join(projectRoot, entry.name), path.join(workspace, entry.name), {
    recursive: true,
    force: true,
    filter: shouldCopy,
  });
}
// The Vite config imports this non-secret hosting descriptor. Copy this file
// explicitly so a fresh workspace works without copying other .openai state.
await mkdir(path.join(workspace, '.openai'), { recursive: true });
await cp(path.join(projectRoot, '.openai', 'hosting.json'), path.join(workspace, '.openai', 'hosting.json'));

const modules = path.join(workspace, 'node_modules');
const sourceModules = path.join(projectRoot, 'node_modules');
try {
  const details = await lstat(modules);
  if (details.isSymbolicLink()) {
    const rawTarget = await readlink(modules);
    const junctionTarget = path.resolve(path.dirname(modules), rawTarget).replace(/[\\/]+$/u, '').toLowerCase();
    const expectedTarget = path.resolve(sourceModules).replace(/[\\/]+$/u, '').toLowerCase();
    if (junctionTarget !== expectedTarget) {
      throw new Error(`Refusing to remove unexpected node_modules junction: ${modules} -> ${rawTarget}`);
    }
    // Remove only the verified junction entry. Never recurse through it or touch its target.
    await unlink(modules);
  } else if (!details.isDirectory()) {
    throw new Error(`${modules} exists and is not a directory`);
  }
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}

await mkdir(modules, { recursive: true });
const cacheEntries = new Set(['.vite', '.vite-temp', '.cache', '.mf']);
for (const entry of await readdir(sourceModules, { withFileTypes: true })) {
  if (cacheEntries.has(entry.name)) continue;
  const source = path.join(sourceModules, entry.name);
  const destination = path.join(modules, entry.name);
  try {
    const existing = await lstat(destination);
    if (!existing.isSymbolicLink()) {
      throw new Error(`${destination} exists and is not a package junction`);
    }
    const rawTarget = await readlink(destination);
    const actualTarget = path.resolve(path.dirname(destination), rawTarget).replace(/[\\/]+$/u, '').toLowerCase();
    const expectedTarget = path.resolve(source).replace(/[\\/]+$/u, '').toLowerCase();
    if (actualTarget !== expectedTarget) {
      throw new Error(`Unexpected package junction: ${destination} -> ${rawTarget}`);
    }
    continue;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const sourceDetails = await stat(source);
  if (!sourceDetails.isDirectory()) continue;
  await symlink(source, destination, 'junction');
}

console.log(`Running Playwright from isolated workspace: ${workspace}`);
let ownedServer;
let runner;
/** @type {Promise<void> | undefined} */
let runnerCleanup;
let signalSent;
const relaySignal = (signal) => {
  signalSent = signal;
  if (!runner) return;
  if (process.platform === 'win32') {
    runnerCleanup ??= stopWindowsTree(runner, 'Playwright').catch((error) => {
      process.exitCode = 1;
      console.error(error.message);
    });
  } else {
    runner.kill(signal);
  }
};
const onSigint = () => relaySignal('SIGINT');
const onSigterm = () => relaySignal('SIGTERM');
process.once('SIGINT', onSigint);
process.once('SIGTERM', onSigterm);

try {
  let runnerEnv = process.env;
  if (!reuseExistingServer) {
    ownedServer = spawn(process.execPath, [path.join(workspace, 'scripts', 'dev-local.mjs')], {
      cwd: workspace,
      env: ownedServerEnv,
      stdio: 'inherit',
      windowsHide: true,
    });
    ownedServer.once('error', (error) => { ownedServer.spawnError = error; });
    await waitForOwnedServer(ownedServer);
    runnerEnv = { ...process.env, MY_ISLAND_E2E_REUSE_SERVER: '1' };
  }
  if (signalSent) throw new Error(`Interrupted before Playwright started (${String(signalSent)})`);

  runner = spawn(process.execPath, [
    path.join(modules, '@playwright', 'test', 'cli.js'),
    'test',
    ...process.argv.slice(2),
  ], { cwd: workspace, env: runnerEnv, stdio: 'inherit', windowsHide: true });

  const [code, signal] = await new Promise((resolve, reject) => {
    runner.once('error', reject);
    runner.once('close', (exitCode, exitSignal) => resolve([exitCode, exitSignal]));
  });
  process.exitCode = code ?? (signalSent || signal ? 1 : 0);
} catch (error) {
  process.exitCode = 1;
  console.error(error.message);
} finally {
  process.removeListener('SIGINT', onSigint);
  process.removeListener('SIGTERM', onSigterm);
  if (runnerCleanup) await runnerCleanup;
  await stopOwnedServer(ownedServer).catch((error) => {
    process.exitCode = 1;
    console.error(error.message);
  });
}
