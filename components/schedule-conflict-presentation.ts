type PairIssue = {
  lessonId: string;
  related?: string;
  category: string;
  type: string;
  severity: 'error' | 'warning';
  message: string;
  code?: string;
  pairCause?: string;
};

export type OverlapInterval = { day: number; start: string; end: string };

export function overlapInterval(a?: OverlapInterval, b?: OverlapInterval): OverlapInterval | undefined {
  if (!a || !b || a.day !== b.day || !a.start || !b.start || !a.end || !b.end) return;
  const start = a.start > b.start ? a.start : b.start;
  const end = a.end < b.end ? a.end : b.end;
  if (start < end) return { day: a.day, start, end };
}

// This is a view over the existing diagnostics. Keep each distinct cause and
// its original issue object for the existing resolution actions.
export function groupConflictPresentation<T extends PairIssue>(
  issues: T[], intervalFor: (issue: T) => OverlapInterval | undefined,
) {
  const pairs = new Map<string, { key: string; ids: [string, string]; interval: OverlapInterval; reasons: Map<string, T>; members: T[] }>();
  const singles: T[] = [];
  for (const issue of issues) {
    const interval = issue.category === 'conflict' && issue.related && issue.related !== issue.lessonId ? intervalFor(issue) : undefined;
    if (!interval || !issue.related) {
      singles.push(issue);
      continue;
    }
    const ids: [string, string] = issue.lessonId < issue.related ? [issue.lessonId, issue.related] : [issue.related, issue.lessonId];
    const key = JSON.stringify([...ids, interval.day, interval.start, interval.end]);
    let pair = pairs.get(key);
    if (!pair) {
      pair = { key, ids, interval, reasons: new Map(), members: [] };
      pairs.set(key, pair);
    }
    pair.members.push(issue);
    const reason = JSON.stringify([issue.type, issue.code || issue.pairCause || issue.message]);
    const previous = pair.reasons.get(reason);
    if (!previous || (issue.severity === 'error' && previous.severity === 'warning')) pair.reasons.set(reason, issue);
  }
  return { singles, pairs: [...pairs.values()].map(pair => ({ ...pair, reasons: [...pair.reasons.values()] })) };
}
