export type CampusLevel = 'closed' | 'peek' | 'expanded' | 'detail';
export type CampusBuilding = 'today' | 'schedule' | 'homework' | 'grades' | 'information' | 'profile';
export type CampusNavigationState = {
  building: CampusBuilding | null;
  level: CampusLevel;
  detailKey: string | null;
  detailContext: string | null;
  scrollTop: number;
  historyDepth: number;
  scopeId: string;
};
export type CampusNavigationAction =
  | { type: 'open-building'; building: CampusBuilding; scopeId?: string }
  | { type: 'expand' }
  | { type: 'collapse' }
  | { type: 'open-detail'; detailKey: string; scrollTop?: number; detailContext?: string | null }
  | { type: 'close-detail' }
  | { type: 'close' }
  | { type: 'reset'; scopeId?: string }
  | { type: 'save-scroll'; scrollTop: number }
  | { type: 'restore'; state: CampusNavigationState };

export const initialCampusNavigation: CampusNavigationState = {
  building: null, level: 'closed', detailKey: null, detailContext: null, scrollTop: 0, historyDepth: 0, scopeId: 'root',
};

export function campusNavigationReducer(state: CampusNavigationState, action: CampusNavigationAction): CampusNavigationState {
  switch (action.type) {
    case 'open-building': return { building: action.building, level: 'peek', detailKey: null, detailContext: null, scrollTop: 0, historyDepth: state.building ? 0 : state.historyDepth + 1, scopeId: action.scopeId || state.scopeId };
    case 'expand': return state.building && state.level === 'peek' ? { ...state, level: 'expanded', historyDepth: state.historyDepth + 1 } : state;
    case 'collapse': return state.building && state.level === 'expanded' ? { ...state, level: 'peek', detailKey: null } : state;
    case 'open-detail': return state.building && state.level === 'expanded' ? { ...state, level: 'detail', detailKey: action.detailKey, detailContext: action.detailContext ?? null, scrollTop: action.scrollTop ?? state.scrollTop, historyDepth: state.historyDepth + 1 } : state;
    case 'close-detail': return state.level === 'detail' ? { ...state, level: 'expanded', detailKey: null, detailContext: null } : state;
    case 'close': return { ...initialCampusNavigation, scopeId: state.scopeId };
    case 'reset': return { ...initialCampusNavigation, scopeId: action.scopeId || state.scopeId };
    case 'save-scroll': return { ...state, scrollTop: Math.max(0, action.scrollTop) };
    case 'restore': return action.state;
  }
}

export function campusNavigationFromHistory(value: unknown): CampusNavigationState {
  if (!value || typeof value !== 'object') return initialCampusNavigation;
  const candidate = value as Partial<CampusNavigationState>;
  const buildings: CampusBuilding[] = ['today', 'schedule', 'homework', 'grades', 'information', 'profile'];
  const levels: CampusLevel[] = ['closed', 'peek', 'expanded', 'detail'];
  if (!levels.includes(candidate.level as CampusLevel) || (candidate.building !== null && !buildings.includes(candidate.building as CampusBuilding))) return initialCampusNavigation;
  if (candidate.level === 'closed' && candidate.building !== null) return initialCampusNavigation;
  if (candidate.level !== 'closed' && !candidate.building) return initialCampusNavigation;
  if (candidate.level === 'detail' && (typeof candidate.detailKey !== 'string' || !candidate.detailKey)) return initialCampusNavigation;
  return {
    building: candidate.building as CampusBuilding | null,
    level: candidate.level as CampusLevel,
    detailKey: typeof candidate.detailKey === 'string' ? candidate.detailKey : null,
    detailContext: typeof candidate.detailContext === 'string' && candidate.detailContext ? candidate.detailContext : null,
    scrollTop: typeof candidate.scrollTop === 'number' && Number.isFinite(candidate.scrollTop) ? Math.max(0, candidate.scrollTop) : 0,
    historyDepth: typeof candidate.historyDepth === 'number' && Number.isFinite(candidate.historyDepth) ? Math.max(0, candidate.historyDepth) : 0,
    scopeId: typeof candidate.scopeId === 'string' && candidate.scopeId ? candidate.scopeId : 'root',
  };
}

export function campusNavigationForContext(state: CampusNavigationState, scheduleContext: string): CampusNavigationState {
  return state.building === 'schedule' && state.level === 'detail' && state.detailContext && state.detailContext !== scheduleContext
    ? { ...state, level: 'expanded', detailKey: null, detailContext: null }
    : state;
}
