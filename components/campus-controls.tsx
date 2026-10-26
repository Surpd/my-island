'use client';
import { useCallback, useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import './campus-controls.css';

export type CampusRole = 'student' | 'teacher' | 'admin';
const roleNames: Record<string, string> = { student: 'Ученик', teacher: 'Учитель', admin: 'Администратор' };
export function CampusRoleSwitch({ roles, value, onChange }: { roles: string[]; value: string; onChange: (role: CampusRole) => void }) {
  const available = roles.filter((role) => role in roleNames);
  if (available.length < 2) return null;
  return <label className="campus-role-switch"><span>Моя роль</span><select aria-label="Моя роль" value={value} onChange={(event) => onChange(event.target.value as CampusRole)}>{available.map((role) => <option key={role} value={role}>{roleNames[role]}</option>)}</select></label>;
}
export const dateISO = (date = new Date()) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
export function periodFor(day: string) {
  const start = new Date(`${day}T12:00:00`); start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  const end = new Date(start); end.setDate(end.getDate() + 6);
  return { start: dateISO(start), end: dateISO(end) };
}
export function CampusPeriod({ day, onChange }: { day: string; onChange: (day: string) => void }) {
  const shift = (amount: number) => { const next = new Date(`${day}T12:00:00`); next.setDate(next.getDate() + amount); onChange(dateISO(next)); };
  return <div className="campus-period"><button aria-label="Предыдущая неделя" onClick={() => shift(-7)}><ChevronLeft size={20} /></button><label>Дата расписания<input aria-label="Дата расписания" type="date" value={day} onChange={(event) => { if (/^\d{4}-\d{2}-\d{2}$/.test(event.target.value)) onChange(event.target.value); }} /></label><button aria-label="Следующая неделя" onClick={() => shift(7)}><ChevronRight size={20} /></button><button onClick={() => onChange(dateISO())}>Сегодня</button></div>;
}
export function useCampusScreen<T extends string>(allowed: readonly string[]) {
  const read = useCallback(() => {
    const screen = new URLSearchParams(window.location.search).get('screen');
    return screen && allowed.includes(screen) ? screen as T : null;
  }, [allowed]);
  const [view, update] = useState<T | null>(() => typeof window === 'undefined' ? null : read());
  useEffect(() => { const sync = () => update(read()); window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync); }, [read]);
  const setView = useCallback((next: T | null) => {
    const url = new URL(window.location.href); if (next) url.searchParams.set('screen', next); else url.searchParams.delete('screen');
    window.history.pushState({}, '', `${url.pathname}${url.search}${url.hash}`); update(next);
  }, []);
  useTelegramBack(Boolean(view), () => setView(null));
  return [view, setView] as const;
}
export function useTelegramBack(visible: boolean, back: () => void) {
  useEffect(() => {
    const app = window.Telegram?.WebApp;
    const button = app?.BackButton;
    if (!button || window.parent !== window || (app?.isVersionAtLeast && !app.isVersionAtLeast('6.1'))) return;
    if (visible) { button.show(); button.onClick(back); } else button.hide();
    return () => { button.offClick(back); button.hide(); };
  }, [visible, back]);
}
