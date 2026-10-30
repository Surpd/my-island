'use client';

import {useEffect, useRef, type ReactNode} from 'react';

/** One persistent editor drawer; closing it never unmounts or resets its form state. */
export function ConstructorInspectorShell({children, onClose, open}: {children: ReactNode; onClose: () => void; open: boolean}) {
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
    const focusEditor = () => focusable()[0]?.focus();
    const restoreFocus = () => node.closest('.constructor-layout')?.querySelector<HTMLElement>('.constructor-drawer-tab')?.focus();
    const update = () => {
      node.classList.toggle('is-open', open);
      node.inert = !open;
      node.setAttribute('aria-hidden', String(!open));
      node.setAttribute('role', media.matches && open ? 'dialog' : 'complementary');
      if (media.matches && open) {
        node.setAttribute('aria-modal', 'true');
        document.body.classList.add('constructor-editor-open');
        background.forEach(item => { item.inert = true; });
        focusEditor();
      } else {
        node.removeAttribute('aria-modal');
        document.body.classList.remove('constructor-editor-open');
        background.forEach(item => { item.inert = previousInert.get(item) || false; });
      }
    };
    const keydown = (event: KeyboardEvent) => {
      if (!open) return;
      if (document.querySelector('.constructor-conflict-backdrop, .constructor-audience-recalculation[open]')) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close.current(); restoreFocus(); }
      if (!media.matches) return;
      if (event.key === 'Tab') {
        const items = focusable();
        if (!items.length) { event.preventDefault(); node.focus(); return; }
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
        else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
      }
    };
    const contain = (event: FocusEvent) => { if (media.matches && open && !node.contains(event.target as Node) && !document.querySelector('.constructor-conflict-backdrop, .constructor-audience-recalculation[open]')) focusEditor(); };
    let viewportFrame = 0;
    const viewport = window.visualViewport;
    const fitViewport = () => {
      cancelAnimationFrame(viewportFrame);
      viewportFrame = requestAnimationFrame(() => {
        if (!media.matches || !open) return;
        node.style.setProperty('--constructor-editor-height', `${Math.max(180, (viewport?.height || window.innerHeight) - 12)}px`);
        node.style.setProperty('--constructor-editor-top', `${(viewport?.offsetTop || 0) + 6}px`);
        const active = document.activeElement;
        if (active instanceof HTMLElement && node.contains(active) && active.matches('input,textarea,select')) active.scrollIntoView({block:'nearest'});
      });
    };
    update(); media.addEventListener('change', update); document.addEventListener('keydown', keydown, true); document.addEventListener('focusin', contain);
    if (open) fitViewport();
    viewport?.addEventListener('resize', fitViewport); viewport?.addEventListener('scroll', fitViewport); window.addEventListener('resize', fitViewport);
    return () => { cancelAnimationFrame(viewportFrame); viewport?.removeEventListener('resize', fitViewport); viewport?.removeEventListener('scroll', fitViewport); window.removeEventListener('resize', fitViewport); media.removeEventListener('change', update); document.removeEventListener('keydown', keydown, true); document.removeEventListener('focusin', contain); background.forEach(item=>{item.inert=previousInert.get(item)||false;}); document.body.classList.remove('constructor-editor-open'); if (media.matches && previous?.isConnected) previous.focus(); };
  }, [open]);
  return <aside id="constructor-inspector-drawer" ref={element} className={`constructor-inspector${open ? ' is-open' : ''}`} aria-label="Редактор занятия" tabIndex={-1}>{children}</aside>;
}
