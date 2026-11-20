import assert from 'node:assert/strict';
import test from 'node:test';
import {subgroupRank, timetableLanes} from './schedule-lanes.ts';

test('confirmed groups keep their columns across days, swaps and empty neighbors', () => {
  for (const names of [['grade7:split:1','grade7:split:2'], ['grade9-math-A','grade9-math-B','grade9-math-C']]) {
    const slots = [names, [...names].reverse(), names.slice(-1), ['class'], [names[0], names[0]]];
    const {positions,width} = timetableLanes(slots.map(groups => groups.map((key,i) => ({
      id: String(i), key, rank: subgroupRank(key), whole: key === 'class'
    }))));
    assert.equal(width, names.length);
    assert.equal(positions[1].get('0').lane, names.length-1);
    assert.equal(positions[2].get('0').lane, names.length-1);
    assert.equal(positions[3].get('0').whole, true);
    assert.equal(positions[4].get('1').collision, true);
    assert.notEqual(positions[4].get('0').lane, positions[4].get('1').lane);
  }
});
