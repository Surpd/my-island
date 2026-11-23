import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixedGroup,
  occupiedCellLanes,
  schoolSourceLane,
  timetableLanes,
} from './schedule-lanes.ts';

test('the ninth grade keeps empty seats and honors independent source positions', () => {
  const entry = (id, group, cell, remainder = false) => ({
    id,
    key: id,
    fixed: fixedGroup(group, '9'),
    sourceLane: schoolSourceLane({ sheet_id: '714307019', cells: [cell] }, '9'),
    remainder,
  });
  const a = entry('a', 'grade9-math-A'),
    b = entry('b', 'grade9-math-B'),
    c = entry('c', 'grade9-math-C');
  const lunch = entry('lunch', '', 'L7', true),
    chemistry = entry('chemistry', '', 'N7');
  const { layouts } = timetableLanes([
    [chemistry, b, lunch],
    [c, entry('lunch', '', '', true), a],
    [b],
    [c, a],
    [entry('class', '', '')],
    [b, entry('unknown', '', '')],
  ]);
  assert.deepEqual(
    ['lunch', 'b', 'chemistry'].map((id) => layouts[0].positions.get(id).col),
    [0, 1, 2],
  );
  assert.deepEqual(
    ['a', 'lunch', 'c'].map((id) => layouts[1].positions.get(id).col),
    [0, 1, 2],
  );
  assert.equal(layouts[2].positions.get('b').col, 1);
  assert.equal(layouts[2].columns, 3);
  assert.equal(layouts[3].positions.get('c').col, 2);
  assert.equal(layouts[4].columns, 1);
  assert.equal(layouts[5].positions.get('unknown').unresolved, true);
});
test('compacts occupied ninth-grade slots independently, honoring source order', () => {
  const make = (id, canonical, sourceLane, remainder = false) => ({
    id, key: id, fixed: fixedGroup(canonical, '9'), sourceLane, remainder,
  });
  const left = make('base', 'grade9-math-A');
  const red = make('red', '', 1, true);
  const green = make('green', '', 2);
  const { layouts } = timetableLanes([[green, red, left], [green, red], [left]], 3);
  const map = (index, visible) => occupiedCellLanes(layouts[index], visible);
  const expectedRows = [['base', 'red', 'green'], ['red', 'green'], ['base']];
  for (const [index, expected] of expectedRows.entries()) {
    const actual = map(index, [...expected].reverse());
    assert.equal(actual.columns, expected.length);
    assert.deepEqual([...actual.positions.keys()], expected);
    expected.forEach((id, col) => assert.deepEqual(actual.positions.get(id), { col, row: 0, span: 1 }));
  }
  assert.equal(layouts[1].positions.get('red').col, 1, 'the stable layout is not changed');
  assert.equal(map(0, ['green']).positions.get('green').col, 0, 'filters do not leave holes');
  assert.equal(map(1, []).columns, 1);
});

test('seventh-grade continued cards fill the cell and same-lane conflicts remain explicit', () => {
  const make = (id, lane) => ({ id, key: id, fixed: fixedGroup('grade7:split:' + lane, '7') });
  const a = make('a', 1), b = make('b', 2), clash = make('clash', 2);
  const { layouts } = timetableLanes([[a, b, clash], [a, b], [b]], 2);
  const first = occupiedCellLanes(layouts[0], ['clash', 'b', 'a']);
  assert.equal(first.columns, 3);
  assert.deepEqual([...first.positions.keys()], ['a', 'b', 'clash']);
  assert.equal(first.positions.get('b').conflict, true);
  assert.equal(first.positions.get('clash').unresolved, true);
  const second = occupiedCellLanes(layouts[1], ['b', 'a']);
  assert.equal(second.columns, 2);
  assert.deepEqual([...second.positions.keys()], ['a', 'b']);
  const continued = occupiedCellLanes(layouts[2], ['b']);
  assert.equal(continued.columns, 1);
  assert.equal(continued.positions.get('b').col, 0);
  assert.equal(continued.positions.get('b').conflict, undefined);
});

test('the second seventh-grade group stays right across subjects', () => {
  const { layouts } = timetableLanes([
    [{ id: 'history', key: '2', fixed: fixedGroup('grade7:split:2', '7') }],
    [{ id: 'literature', key: '2', fixed: fixedGroup('grade7:split:2', '7') }],
  ]);
  for (const layout of layouts)
    assert.equal([...layout.positions.values()][0].col, 1);
});

test('a historical lunch coordinate cannot displace Tuesday’s confirmed art group', () => {
  const slot = [
    { id: 'math', key: 'b', fixed: { lane: 1, width: 3 }, sourceLane: 1 },
    { id: 'history', key: 'c', fixed: { lane: 2, width: 3 } },
    { id: 'lunch', key: 'others', sourceLane: 2, remainder: true },
  ];
  for (const entries of [slot, [...slot].reverse()]) {
    const { positions, columns } = timetableLanes([entries]).layouts[0];
    assert.equal(columns, 3);
    for (const [col, id] of ['lunch', 'math', 'history'].entries())
      assert.deepEqual(positions.get(id), { col, row: 0, span: 1 });
  }
});

