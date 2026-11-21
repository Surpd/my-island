import assert from 'node:assert/strict';
import test from 'node:test';
import {fixedGroup, schoolSourceLane, timetableLanes} from './schedule-lanes.ts';

test('the ninth grade keeps empty seats and honors independent source positions', () => {
  const entry = (id, group, cell, remainder = false) => ({id, key:id, fixed:fixedGroup(group, '9'),
    sourceLane:schoolSourceLane({sheet_id:'714307019',cells:[cell]}, '9'), remainder});
  const a=entry('a','grade9-math-A'), b=entry('b','grade9-math-B'), c=entry('c','grade9-math-C');
  const lunch=entry('lunch','','L7',true), chemistry=entry('chemistry','','N7');
  const {layouts}=timetableLanes([[chemistry,b,lunch],[c,entry('lunch','','',true),a],[b],[c,a],[entry('class','','')],[b,entry('unknown','','')] ]);
  assert.deepEqual(['lunch','b','chemistry'].map(id=>layouts[0].positions.get(id).col),[0,1,2]);
  assert.deepEqual(['a','lunch','c'].map(id=>layouts[1].positions.get(id).col),[0,1,2]);
  assert.equal(layouts[2].positions.get('b').col,1);
  assert.equal(layouts[2].columns,3);
  assert.equal(layouts[3].positions.get('c').col,2);
  assert.equal(layouts[4].columns,1);
  assert.equal(layouts[5].positions.get('unknown').unresolved,true);
});
test('the second seventh-grade group stays right across subjects', () => {
  const {layouts}=timetableLanes([[{id:'history',key:'2',fixed:fixedGroup('grade7:split:2','7')}],
    [{id:'literature',key:'2',fixed:fixedGroup('grade7:split:2','7')}]]);
  for(const layout of layouts) assert.equal([...layout.positions.values()][0].col,1);
});
