import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

test('reconnect supersedes a pending response and revocation wins over late success', async () => {
  const pending = [];
  const refreshed = [];
  const cleanups = [];
  const window = Object.assign(new EventTarget(), { setInterval: () => 1, clearInterval() {} });
  const document = Object.assign(new EventTarget(), { hidden: false });
  const navigator = { onLine: true };
  let revoked = 0;
  const react = { useRef: current => ({ current }), useEffect: effect => cleanups.push(effect()) };
  const source = readFileSync('components/use-live-revision.ts', 'utf8');
  const moduleExports = {};
  runInNewContext(ts.transpile(source, { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }),
    { exports: moduleExports, require: () => react, window, document, navigator, AbortController });
  moduleExports.useLiveRevision({
    enabled: true, path: '/marker',
    api: (_path, options) => new Promise((resolve, reject) => pending.push({ resolve, reject, signal: options.signal })),
    refresh: async marker => { refreshed.push(marker.version); },
    revoke: () => { revoked++; },
  });
  try {
    assert.equal(pending.length, 1);
    navigator.onLine = false;
    window.dispatchEvent(new Event('offline'));
    navigator.onLine = true;
    window.dispatchEvent(new Event('online'));
    assert.equal(pending.length, 2);
    assert.equal(pending[0].signal.aborted, true);
    pending[1].reject({ status: 403 });
    await new Promise(setImmediate);
    pending[0].resolve({ actor: 'fixture', version: 'stale' });
    await new Promise(setImmediate);
    assert.equal(revoked, 1);
    assert.deepEqual(refreshed, []);
  } finally {
    cleanups.forEach(fn => fn?.());
  }
});
