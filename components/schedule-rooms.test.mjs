import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeRoomDisplay, normalizeRoomInput, normalizeRoomKey } from './schedule-rooms.ts';

test('common Russian numbered-room spellings share a key and concise display', () => {
  const spellings = ['каб.16', 'каб 16', 'Кабинет 16', '16'];
  assert.equal(new Set(spellings.map(normalizeRoomKey)).size, 1);
  assert.deepEqual(spellings.map(normalizeRoomDisplay), ['каб. 16', 'каб. 16', 'каб. 16', 'каб. 16']);
  assert.deepEqual(['8', '16', 'каб.8', 'каб. 8'].map(normalizeRoomInput), ['8', '16', '8', '8']);
  assert.equal(normalizeRoomKey('кабинет 16а'), normalizeRoomKey('16А'));
  assert.notEqual(normalizeRoomKey('ауд. 16'), normalizeRoomKey('16'));
});

test('unknown room names keep their semantic identity and readable spelling', () => {
  assert.notEqual(normalizeRoomKey('Большой зал'), normalizeRoomKey('Малый зал'));
  assert.notEqual(normalizeRoomKey('зал 1'), normalizeRoomKey('зал 2'));
  assert.notEqual(normalizeRoomKey('Ёлочный зал'), normalizeRoomKey('Елочный зал'));
  assert.equal(normalizeRoomKey('  Театр   А  '), normalizeRoomKey('театр а'));
  assert.equal(normalizeRoomDisplay('  Театр   А  '), 'Театр А');
  assert.equal(normalizeRoomDisplay('Библиотека'), 'Библиотека');
  assert.equal(normalizeRoomInput('Библиотека'), 'Библиотека');
  assert.equal(normalizeRoomDisplay(null), '');
});
