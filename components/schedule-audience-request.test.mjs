import test from 'node:test';
import assert from 'node:assert/strict';
import { audienceRequestState, prepareAudienceRequest, sameExclusionRosterContext, scopedAudienceKey } from './schedule-audience-request.ts';

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

test('preview scope separates identically shaped drafts, workspaces, and changed inputs', () => {
  const current = scopedAudienceKey('school', 'draft-a', '{"payload":{"lessons":[]}}');
  const completed = { key: current, attempt: 0 };
  assert.deepEqual(audienceRequestState(current, 0, completed, null, true), { status: 'complete' });
  for (const changed of [
    scopedAudienceKey('demo', 'draft-a', '{"payload":{"lessons":[]}}'),
    scopedAudienceKey('school', 'draft-b', '{"payload":{"lessons":[]}}'),
    scopedAudienceKey('school', 'draft-a', '{"payload":{"lessons":[{}]}}'),
  ]) {
    assert.notEqual(changed, current);
    assert.deepEqual(audienceRequestState(changed, 0, completed, null, true), { status: 'pending' });
  }
  assert.deepEqual(audienceRequestState('', 0, completed, null, true), { status: 'idle' });
});

test('failed transport is request-scoped and an explicit retry enters pending before the next result', () => {
  const key = 'same draft and same audience';
  const completed = { key, attempt: 0 };
  const failed = { key, attempt: 1, reason: 'timeout' };
  assert.deepEqual(audienceRequestState(key, 1, completed, failed, true), { status: 'unavailable', reason: 'timeout' });
  assert.deepEqual(audienceRequestState(key, 2, completed, failed, true), { status: 'pending' });
  assert.deepEqual(audienceRequestState('another draft', 1, null, failed, true), { status: 'pending' });
});

test('deadline starts after the queue and releases it even when a timed-out response arrives late', async () => {
  const enqueued = deferred();
  const lateResult = deferred();
  const failure = deferred();
  const calls = [];
  const cancel = prepareAudienceRequest({
    delayMs: 0, timeoutMs: 15,
    enqueue: async (task) => enqueued.resolve(task),
    load: () => lateResult.promise, isPaused: () => false,
    complete: (value) => calls.push(value), fail: (reason) => { calls.push(reason); failure.resolve(); },
  });
  try {
    const start = await enqueued.promise;
    await new Promise(r => setTimeout(r, 30));
    assert.deepEqual(calls, []);
    await start();
    await failure.promise;
    lateResult.resolve('should never replace the retry');
    await Promise.resolve();
    assert.deepEqual(calls, ['timeout']);
  } finally { cancel(); }
});

test('cancelled old request does not publish a result or error even if the loader ignores abort', async () => {
  const enqueued = deferred();
  const lateResult = deferred();
  const calls = [];
  const cancel = prepareAudienceRequest({
    delayMs: 0, timeoutMs: 1000,
    enqueue: async (task) => enqueued.resolve(task),
    load: () => lateResult.promise, isPaused: () => false,
    complete: (value) => calls.push(value), fail: (reason) => calls.push(reason),
  });
  const start = await enqueued.promise;
  const finished = start();
  cancel();
  lateResult.resolve('from old draft');
  await finished;
  assert.deepEqual(calls, []);
});

test('ordinary request rejection is distinct from timeout', async () => {
  const failure = deferred();
  const cancel = prepareAudienceRequest({
    delayMs: 0, timeoutMs: 1000,
    enqueue: (task) => task(),
    load: async () => { throw new Error('offline'); }, isPaused: () => false,
    complete: () => failure.resolve('unexpected success'), fail: failure.resolve,
  });
  try { assert.equal(await failure.promise, 'request'); } finally { cancel(); }
});

test('consecutive exclusions preserve their roster only inside the same lesson, draft and workspace', () => {
  const key = (ids, { workspace = 'school', draft = 'test:draft', otherRoom = '18' } = {}) => scopedAudienceKey(workspace, draft, JSON.stringify({ lessons: [
    { id: 'x', audience: { kind: 'class', exclude_student_ids: ids }, audience_snapshot: { student_ids: ids } },
    { id: 'y', room: otherRoom },
  ] }));
  assert.ok(sameExclusionRosterContext(key([]), key(['one']), 'x'));
  assert.ok(sameExclusionRosterContext(key([]), key(['one', 'two', 'three']), 'x'));
  assert.ok(!sameExclusionRosterContext(key([]), key(['one']), 'y'));
  assert.ok(!sameExclusionRosterContext(key([]), key(['one'], { workspace: 'demo' }), 'x'));
  assert.ok(!sameExclusionRosterContext(key([]), key(['one'], { draft: 'other' }), 'x'));
  assert.ok(!sameExclusionRosterContext(key([]), key(['one'], { otherRoom: '19' }), 'x'));
  assert.ok(!sameExclusionRosterContext('{}:{"lessons":[]}', '{}:{"lessons":[]}', 'x'));
});
