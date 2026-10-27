import test from 'node:test';
import assert from 'node:assert/strict';
import { campusNavigationForContext, campusNavigationFromHistory, campusNavigationReducer, initialCampusNavigation } from './campus-navigation.ts';

test('building opens at peek and the reducer follows the shared four-level path', () => {
  let state = campusNavigationReducer(initialCampusNavigation, { type: 'open-building', building: 'grades' });
  assert.equal(state.level, 'peek');
  state = campusNavigationReducer(state, { type: 'expand' });
  state = campusNavigationReducer(state, { type: 'open-detail', detailKey: 'math:roster-1' });
  assert.deepEqual([state.building, state.level, state.detailKey], ['grades', 'detail', 'math:roster-1']);
  state = campusNavigationReducer(state, { type: 'close-detail' });
  assert.deepEqual([state.building, state.level, state.detailKey], ['grades', 'expanded', null]);
  state = campusNavigationReducer(state, { type: 'collapse' });
  assert.deepEqual([state.building, state.level], ['grades', 'peek']);
  assert.deepEqual(campusNavigationReducer(state, { type: 'close' }), initialCampusNavigation);
});

test('switching buildings and resetting identity always clear stale detail and scroll', () => {
  let state = campusNavigationReducer(initialCampusNavigation, { type: 'open-building', building: 'grades', scopeId: 'target-a' });
  state = campusNavigationReducer(state, { type: 'expand' });
  state = campusNavigationReducer(state, { type: 'open-detail', detailKey: 'math:roster-1' });
  state = campusNavigationReducer(state, { type: 'save-scroll', scrollTop: 320 });
  state = campusNavigationReducer(state, { type: 'open-building', building: 'schedule', scopeId: 'target-b' });
  assert.deepEqual(state, { building: 'schedule', level: 'peek', detailKey: null, detailContext: null, scrollTop: 0, historyDepth: 0, scopeId: 'target-b' });
  assert.deepEqual(campusNavigationReducer(state, { type: 'reset' }), { ...initialCampusNavigation, scopeId: 'target-b' });
});

test('history parser accepts valid snapshots and rejects malformed or stale states', () => {
  assert.deepEqual(campusNavigationFromHistory({ building: 'grades', level: 'detail', detailKey: 'math', scrollTop: 42 }), {
    building: 'grades', level: 'detail', detailKey: 'math', detailContext: null, scrollTop: 42, historyDepth: 0, scopeId: 'root',
  });
  assert.deepEqual(campusNavigationFromHistory({ building: 'grades', level: 'detail', detailKey: null }), initialCampusNavigation);
  assert.deepEqual(campusNavigationFromHistory({ building: null, level: 'expanded' }), initialCampusNavigation);
});

test('stale schedule lesson detail falls back to the weekly schedule when the period changes', () => {
  const state = campusNavigationReducer(
    campusNavigationReducer(initialCampusNavigation, { type: 'open-building', building: 'schedule' }),
    { type: 'expand' },
  );
  const detail = campusNavigationReducer(state, { type: 'open-detail', detailKey: 'lesson:2026-10-26:0', detailContext: '2026-10-26' });
  assert.equal(campusNavigationForContext(detail, '2026-10-26').level, 'detail');
  assert.deepEqual(campusNavigationForContext(detail, '2026-11-02'), { ...detail, level: 'expanded', detailKey: null, detailContext: null });
});
