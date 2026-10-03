import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const manifest = JSON.parse(
  fs.readFileSync(path.join(root, '.project-identity.json'), 'utf8'),
);

function readDotEnv() {
  const file = path.join(root, '.env');
  if (!fs.existsSync(file)) return {};
  return Object.fromEntries(
    fs
      .readFileSync(file, 'utf8')
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#') && line.includes('='))
      .map((line) => {
        const separator = line.indexOf('=');
        const key = line.slice(0, separator).trim();
        const value = line
          .slice(separator + 1)
          .trim()
          .replace(/^(['"])(.*)\1$/, '$2');
        return [key, value];
      }),
  );
}

const env = { ...readDotEnv(), ...process.env };
const checkedValues = [
  env.VITE_API_BASE_URL,
  env.BACKEND_PUBLIC_URL,
  env.FRONTEND_PUBLIC_URL,
  env.RENDER_WORKSPACE_ID,
  env.RENDER_SERVICE_ID,
  env.RENDER_EXTERNAL_URL,
].filter(Boolean);
const forbidden = Object.values(manifest.forbiddenExternalTargets)
  .flat()
  .map((value) => String(value).toLowerCase());

for (const value of checkedValues) {
  const normalized = String(value).toLowerCase();
  const marker = forbidden.find((candidate) => normalized.includes(candidate));
  if (marker) {
    throw new Error(
      `Project identity mismatch: My Island cannot use forbidden target "${marker}".`,
    );
  }
}

const approved = manifest.approvedExternalTargets;
if (
  env.RENDER_WORKSPACE_ID &&
  env.RENDER_WORKSPACE_ID !== approved.renderWorkspaceId
) {
  throw new Error(
    `Project identity mismatch: expected Render workspace "${approved.renderWorkspaceId}".`,
  );
}
if (
  env.RENDER_SERVICE_ID &&
  env.RENDER_SERVICE_ID !== approved.renderServiceId
) {
  throw new Error(
    `Project identity mismatch: expected Render service "${approved.renderServiceId}".`,
  );
}

if (!env.VITE_API_BASE_URL) {
  throw new Error(
    'Project identity check failed: VITE_API_BASE_URL must be explicitly configured for My Island builds.',
  );
}

console.log(`Project identity verified: ${manifest.project} (${manifest.projectId})`);
