// Run the actual frontend sources against the synthetic, loopback-only API.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { cp, mkdir, rm, symlink, lstat } from 'node:fs/promises';
import { spawn } from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const target = path.join(root, '.local-test/consolidation/frontend');
await mkdir(target, { recursive: true });
for (const name of ['app', 'components', 'public']) {
  await cp(path.join(root, name), path.join(target, name), { recursive: true, force: true });
}
for (const name of ['package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json', 'next.config.ts']) {
  await cp(path.join(root, name), path.join(target, name));
}
// school-audience.ts imports this checked-in alias vocabulary. Mirror the
// single shared asset, never the backend directory or any runtime data.
const sharedSubjectAliases = 'backend/services/schedule_subject_aliases.json';
await mkdir(path.dirname(path.join(target, sharedSubjectAliases)), { recursive: true });
await cp(path.join(root, sharedSubjectAliases), path.join(target, sharedSubjectAliases));

await mkdir(path.join(target, '.openai'), { recursive: true });
await cp(path.join(root, '.openai/hosting.json'), path.join(target, '.openai/hosting.json'));
await rm(path.join(target, 'components/schedule-admin.tsx'), { force: true });
if (!await lstat(path.join(target, 'node_modules')).catch(() => null)) {
  await symlink(path.join(root, 'node_modules'), path.join(target, 'node_modules'), 'junction');
}
const env = { ...process.env };
for (const key of Object.keys(env)) {
  if (/^(VITE_|GOOGLE_|TELEGRAM_|SUPABASE_|RENDER_)/.test(key) || key === 'DATABASE_URL') delete env[key];
}
Object.assign(env, {
  VITE_API_BASE_URL: 'http://127.0.0.1:8041',
  VITE_SCHOOL_DIRECTORY_MODE: 'school',
  VITE_DATA_SOURCE_LABEL: 'Local synthetic school · SQLite',
});
const child = spawn(process.execPath, [path.join(root, 'node_modules/vinext/dist/cli.js'), 'dev', '--host', '127.0.0.1', '--port', '3041'], {
  cwd: target, env, stdio: 'inherit', windowsHide: true,
});
child.on('exit', code => { process.exitCode = code || 0; });
