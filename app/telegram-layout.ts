import { useEffect } from 'react';

/** One bridge subscription for every role, including auth and modal screens. */
export function useTelegramLayout() {
  useEffect(() => {
    let detach = () => {};
    let timer: ReturnType<typeof setTimeout>;
    let disposed = false;
    const connect = () => {
      if (disposed) return;
      const app = window.Telegram?.WebApp;
      if (!app) {
        timer = setTimeout(connect, 100);
        return;
      }
      const root = document.documentElement;
      const update = () => {
        const height = app.viewportStableHeight || app.viewportHeight;
        if (height) root.style.setProperty('--telegram-height', `${height}px`);
        for (const edge of ['top', 'right', 'bottom', 'left'] as const) {
          const safe = app.safeAreaInset?.[edge];
          const content = app.contentSafeAreaInset?.[edge];
          if (safe !== undefined)
            root.style.setProperty(
              `--tg-safe-${edge}`,
              `${Math.max(0, safe)}px`,
            );
          if (content !== undefined)
            root.style.setProperty(
              `--tg-content-safe-${edge}`,
              `${Math.max(0, content)}px`,
            );
        }
        // Local fixture also survives delayed SDK arrival and viewport events.
        if (
          (import.meta as ImportMeta & { env?: { DEV?: boolean } }).env?.DEV
        ) {
          const query = new URLSearchParams(location.search);
          if (query.has('qaFullscreen')) {
            root.style.setProperty(
              '--tg-safe-top',
              `${Number(query.get('qaDeviceTop') || 47)}px`,
            );
            root.style.setProperty(
              '--tg-content-safe-top',
              `${Number(query.get('qaChromeTop') || 56)}px`,
            );
          }
        }
        root.dataset.telegramFullscreen = String(Boolean(app.isFullscreen));
      };
      const events = [
        'viewportChanged',
        'fullscreenChanged',
        'fullscreenFailed',
        'safeAreaChanged',
        'contentSafeAreaChanged',
        'activated',
      ];
      events.forEach((event) => app.onEvent?.(event, update));
      update();
      // Unsupported methods can be exposed by older bridges. Keep expanded fallback.
      for (const action of [app.ready, app.expand, app.disableVerticalSwipes]) {
        try {
          action?.call(app);
        } catch {
          /* Older client. */
        }
      }
      if (!app.isFullscreen) {
        try {
          app.requestFullscreen?.();
        } catch {
          /* Expanded mode remains usable. */
        }
      }
      detach = () => events.forEach((event) => app.offEvent?.(event, update));
    };
    connect();
    return () => {
      disposed = true;
      clearTimeout(timer);
      detach();
    };
  }, []);
}