test('fixed collisions subdivide only their seat; other audiences occupy separate horizontal space', () => {
  const a = { id: 'z', key: 'art', fixed: { lane: 1, width: 3 } },
    b = { ...a, id: 'a', key: 'math' };
  const pending = [
    { id: 'other', key: 'practice' },
    { id: 'l1', key: 'meal-1', remainder: true, sourceLane: 1 },
    { id: 'l2', key: 'meal-2', remainder: true, sourceLane: 1 },
  ];
  for (const slot of [
    [a, b, ...pending],
    [...pending, b, a],
  ]) {
    const { positions: p, columns } = timetableLanes([slot]).layouts[0];
    assert.equal(p.size, 5);
    assert.equal(columns, 8);
    assert.equal(p.get('z').col, 2);
    assert.equal(p.get('a').col, 3);
    assert.equal(p.get('z').conflict, true);
    const occupied = new Set();
    for (const { col, row, span } of p.values()) {
      assert.equal(row, 0);
      for (let i = col; i < col + span; i++) {
        assert.ok(!occupied.has(i));
        occupied.add(i);
      }
    }
    assert.equal(occupied.size, columns);
  }
});

test('relocation, copy, group edit and deletion recalculate from the live occupants', () => {
  const group = { id: 'A', key: 'first', fixed: { lane: 0, width: 3 } },
    remainder = { id: 'meal', key: 'meal', remainder: true };
  let slots = [[group, remainder], []];
  const place = () => timetableLanes(slots, 3).layouts;
  assert.equal(place()[0].positions.get('A').col, 0);
  slots = [[remainder], [group]];
  assert.equal(place()[1].positions.get('A').col, 0);
  slots[1].push({ ...group, id: 'copy', key: 'second' });
  assert.ok(place()[1].positions.get('copy').conflict);
  slots[1][1] = { ...group, id: 'copy', fixed: { lane: 2, width: 3 } };
  assert.equal(place()[1].positions.get('copy').col, 2);
  slots[1].shift();
  assert.equal(place()[1].positions.get('copy').col, 2);
});

test('explicit audience beats obsolete group mirrors and source cells', async () => {
  const { lessonLaneEntry } = await import('./schedule-lanes.ts');
  const groups = new Map(
    ['A', 'B', 'C'].map((id) => [
      id,
      { id, name: 'grade9-math-' + id, group_type: 'subject_group' },
    ]),
  );
  const raw = {
    id: 'art',
    subject: 'История искусства',
    groupId: 'A',
    canonical: { assignment: { canonical_group_ids: ['B'] } },
    source: { sheet_id: 638874869, cells: ['L7', 'N7'] },
    audience: { kind: 'groups', group_ids: ['C'] },
  };
  assert.deepEqual(lessonLaneEntry(raw, '9', groups).fixed, {
    lane: 2,
    width: 3,
  });
  for (const cells of [
    [],
    ['M', 'N6'],
    ['M6', 'N6'],
    ['12'],
    [null],
    ['AA7'],
  ]) {
    assert.equal(
      schoolSourceLane({ sheet_id: 638874869, cells }, '9'),
      undefined,
    );
  }
  for (const audience of [
    { kind: 'students', student_ids: ['p'] },
    { kind: 'groups', group_ids: ['A', 'C'] },
    { kind: 'available_slot' },
  ]) {
    assert.equal(
      lessonLaneEntry({ ...raw, audience }, '9', groups).fixed,
      undefined,
    );
  }
  const whole = lessonLaneEntry(
    {
      ...raw,
      audience: { kind: 'classes' },
      source: { sheet_id: 638874869, cells: ['L7'] },
    },
    '9',
    groups,
  );
  assert.equal(timetableLanes([[whole]], 3).layouts[0].columns, 1);
});

test('semantic order survives new ids and contradictory source claims', () => {
  const slots = ['Биология', 'Практикум', 'Химия'].map((key, i) => ({
    id: 'id' + i,
    key,
    sourceLane: 1,
  }));
  const order = (entries) => {
    const { positions } = timetableLanes([entries], 3).layouts[0];
    return entries
      .slice()
      .sort((a, b) => positions.get(a.id).col - positions.get(b.id).col)
      .map((e) => e.key);
  };
  assert.deepEqual(
    order(slots),
    order(slots.reverse().map((e, i) => ({ ...e, id: 'new' + i }))),
  );
});

test('online and on-site entries with an identical subject have stable positions after copying', async () => {
  const { lessonLaneEntry } = await import('./schedule-lanes.ts');
  const positions = (entries) => {
    const slot = entries.map((e) =>
      lessonLaneEntry({ subject: 'Консультация', ...e }, '10', new Map()),
    );
    const layout = timetableLanes([slot]).layouts[0];
    return entries.find((e) => layout.positions.get(e.id).col === 0).format;
  };
  assert.equal(
    positions([
      { id: 'b', format: 'offline' },
      { id: 'a', format: 'online' },
    ]),
    'offline',
  );
  assert.equal(
    positions([
      { id: 'z', format: 'online' },
      { id: 'a', format: 'offline' },
    ]),
    'offline',
  );
});
