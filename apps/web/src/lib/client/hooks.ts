"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";

export type Query<T> = {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
};

/** GET with abort-on-unmount and manual reload. `path === null` skips the request.
 *  The last data stays visible while a reload is in flight. */
export function useApi<T>(path: string | null): Query<T> {
  const [tick, setTick] = useState(0);
  const key = path === null ? null : `${tick}:${path}`;
  const [state, setState] = useState<{ key: string | null; data: T | undefined; error: ApiError | null }>({
    key: null,
    data: undefined,
    error: null,
  });
  useEffect(() => {
    if (path === null || key === null) return;
    const ctl = new AbortController();
    api<T>(path, { signal: ctl.signal })
      .then((data) => setState({ key, data, error: null }))
      .catch((e) => {
        if ((e as Error).name === "AbortError") return;
        setState((s) => ({ key, data: s.data, error: e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e)) }));
      });
    return () => ctl.abort();
  }, [path, key]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data: state.data, error: state.key === key ? state.error : null, loading: key !== null && state.key !== key, reload };
}

/**
 * Bounded polling: calls `fetcher` every `intervalMs` until `done(result)` is true, the
 * attempt budget is exhausted, or the component unmounts. Never polls forever.
 * `subject` identifies what is being watched (e.g. a run id); a new subject starts over.
 */
export function usePoll<T>(opts: {
  enabled: boolean;
  subject: string;
  fetcher: (signal: AbortSignal) => Promise<T>;
  done: (v: T) => boolean;
  intervalMs?: number;
  maxAttempts?: number;
  onUpdate: (v: T) => void;
}): { polling: boolean; exhausted: boolean; restart: () => void } {
  const { enabled, subject, intervalMs = 3000, maxAttempts = 60 } = opts;
  const ref = useRef(opts);
  useEffect(() => {
    ref.current = opts;
  });
  const [generation, setGeneration] = useState(0);
  const runKey = `${generation}:${subject}`;
  const [finished, setFinished] = useState<{ key: string; exhausted: boolean } | null>(null);
  useEffect(() => {
    if (!enabled) return;
    const ctl = new AbortController();
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      attempts += 1;
      try {
        const v = await ref.current.fetcher(ctl.signal);
        ref.current.onUpdate(v);
        if (ref.current.done(v)) {
          setFinished({ key: runKey, exhausted: false });
          return;
        }
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        // transient errors count against the same bounded budget
      }
      if (attempts >= maxAttempts) {
        setFinished({ key: runKey, exhausted: true });
        return;
      }
      timer = setTimeout(tick, intervalMs);
    };
    timer = setTimeout(tick, intervalMs);
    return () => {
      ctl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [enabled, intervalMs, maxAttempts, runKey]);
  const restart = useCallback(() => setGeneration((g) => g + 1), []);
  const isFinished = finished?.key === runKey;
  return { polling: enabled && !isFinished, exhausted: isFinished && finished!.exhausted, restart };
}
