export type LaneEntry = {
  id: string;
  key: string;
  rank?: number;
  fixed?: { lane: number; width: number };
  sourceLane?: number;
  whole?: boolean;
  remainder?: boolean;
};

export function subgroupRank(name: string, subgroup?: string) {
  const numbered = /^(?:grade[0-9]+:split:|english:)([1-9][0-9]*)$/.exec(name);
  if (numbered) return Number(numbered[1]) - 1;
  const math = /^grade9-math-([ABC])$/.exec(name);
  if (math) return 'ABC'.indexOf(math[1]);
  const value = subgroup?.trim().toUpperCase() || '';
  if (/^[1-9][0-9]*$/.test(value)) return Number(value) - 1;
  if (/^[A-Z]$/.test(value)) return value.charCodeAt(0) - 65;
}

export function fixedGroup(name: string, grade: string) {
  const lane =
    grade === '9'
      ? ['grade9-math-A', 'grade9-math-B', 'grade9-math-C'].indexOf(name)
      : grade === '7'
        ? ['grade7:split:1', 'grade7:split:2'].indexOf(name)
        : -1;
  if (lane >= 0) return { lane, width: grade === '9' ? 3 : 2 };
}

export function schoolSourceLane(
  source: { sheet_id?: unknown; cells?: unknown } | undefined,
  grade: string,
) {
  if (
    !['638874869', '714307019'].includes(String(source?.sheet_id)) ||
    !Array.isArray(source?.cells)
  )
    return;
  const columns = grade === '9' ? 'LMN' : grade === '7' ? 'FG' : '';
  const lanes = new Set(
    source.cells.map((cell) => {
      const match = /^([A-Z])[1-9]\d*$/.exec(String(cell));
      return match ? columns.indexOf(match[1]) : -1;
    }),
  );
  if (lanes.size === 1 && !lanes.has(-1)) return [...lanes][0];
}

function partition(entries: LaneEntry[], gradeWidth: number) {
  if (
    entries.length === 1 &&
    entries[0].whole &&
    !entries[0].fixed &&
    !entries[0].remainder
  )
    return 0;
  const fixedWidth = Math.max(0, ...entries.map((e) => e.fixed?.width || 0));
  return (
    fixedWidth ||
    (entries.some((e) => e.sourceLane !== undefined || e.remainder)
      ? gradeWidth
      : 0)
  );
}

function seatCount(entries: LaneEntry[], base: number) {
  const fixed = new Set(
    entries.flatMap((e) => (e.fixed ? [e.fixed.lane] : [])),
  );
  return Math.max(base, fixed.size + entries.filter((e) => !e.fixed).length, 1);
}

// Coordinates describe display space only. A conflicting fixed group shares
// its own seat horizontally. Other cards never acquire that group's identity.
export function timetableLanes(slots: LaneEntry[][], gradeWidth = 0) {
  const bases = slots.map((entries) => partition(entries, gradeWidth));
  const anchoredWidth = Math.max(
    1,
    ...slots.map((entries, i) => (bases[i] ? seatCount(entries, bases[i]) : 1)),
  );
  const layouts = slots.map((entries, i) => {
    const base = bases[i];
    const seats = base ? anchoredWidth : Math.max(1, entries.length);
    const buckets: LaneEntry[][] = Array.from({ length: seats }, () => []);
    const uncertain = new Set<LaneEntry>();
    const ordered = [...entries].sort(
      (a, b) =>
        (a.rank ?? 100) - (b.rank ?? 100) ||
        (a.key < b.key
          ? -1
          : a.key > b.key
            ? 1
            : a.id < b.id
              ? -1
              : Number(a.id > b.id)),
    );
    for (const e of ordered) if (e.fixed) buckets[e.fixed.lane].push(e);
    const pending = ordered.filter((e) => !e.fixed);
    const placed = new Set<LaneEntry>();
    // Ordinary source positions have precedence over leftovers' source hints.
    for (const remainder of [false, true]) {
      for (const e of pending.filter(
        (e) => Boolean(e.remainder) === remainder,
      )) {
        const col = e.sourceLane;
        if (
          !e.whole &&
          col !== undefined &&
          Number.isInteger(col) &&
          col >= 0 &&
          col < base &&
          !buckets[col].length &&
          pending.filter(
            (p) =>
              !p.whole &&
              Boolean(p.remainder) === remainder &&
              p.sourceLane === col,
          ).length === 1
        ) {
          buckets[col].push(e);
          placed.add(e);
        }
      }
      for (const e of pending.filter(
        (e) => Boolean(e.remainder) === remainder && !placed.has(e),
      )) {
        const free = buckets.findIndex((bucket) => !bucket.length);
        buckets[free].push(e);
        if (
          base &&
          (!e.remainder ||
            pending.filter((p) => p.remainder).length > 1 ||
            buckets.slice(0, base).filter((b) => !b.length).length ||
            free >= base)
        )
          uncertain.add(e);
      }
    }
    const subdivisions = Math.max(1, ...buckets.map((b) => b.length));
    const positions = new Map<
      string,
      {
        col: number;
        row: number;
        span: number;
        unresolved?: boolean;
        conflict?: boolean;
      }
    >();
    buckets.forEach((bucket, seat) =>
      bucket.forEach((entry, index) => {
        const left = Math.floor((index * subdivisions) / bucket.length);
        positions.set(entry.id, {
          col: seat * subdivisions + left,
          row: 0,
          span: Math.floor(((index + 1) * subdivisions) / bucket.length) - left,
          ...(bucket.length > 1 ? { conflict: true } : {}),
          ...(bucket.length > 1 || uncertain.has(entry)
            ? { unresolved: true }
            : {}),
        });
      }),
    );
    return { columns: seats * subdivisions, positions };
  });
  return { width: Math.max(1, ...layouts.map((l) => l.columns)), layouts };
}

