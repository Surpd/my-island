import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

async function exerciseReconnect(reconnectResult) {
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
    pending[0].resolve({ actor: 'first admin', version: 'initial' });
    await new Promise(setImmediate);
    window.dispatchEvent(new Event('focus'));
    assert.equal(pending.length, 2);
    navigator.onLine = false;
    window.dispatchEvent(new Event('offline'));
    navigator.onLine = true;
    window.dispatchEvent(new Event('online'));
    assert.equal(pending.length, 3);
    assert.equal(pending[1].signal.aborted, true);
    reconnectResult(pending[2]);
    await new Promise(setImmediate);
    pending[1].resolve({ actor: 'first admin', version: 'stale' });
    await new Promise(setImmediate);
    assert.equal(revoked, 1);
    assert.deepEqual(refreshed, ['initial']);
  } finally {
    cleanups.forEach(fn => fn?.());
  }
}

test('reconnect observes revoked access before a stale marker arrives', async () => {
  await exerciseReconnect(request => request.reject({ status: 403 }));
});

test('switching administrators discards the previous session and its late marker', async () => {
  await exerciseReconnect(request => request.resolve({ actor: 'second admin', version: 'new session' }));
});
