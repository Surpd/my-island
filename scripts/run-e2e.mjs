import { cp, mkdir, lstat, readlink, readdir, rm, stat, symlink, unlink } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const workspace = path.join(projectRoot, '.local-test', 'e2e-workspace');
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
const runner = spawn(process.execPath, [
  path.join(modules, '@playwright', 'test', 'cli.js'),
  'test',
  ...process.argv.slice(2),
], { cwd: workspace, env: process.env, stdio: 'inherit', windowsHide: true });

let signalSent;
const relaySignal = (signal) => {
  signalSent = signal;
  runner.kill(signal);
};
process.once('SIGINT', () => relaySignal('SIGINT'));
process.once('SIGTERM', () => relaySignal('SIGTERM'));

const [code, signal] = await new Promise((resolve, reject) => {
  runner.once('error', reject);
  runner.once('close', (exitCode, exitSignal) => resolve([exitCode, exitSignal]));
});
process.exitCode = code ?? (signalSent || signal ? 1 : 0);