type Group = {
  id: string;
  name: string;
  canonicalName?: string;
  group_type: string;
  subject_subgroup?: string;
};
type PlacementLesson = {
  id: string;
  subject: string;
  groupId?: string;
  groupIds?: string[];
  classId?: string;
  classIds?: string[];
  start?: string;
  end?: string;
  blockName?: string;
  format?: string;
  place?: string;
  specialMark?: string;
  kind?: string;
  teacherId?: string;
  teacherIds?: string[];
  room?: string;
  activityType?: string;
  audience?: {
    kind?: string;
    group_ids?: string[];
    student_ids?: string[];
    exclude_student_ids?: string[];
  };
  audienceMode?: string;
  source?: { sheet_id?: unknown; cells?: unknown };
  canonical?: {
    assignment?: {
      canonical_group_ids?: string[];
      metadata?: {
        audience?: { canonical_group_ids?: string[] };
        audience_rule?: { group_ids?: string[]; kind?: string };
      };
    };
  };
};

export function instructionalGroupIds(
  lesson: Pick<
    PlacementLesson,
    'audience' | 'canonical' | 'groupId' | 'groupIds'
  >,
  isClass: (id: string) => boolean,
) {
  const distinct = (values: string[]) => [
    ...new Set(values.filter((id) => id && !isClass(id))),
  ];
  if (lesson.audience?.kind)
    return lesson.audience.kind === 'groups'
      ? distinct(lesson.audience.group_ids || [])
      : [];
  const current = distinct([...(lesson.groupIds || []), lesson.groupId || '']);
  if (current.length) return current;
  const assignment = lesson.canonical?.assignment;
  return distinct([
    ...(assignment?.canonical_group_ids || []),
    ...(assignment?.metadata?.audience?.canonical_group_ids || []),
    ...(assignment?.metadata?.audience_rule?.group_ids || []),
  ]);
}

export function lessonLaneEntry(
  lesson: PlacementLesson,
  grade: string,
  directory: Map<string, Group>,
): LaneEntry {
  const kind = lesson.audience?.kind;
  const ids = instructionalGroupIds(
    lesson,
    (id) => directory.get(id)?.group_type === 'class',
  );
  const names = ids
    .map(
      (id) => directory.get(id)?.canonicalName || directory.get(id)?.name || id,
    )
    .sort();
  const legacy = lesson.canonical?.assignment?.metadata?.audience_rule?.kind;
  const remainder = kind
    ? kind === 'available_slot'
    : lesson.audienceMode === 'remainder' ||
      legacy === 'available_slot' ||
      legacy === 'remaining';
  // Explicitly different grade partitions cannot claim a seat in this column.
  // Unrecognised or cross-grade groups stay in the candidate set.
  const local = ids.filter((id) => {
    const group = directory.get(id);
    const owner = /^grade(7|9)(?=:split:|-math-)/.exec(
      group?.canonicalName || group?.name || '',
    )?.[1];
    return !owner || owner === grade;
  });
  const group = local.length === 1 ? directory.get(local[0]) : undefined;
  const name = group?.canonicalName || group?.name || '';
  const cells = Array.isArray(lesson.source?.cells)
    ? lesson.source.cells.map(String).sort()
    : [];
  return {
    id: lesson.id,
    fixed: !remainder ? fixedGroup(name, grade) : undefined,
    remainder,
    whole:
      (kind === 'classes' || (!kind && legacy === 'whole_grade')) &&
      !lesson.audience?.exclude_student_ids?.length,
    sourceLane: schoolSourceLane(lesson.source, grade),
    rank: group && subgroupRank(name, group.subject_subgroup),
    key: JSON.stringify([
      names,
      lesson.subject,
      lesson.activityType || '',
      lesson.room || '',
      cells,
      lesson.start || '',
      lesson.end || '',
      lesson.blockName || '',
      lesson.format || '',
      lesson.place || '',
      lesson.specialMark || '',
      lesson.kind || '',
      kind || '',
      [
        ...new Set(
          [...(lesson.teacherIds || []), lesson.teacherId || ''].filter(
            Boolean,
          ),
        ),
      ].sort(),
      [...(lesson.classIds || [lesson.classId || ''])].sort(),
      lesson.audience?.student_ids?.length
        ? '0' + JSON.stringify([...lesson.audience.student_ids].sort())
        : '1',
    ]),
  };
}
