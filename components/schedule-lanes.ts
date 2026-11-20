type Entry = { id: string; key: string; rank?: number; whole: boolean };

export function subgroupRank(name: string, dimension?: string, subgroup?: string) {
  const numbered = /^grade7:split:([12])$/.exec(name);
  if (numbered) return Number(numbered[1]) - 1;
  const math = /^grade9-math-([ABC])$/.exec(name);
  if (math) return 'ABC'.indexOf(math[1]);
  const value = subgroup?.trim().toUpperCase() || '';
  if (dimension && /^[123ABC]$/.test(value)) return '123ABC'.indexOf(value) % 3;
}

export function timetableLanes(slots: Entry[][]) {
  const neighbors = new Map<string, Set<string>>();
  const ranks = new Map<string, number>();
  for (const entries of slots) {
    for (const entry of entries) {
      const others = neighbors.get(entry.key) || new Set<string>();
      for (const other of entries) if (other.key !== entry.key) others.add(other.key);
      neighbors.set(entry.key, others);
      if (entry.rank !== undefined) ranks.set(entry.key, entry.rank);
    }
  }
  for (const key of [...neighbors.keys()].sort()) {
    if (ranks.has(key)) continue;
    const used = new Set([...neighbors.get(key)!].map(other => ranks.get(other)));
    let lane = 0;
    while (used.has(lane)) lane++;
    ranks.set(key, lane);
  }
  let width = Math.max(0, ...ranks.values()) + 1;
  const positions = slots.map(entries => {
    const cells = new Map<string, {lane: number; whole: boolean; collision: boolean}>();
    const used = new Set<number>();
    const reserved = new Set(entries.map(entry => ranks.get(entry.key)!));
    for (const entry of [...entries].sort((a,b) => a.key < b.key ? -1 : a.key > b.key ? 1 : 0)) {
      let lane = ranks.get(entry.key)!;
      const collision = used.has(lane);
      if (collision) {
        lane = 0;
        while (used.has(lane) || reserved.has(lane)) lane++;
      }
      used.add(lane);
      width = Math.max(width, lane + 1);
      cells.set(entry.id, {lane, whole: entries.length === 1 && entry.whole, collision});
    }
    return cells;
  });
  return {width, positions};
}
