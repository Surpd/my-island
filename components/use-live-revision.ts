import { useEffect, useRef } from 'react';

type Marker = { actor: string; version?: string; revision?: number };
type Options = {
  enabled: boolean;
  path: string;
  api: <T>(path: string, options?: RequestInit) => Promise<T>;
  refresh: (marker: Marker, signal: AbortSignal) => Promise<void | false>;
  revoke: () => void;
  missing?: () => void;
  unavailable?: () => void;
};

// One request in flight per visible editor. A failed refresh leaves the marker
// unacknowledged, so reconnect/focus can recover it without a data write.
export function useLiveRevision({ enabled, path, api, refresh, revoke, missing, unavailable }: Options) {
  const handlers = useRef({ refresh, revoke, missing, unavailable });
  useEffect(() => { handlers.current = { refresh, revoke, missing, unavailable }; }, [refresh, revoke, missing, unavailable]);
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let current: AbortController | undefined;
    let lastVersion: string | number | undefined;
    let actor: string | undefined;
    let initialFailure = false;
    const tick = async () => {
      if (current || stopped || document.hidden || !navigator.onLine) return;
      const controller = new AbortController();
      current = controller;
      try {
        const marker = await api<Marker>(path, { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (actor !== undefined && actor !== marker.actor) {
          stopped = true; controller.abort(); handlers.current.revoke(); return;
        }
        actor = marker.actor;
        const version = marker.version ?? marker.revision;
        if (version !== undefined && lastVersion !== version) {
          const applied = await handlers.current.refresh(marker, controller.signal);
          if (!controller.signal.aborted && applied !== false) lastVersion = version;
        }
      } catch (cause) {
        if (!controller.signal.aborted && (cause as { status?: number })?.status === 404 && handlers.current.missing) {
          stopped = true; controller.abort(); handlers.current.missing(); return;
        }
        if (!controller.signal.aborted && [401, 403].includes((cause as { status?: number })?.status ?? 0)) {
          stopped = true; controller.abort(); handlers.current.revoke();
        } else if (!initialFailure && lastVersion === undefined && !controller.signal.aborted) {
          initialFailure = true;
          handlers.current.unavailable?.();
        }
      } finally { if (current === controller) current = undefined; }
    };
    const onEnvironmentChange = () => {
      current?.abort();
      current = undefined;
      void tick();
    };
    void tick();
    const interval = window.setInterval(tick, 15000);
    window.addEventListener('focus', onEnvironmentChange);
    window.addEventListener('online', onEnvironmentChange);
    window.addEventListener('offline', onEnvironmentChange);
    document.addEventListener('visibilitychange', onEnvironmentChange);
    return () => {
      stopped = true;
      current?.abort();
      window.clearInterval(interval);
      window.removeEventListener('focus', onEnvironmentChange);
      window.removeEventListener('online', onEnvironmentChange);
      window.removeEventListener('offline', onEnvironmentChange);
      document.removeEventListener('visibilitychange', onEnvironmentChange);
    };
  }, [enabled, path, api]);
}
