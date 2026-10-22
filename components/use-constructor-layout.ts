'use client';
import {useEffect, type RefObject} from 'react';

/** Presentation measurements only; does not alter slots, column widths or lessons. */
export function useConstructorLayout(root: RefObject<HTMLDivElement | null>, draftId?: string) {
  useEffect(() => {
    if (!draftId || !root.current) return;
    const node = root.current;
    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const grid = node.querySelector<HTMLElement>('.constructor-grid-wrap');
        if (!grid) return;
        const height = `${Math.max(220, window.innerHeight - Math.max(0, grid.getBoundingClientRect().top) - 34)}px`;
        if (node.style.getPropertyValue('--constructor-grid-height') !== height) node.style.setProperty('--constructor-grid-height', height);
      });
    };
    const outside = (event: PointerEvent) => node.querySelectorAll<HTMLDetailsElement>('details[data-ui-menu][open]').forEach(menu => { if (!menu.contains(event.target as Node)) menu.open = false; });
    const observer = new ResizeObserver(measure);
    const mutations = new MutationObserver(measure);
    observer.observe(node);
    mutations.observe(node, {childList:true, subtree:true});
    node.querySelectorAll('.constructor-top,.constructor-toolbar,.constructor-filters').forEach(item => observer.observe(item));
    window.addEventListener('resize', measure); window.addEventListener('scroll', measure, {capture:true,passive:true}); document.addEventListener('pointerdown', outside); measure();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); mutations.disconnect(); window.removeEventListener('resize', measure); window.removeEventListener('scroll', measure, true); document.removeEventListener('pointerdown', outside); };
  }, [root, draftId]);
}
