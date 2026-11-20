type Entry = { id: string; key: string; rank?: number };

export function subgroupRank(name: string, subgroup?: string) {
  const numbered = /^(?:grade[0-9]+:split:|english:)([1-9][0-9]*)$/.exec(name);
  if (numbered) return Number(numbered[1]) - 1;
  const math = /^grade9-math-([ABC])$/.exec(name);
  if (math) return 'ABC'.indexOf(math[1]);
  const value = subgroup?.trim().toUpperCase() || '';
  if (/^[1-9][0-9]*$/.test(value)) return Number(value) - 1;
  if (/^[A-Z]$/.test(value)) return value.charCodeAt(0) - 65;
}

export function timetableLanes(slots: Entry[][]) {
  const width = Math.max(1, ...slots.map(slot => slot.length));
  const positions = slots.map(entries => {
    const ordered = [...entries].sort((a, b) =>
      (a.rank ?? 100) - (b.rank ?? 100) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    return new Map(ordered.map((entry, index) => [entry.id, index]));
  });
  return {width, positions};
}
