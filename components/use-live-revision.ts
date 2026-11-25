import { useEffect, useRef } from 'react';

type Marker = { actor: string; version?: string; revision?: number };
type Options = {
  enabled: boolean;
  path: string;
  api: <T>(path: string, options?: RequestInit) => Promise<T>;
  refresh: (marker: Marker, signal: AbortSignal) => Promise<void | false>;
  revoke: () => void;
};

// One request in flight per visible editor. A failed refresh leaves the marker
// unacknowledged, so reconnect/focus can recover it without a data write.
export function useLiveRevision({ enabled, path, api, refresh, revoke }: Options) {
  const handlers = useRef({ refresh, revoke });
  useEffect(() => { handlers.current = { refresh, revoke }; }, [refresh, revoke]);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let lastVersion: string | number | undefined;
    let actor: string | undefined;
    let busy = false;
    const tick = async () => {
      if (busy || controller.signal.aborted || document.hidden || !navigator.onLine) return;
      busy = true;
      try {
        const marker = await api<Marker>(path, { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (actor !== undefined && actor !== marker.actor) {
          controller.abort(); handlers.current.revoke(); return;
        }
        actor = marker.actor;
        const version = marker.version ?? marker.revision;
        if (version !== undefined && lastVersion !== version) {
          const applied = await handlers.current.refresh(marker, controller.signal);
          if (!controller.signal.aborted && applied !== false) lastVersion = version;
        }
      } catch (cause) {
        if (!controller.signal.aborted && [401, 403].includes((cause as { status?: number })?.status ?? 0)) {
          controller.abort(); handlers.current.revoke();
        }
      } finally { busy = false; }
    };
    void tick();
    const interval = window.setInterval(tick, 15000);
    window.addEventListener('focus', tick);
    window.addEventListener('online', tick);
    document.addEventListener('visibilitychange', tick);
    return () => {
      controller.abort(); window.clearInterval(interval);
      window.removeEventListener('focus', tick); window.removeEventListener('online', tick);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [enabled, path, api]);
}
