// Input is a private detached snapshot; the output contains aggregate counts only.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import {
  lessonLaneEntry,
  timetableLanes,
} from '../../components/schedule-lanes.ts';

const path = process.argv[2];
const { groups, payload: p } = JSON.parse(readFileSync(path, 'utf8'));
const sheet = spawnSync(
  process.env.PYTHON || 'python',
  ['-m', 'scripts.schedule-regression.lane_week_sheet', path],
  { encoding: 'utf8' },
);
assert.equal(sheet.status, 0, sheet.stderr);
const exportSlots = JSON.parse(sheet.stdout);
const directory = new Map(groups.map((g) => [g.id, g]));
const gradeOf = (id) => {
  const g = directory.get(id);
  return Number((g?.base_class_name || g?.name || '').match(/\d+/)?.[0]);
};
const report = [],
  seen = new Set();
for (let grade = 5; grade <= 11; grade++) {
  const slots = p.days.flatMap((day) =>
    p.slots.map((bell) =>
      p.lessons
        .filter((l) => {
          const ids =
            l.activityType === 'club' && l.displayClassIds?.length
              ? l.displayClassIds
              : l.classIds?.length
                ? l.classIds
                : [l.classId];
          const base = p.slots[l.period];
          return (
            l.day === day &&
            (ids.some((id) => gradeOf(id) === grade) ||
              (l.activityType === 'club' &&
                !ids.some(Boolean) &&
                l.eligibleGrades?.includes(grade))) &&
            (l.start || base.start) < bell.end &&
            (l.end || base.end) > bell.start
          );
        })
        .map((l) => lessonLaneEntry(l, String(grade), directory)),
    ),
  );
  const { layouts } = timetableLanes(
    slots,
    grade === 9 ? 3 : grade === 7 ? 2 : 0,
  );
  layouts.forEach(({ columns, positions }, i) => {
    const day = p.days[Math.floor(i / p.slots.length)],
      period = i % p.slots.length;
    const cell = exportSlots[[grade, day, period].join('/')] || {};
    const used = new Set();
    let ambiguous = 0, fixedConflicts = 0;
    for (const [id, { col, row, span, unresolved, conflict }] of positions) {
      assert.equal(row, 0);
      assert.ok(col >= 0 && span > 0 && col + span <= columns);
      for (let x = col; x < col + span; x++) {
        assert.ok(!used.has(x));
        used.add(x);
      }
      assert.ok(cell[id], 'Missing sheet card at ' + [grade, day, period, id]);
      const [left, right, total] = cell[id];
      assert.equal(
        left,
        Math.floor((col * total) / columns),
        'Sheet column mismatch ' + [grade, day, period, id],
      );
      assert.equal(
        right,
        Math.floor(((col + span) * total) / columns),
        'Sheet span mismatch ' + [grade, day, period, id],
      );
      seen.add(id);
      if (unresolved) ambiguous++;
      if (conflict) fixedConflicts++;
    }
    assert.equal(Object.keys(cell).length, positions.size);
    report.push({
      grade,
      day,
      period,
      time: p.slots[period].start,
      columns,
      cards: positions.size,
      free: columns - used.size,
      ambiguous,
      fixedConflicts,
    });
  });
}
assert.equal(seen.size, p.lessons.length);
const summary = {sourceLessons:seen.size, gradeSlots:report.length, overlaps:0, verticalStacks:0, missingCards:0};
console.log(JSON.stringify(summary,null,2).slice(0,-2) + ',\n  "slots": [\n' +
  report.map(slot => '    '+JSON.stringify(slot)).join(',\n') + '\n  ]\n}');
