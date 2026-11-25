export type SelectionProblem = {
  lesson_id: string;
  details: string[];
  category?: 'legacy_marker' | 'heuristic_overlap' | 'missing_baseline';
};

export type SelectionProblemKind = 'legacy' | 'heuristic' | 'missing_baseline' | 'unknown';

const overlapPrefixes = ['Конфликт группы:', 'Конфликт преподавателя:'];
const legacyFlags = new Set(['importError', 'needsClarification']);

export function selectionProblemKind(item: SelectionProblem): SelectionProblemKind {
  if (item.category === 'legacy_marker') return 'legacy';
  if (item.category === 'heuristic_overlap') return 'heuristic';
  if (item.category === 'missing_baseline') return 'missing_baseline';
  if (item.lesson_id === 'selected-template'
      || item.details.some(detail => detail.startsWith('Историческая ревизия недоступна.'))) return 'missing_baseline';
  if (item.details.length > 0
      && item.details.every(detail => overlapPrefixes.some(prefix => detail.startsWith(prefix)))) return 'heuristic';
  if (item.details.some(detail => legacyFlags.has(detail))) return 'legacy';
  return 'unknown';
}

export function groupSelectionProblems(items: SelectionProblem[]) {
  return {
    legacy: items.filter(item => selectionProblemKind(item) === 'legacy'),
    heuristic: items.filter(item => selectionProblemKind(item) === 'heuristic'),
    missingBaseline: items.filter(item => selectionProblemKind(item) === 'missing_baseline'),
    unknown: items.filter(item => selectionProblemKind(item) === 'unknown'),
  };
}

export function primaryRevisionActionState(value: {
  primary: boolean;
  schoolTemplate: boolean;
  selectionStale: boolean;
  draftRevision: number;
  summaryRevision: number;
  editorRevision: number;
  dirty: boolean;
  saving: boolean;
  readOnly: boolean;
}) {
  const visible = value.primary && value.schoolTemplate && value.selectionStale;
  const revisionsMatch = value.draftRevision === value.summaryRevision
    && value.draftRevision === value.editorRevision;
  return {
    visible,
    revisionsMatch,
    enabled: visible && revisionsMatch && !value.dirty && !value.saving && !value.readOnly,
  };
}
