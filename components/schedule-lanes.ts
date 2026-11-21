type Entry = { id: string; key: string; rank?: number; fixed?: {lane: number; width: number}; sourceLane?: number; remainder?: boolean };

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
  const lane = grade === '9' ? ['grade9-math-A','grade9-math-B','grade9-math-C'].indexOf(name)
    : grade === '7' ? ['grade7:split:1','grade7:split:2'].indexOf(name) : -1;
  if (lane >= 0) return {lane, width: grade === '9' ? 3 : 2};
}

export function schoolSourceLane(source: {sheet_id?: unknown; cells?: unknown} | undefined, grade: string) {
  if (!['638874869', '714307019'].includes(String(source?.sheet_id)) || !Array.isArray(source?.cells)) return;
  const columns = grade === '9' ? 'LMN' : grade === '7' ? 'FG' : '';
  const lanes = new Set(source.cells.map(cell => {
    const match = /^([A-Z])[1-9]\d*$/.exec(String(cell));
    return match ? columns.indexOf(match[1]) : -1;
  }));
  if (lanes.size === 1 && !lanes.has(-1)) return [...lanes][0];
}

export function timetableLanes(slots: Entry[][]) {
  const layouts = slots.map(entries => {
    const ordered = [...entries].sort((a,b) => (a.rank ?? 100) - (b.rank ?? 100) || a.key.localeCompare(b.key));
    const fixed = ordered.find(entry => entry.fixed)?.fixed;
    const columns = fixed?.width ?? Math.max(1, ordered.length);
    const positions = new Map<string, {col:number; row:number; span:number; unresolved?:boolean}>();
    if (!fixed) {
      ordered.forEach((e,col) => positions.set(e.id,{col,row:0,span:1}));
    } else {
      const heights = Array<number>(columns).fill(0);
      const extra: Entry[] = [];
      for (const entry of ordered.sort((a,b) => Number(!a.fixed)-Number(!b.fixed))) {
        const col = entry.fixed?.lane ?? entry.sourceLane;
        if (col === undefined || col >= columns) extra.push(entry);
        else positions.set(entry.id,{col,row:heights[col]++,span:1});
      }
      const free = heights.flatMap((height,col) => height ? [] : [col]);
      if (extra.length === 1 && extra[0].remainder && free.length === 1) {
        positions.set(extra[0].id,{col:free[0],row:0,span:1});
      } else {
        extra.forEach((e,i) => positions.set(e.id,{col:0,row:Math.max(...heights)+i,span:columns,unresolved:true}));
      }
    }
    return {columns, positions};
  });
  return {width:Math.max(1,...layouts.map(l=>l.columns)), layouts};
}
