import { sites } from '@openai/sites-vite-plugin';
import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { defineConfig } from 'vite';
import hostingConfig from './.openai/hosting.json';

const SITE_CREATOR_PLACEHOLDER_DATABASE_ID =
  '00000000-0000-4000-8000-000000000000';

const { d1, r2 } = hostingConfig;

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === 'seatbelt';

const localBindingConfig = {
  main: 'vinext/server/fetch-handler',
  // Wrangler otherwise imports `.env` as Miniflare secret bindings. The
  // local-test profile points it at an intentionally absent, isolated file.
  envFiles: process.env.MY_ISLAND_LOCAL_TEST_MODE === 'true'
    ? ['.local-test/frontend-env/empty.env']
    : undefined,
  compatibility_flags: ['nodejs_compat'],
  d1_databases: d1
    ? [
        {
          binding: d1,
          database_name: 'site-creator-d1',
          database_id: SITE_CREATOR_PLACEHOLDER_DATABASE_ID,
        },
      ]
    : [],
  r2_buckets: r2
    ? [
        {
          binding: r2,
          bucket_name: 'site-creator-r2',
        },
      ]
    : [],
};

export default defineConfig(async () => {
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= 'false';
  process.env.WRANGLER_LOG_PATH ??= '.wrangler/logs';
  process.env.MINIFLARE_REGISTRY_PATH ??= '.wrangler/registry';

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import('@cloudflare/vite-plugin');

  return {
    // The explicit local-test profile must not inherit any project `.env`
    // values (including server-side integration secrets) into Miniflare.
    envDir: process.env.MY_ISLAND_LOCAL_TEST_MODE === 'true'
      ? '.local-test/frontend-env'
      : undefined,
    css: { postcss: { plugins: [tailwindcss()] } },
    server: {
      watch: {
        ...(isCodexSeatbeltSandbox ? { useFsEvents: false, usePolling: true } : {}),
        // Tests live beneath the project but must never reload the normal
        // school browser when reports, copied configs or traces are written.
        ignored: ['**/.local-test/**', '**/test-results/**', '**/.sites-*/**', '**/.release-worktree/**'],
      },
    },
    plugins: [
      vinext(),
      sites(),
      cloudflare({
        viteEnvironment: { name: 'rsc', childEnvironments: ['ssr'] },
        config: localBindingConfig,
      }),
    ],
  };
});
