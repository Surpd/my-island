import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
const families = JSON.parse(readFileSync(new URL('../backend/tests/fixtures/schedule_lane_order.json', import.meta.url)));
import {subgroupRank, timetableLanes} from './schedule-lanes.ts';

test('confirmed groups keep their columns across days, swaps and empty neighbors', () => {
  for (const names of families) {
    const slots = [names, [...names].reverse(), names.slice(-1), ['class'], [names[0], names[0]]];
    const {positions,width} = timetableLanes(slots.map(groups => groups.map((key,i) => ({
      id: String(i), key, rank: subgroupRank(key), whole: key === 'class'
    }))));
    assert.equal(width, names.length);
    assert.equal(positions[1].get('0'), names.length-1);
    assert.equal(positions[2].get('0'), 0);
    assert.equal(positions[3].get('0'), 0);
    assert.notEqual(positions[4].get('0'), positions[4].get('1'));
  }
});
