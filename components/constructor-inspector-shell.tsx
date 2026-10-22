'use client';

import {useEffect, useRef, type ReactNode} from 'react';

/** Desktop aside; mobile editor with focus containment and focus return. */
export function ConstructorInspectorShell({children, onClose}: {children: ReactNode; onClose: () => void}) {
  const element = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; }, [onClose]);
  useEffect(() => {
    const node = element.current;
    if (!node) return;
    const media = window.matchMedia('(max-width: 900px)');
    const previous = document.activeElement as HTMLElement | null;
    const background = [...(node.closest('.constructor')?.querySelectorAll<HTMLElement>('.constructor-top,.constructor-workspaces,.constructor-command-bar,.constructor-grid-wrap,.constructor-filters,.constructor-footer') || [])];
    const previousInert = new Map(background.map(item => [item,item.inert]));
    const focusable = () => [...node.querySelectorAll<HTMLElement>('button,input,select,textarea,a[href],[tabindex]')].filter(item => !item.hasAttribute('disabled') && item.getClientRects().length && item.tabIndex >= 0);
    const focusEditor = () => (node.querySelector<HTMLElement>('.constructor-mobile-quick')?.getClientRects().length ? node.querySelector<HTMLElement>('.constructor-mobile-quick') : focusable()[0])?.focus();
    const update = () => {
      node.setAttribute('role', media.matches ? 'dialog' : 'complementary');
      if (media.matches) { node.setAttribute('aria-modal', 'true'); document.body.classList.add('constructor-editor-open'); focusEditor(); }
      else { node.removeAttribute('aria-modal'); document.body.classList.remove('constructor-editor-open'); }
      background.forEach(item => { item.inert = media.matches || previousInert.get(item) || false; });
    };
    const keydown = (event: KeyboardEvent) => {
      if (!media.matches) return;
      if (document.querySelector('.constructor-conflict-backdrop')) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close.current(); }
      if (event.key === 'Tab') {
        const items = focusable();
        if (!items.length) { event.preventDefault(); node.focus(); return; }
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
        else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
      }
    };
    const contain = (event: FocusEvent) => { if (media.matches && !node.contains(event.target as Node) && !document.querySelector('.constructor-conflict-backdrop')) focusEditor(); };
    let viewportFrame = 0;
    const viewport = window.visualViewport;
    const fitViewport = () => {
      cancelAnimationFrame(viewportFrame);
      viewportFrame = requestAnimationFrame(() => {
        if (!media.matches) return;
        node.style.setProperty('--constructor-editor-height', `${Math.max(180, (viewport?.height || window.innerHeight) - 12)}px`);
        node.style.setProperty('--constructor-editor-top', `${(viewport?.offsetTop || 0) + 6}px`);
        const active = document.activeElement;
        if (active instanceof HTMLElement && node.contains(active) && active.matches('input,textarea,select')) active.scrollIntoView({block:'nearest'});
      });
    };
    update(); media.addEventListener('change', update); document.addEventListener('keydown', keydown, true); document.addEventListener('focusin', contain);
    fitViewport(); viewport?.addEventListener('resize', fitViewport); viewport?.addEventListener('scroll', fitViewport); window.addEventListener('resize', fitViewport);
    return () => { cancelAnimationFrame(viewportFrame); viewport?.removeEventListener('resize', fitViewport); viewport?.removeEventListener('scroll', fitViewport); window.removeEventListener('resize', fitViewport); media.removeEventListener('change', update); document.removeEventListener('keydown', keydown, true); document.removeEventListener('focusin', contain); background.forEach(item=>{item.inert=previousInert.get(item)||false;}); document.body.classList.remove('constructor-editor-open'); if (media.matches && previous?.isConnected) previous.focus(); };
  }, []);
  return <aside ref={element} className="constructor-inspector" aria-label="Редактор занятия" tabIndex={-1}>{children}</aside>;
}
