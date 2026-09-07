import { useEffect, useRef } from 'react';

type Destination = {
  id: string;
  focusAsset?: string;
  camera?: { x: number; y: number; scale: number };
};
const clear = 'blur(0px) brightness(1)';
const masked = 'blur(22px) brightness(1.035)';

/** The two rasters share one optical envelope; their overlap is only 56ms. */
export function useSceneMotion(selected: Destination | undefined) {
  const master = useRef<HTMLDivElement>(null);
  const closeup = useRef<HTMLDivElement>(null);
  const optics = useRef<HTMLDivElement>(null);
  const current = useRef<Destination | undefined>(undefined);
  const id = selected?.id;
  const asset = selected?.focusAsset;
  const x = selected?.camera?.x ?? 50;
  const y = selected?.camera?.y ?? 50;
  const scale = selected?.camera?.scale ?? 1.45;

  useEffect(() => {
    const base = master.current;
    const focus = closeup.current;
    const lens = optics.current;
    if (!base || !focus || !lens) return;
    let cancelled = false;
    const animations: Animation[] = [];
    const destination = id
      ? { id, focusAsset: asset, camera: { x, y, scale } }
      : undefined;
    const previous = current.current;
    const run = async () => {
      if (destination) lens.dataset.motion = 'loading';
      if (asset) {
        const image = new Image();
        image.src = asset;
        try {
          await image.decode();
        } catch {
          lens.dataset.motion = 'idle';
          return;
        }
        if (cancelled) return;
      }
      if (!destination && !previous) return;
      const camera = destination?.camera ||
        previous?.camera || { x: 50, y: 50, scale: 1.45 };
      const framing = `translate(${(50 - camera.x) * 0.14}%, ${(38 - camera.y) * 0.14}%) scale(${camera.scale})`;
      const approach = `translate(${(50 - camera.x) * 0.14}%, ${(38 - camera.y) * 0.14}%) scale(${camera.scale * 1.12})`;
      const reduced = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      ).matches;
      const duration = reduced ? 100 : 700;
      base.style.transformOrigin = `${camera.x}% ${camera.y}%`;
      if (asset) focus.style.backgroundImage = `url("${asset}")`;
      lens.dataset.motion = destination ? 'enter' : 'leave';
      const animate = (element: HTMLElement, frames: Keyframe[]) => {
        const animation = element.animate(frames, {
          duration,
          fill: 'forwards',
          easing: 'linear',
        });
        animations.push(animation);
        return animation;
      };
      if (reduced) {
        base.style.transform = 'scale(1.01)';
        base.style.opacity = '1';
        focus.style.transform = 'scale(1.01)';
        animate(focus, [
          { opacity: destination ? 0 : 1 },
          { opacity: destination ? 1 : 0 },
        ]);
      } else {
        animate(
          lens,
          reduced
            ? [{ filter: clear }, { filter: clear }]
            : [
                { filter: clear, offset: 0 },
                {
                  filter: 'blur(1px) brightness(1)',
                  offset: 0.2,
                  easing: 'ease-in',
                },
                { filter: masked, offset: 0.44 },
                {
                  filter: masked,
                  offset: 0.56,
                  easing: 'cubic-bezier(.16,1,.3,1)',
                },
                { filter: clear, offset: 0.9 },
                { filter: clear, offset: 1 },
              ],
        );
        if (destination) {
          animate(base, [
            {
              transform: 'scale(1.01)',
              opacity: 1,
              offset: 0,
              easing: 'cubic-bezier(.55,.05,.8,.45)',
            },
            { transform: approach, opacity: 1, offset: 0.46 },
            { transform: approach, opacity: 1, offset: 0.54 },
            { transform: framing, opacity: 0, offset: 1 },
          ]);
          animate(focus, [
            { transform: 'scale(1.14) translateY(1%)', opacity: 0, offset: 0 },
            {
              transform: 'scale(1.14) translateY(1%)',
              opacity: 0,
              offset: 0.46,
            },
            {
              transform: 'scale(1.12) translateY(.8%)',
              opacity: 1,
              offset: 0.54,
              easing: 'cubic-bezier(.16,1,.3,1)',
            },
            { transform: 'scale(1.01)', opacity: 1, offset: 1 },
          ]);
        } else {
          animate(focus, [
            {
              transform: 'scale(1.01)',
              opacity: 1,
              offset: 0,
              easing: 'ease-in',
            },
            {
              transform: 'scale(.96) translateY(.8%)',
              opacity: 1,
              offset: 0.46,
            },
            {
              transform: 'scale(.96) translateY(.8%)',
              opacity: 0,
              offset: 0.54,
            },
            { transform: 'scale(.96)', opacity: 0, offset: 1 },
          ]);
          animate(base, [
            { transform: approach, opacity: 1, offset: 0 },
            {
              transform: approach,
              opacity: 1,
              offset: 0.5,
              easing: 'cubic-bezier(.16,1,.3,1)',
            },
            { transform: 'scale(1.01)', opacity: 1, offset: 1 },
          ]);
        }
      }
      current.current = destination;
      // Reproducible frame captures use the actual browser animation timeline.
      if (
        (import.meta as ImportMeta & { env?: { DEV?: boolean } }).env?.DEV &&
        new URLSearchParams(location.search).has('qaMotion')
      ) {
        animations.forEach((animation) => {
          animation.pause();
          animation.currentTime = 0;
        });
      }
      try {
        await Promise.all(animations.map((animation) => animation.finished));
      } catch {
        return;
      }
      if (cancelled) return;
      animations.forEach((animation) => {
        animation.commitStyles();
        animation.cancel();
      });
      lens.dataset.motion = 'idle';
    };
    void run();
    return () => {
      cancelled = true;
      animations.forEach((animation) => {
        try {
          animation.commitStyles();
        } catch {
          /* Detached during role switch. */
        }
        animation.cancel();
      });
    };
  }, [id, asset, x, y, scale]);
  return { masterRef: master, closeupRef: closeup, opticsRef: optics };
}
