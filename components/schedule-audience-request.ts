export type AudienceRequestState =
  | { status: 'idle' | 'pending' | 'complete' }
  | { status: 'unavailable'; reason: 'timeout' | 'request' };

export function scopedAudienceKey(workspace: string, draftId: string | undefined, inputs: string): string {
  return inputs && draftId ? JSON.stringify([workspace, draftId]) + ":" + inputs : '';
}

export function audienceRequestState(
  key: string,
  attempt: number,
  completed: { key: string; attempt: number } | null,
  failed: { key: string; attempt: number; reason: 'timeout' | 'request' } | null,
  enabled: boolean,
): AudienceRequestState {
  if (!key) return { status: 'idle' };
  if (failed?.key === key && failed.attempt === attempt) return { status: 'unavailable', reason: failed.reason };
  if (completed?.key === key && completed.attempt === attempt) return { status: 'complete' };
  return { status: enabled ? 'pending' : 'idle' };
}

export function prepareAudienceRequest<T>({
  delayMs,
  timeoutMs = 10_000,
  enqueue,
  load,
  isPaused,
  complete,
  fail,
}: {
  delayMs: number;
  timeoutMs?: number;
  enqueue: (task: () => Promise<void>) => Promise<void>;
  load: (signal: AbortSignal) => Promise<T>;
  isPaused: () => boolean;
  complete: (value: T) => void;
  fail: (reason: 'timeout' | 'request') => void;
}): () => void {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    void enqueue(async () => {
      if (controller.signal.aborted || isPaused()) return;
      let timedOut = false;
      const stop = new Promise<never>((_, reject) => {
        controller.signal.addEventListener('abort', () => reject(new Error('Audience preview cancelled')), { once: true });
      });
      const deadline = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, timeoutMs);
      try {
        const response = Promise.resolve().then(() => {
          if (controller.signal.aborted || isPaused()) throw new Error('Audience preview cancelled');
          return load(controller.signal);
        });
        const value = await Promise.race([response, stop]);
        if (!controller.signal.aborted && !isPaused()) complete(value);
      } catch {
        if ((timedOut || !controller.signal.aborted) && !isPaused()) fail(timedOut ? 'timeout' : 'request');
      } finally {
        clearTimeout(deadline);
      }
    });
  }, delayMs);
  return () => {
    clearTimeout(timer);
    controller.abort();
  };
}

export function audienceRequestMessage(state: AudienceRequestState): string {
  if (state.status === 'unavailable') return state.reason === 'timeout'
    ? 'Расчёт участников не ответил вовремя. Правки не потеряны.'
    : 'Расчёт участников недоступен. Правки не потеряны.';
  return 'Считаем участников…';
}
