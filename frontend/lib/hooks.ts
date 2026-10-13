"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { cache } from "./cache";

/**
 * One key per thing the screens load. Two screens that use the same key share the answer.
 * Keys are grouped by prefix so that an action can invalidate a whole group.
 */
export const keys = {
  invoiceList: (status?: string) => `invoices:list:${status || "all"}`,
  reviewQueue: "invoices:queue",
  invoice: (id: number) => `invoice:${id}:detail`,
  audit: (id: number) => `invoice:${id}:audit`,
  parameters: "parameters:current",
  parameterHistory: "parameters:history",
  uploadLimits: "me:upload",
};

// After an action that changes data, what it affects is invalidated, so that an earlier
// state is never shown as the current one (DL-80).

/** A document was uploaded: every list of invoices is out of date. */
export function invoicesChanged(): void {
  cache.invalidate("invoices:");
}

/**
 * An invoice was corrected, decided on or sent to be read again. The lists, the review
 * queue and its audit trail are out of date. `detail` is the invoice as the API returned
 * it from the action; without it, the invoice itself is requested again.
 */
export function invoiceChanged(invoiceId: number, detail?: unknown): void {
  cache.invalidate("invoices:");
  cache.invalidate(keys.audit(invoiceId));
  if (detail === undefined) cache.invalidate(keys.invoice(invoiceId));
  else cache.set(keys.invoice(invoiceId), detail);
}

/** A new version of the parameters was saved; `current` is the one the API returned. */
export function parametersChanged(current: unknown): void {
  cache.set(keys.parameters, current);
  cache.invalidate(keys.parameterHistory);
}

export interface Loaded<T> {
  data: T | undefined;
  error: unknown;
  /** True only while there is nothing to show: no cached answer and no error yet. */
  loading: boolean;
  reload: () => Promise<void>;
  setData: (value: T) => void;
}

/**
 * Loads the value for `key`, stale-while-revalidate: what is already cached is shown at
 * once and a fresh copy is requested in the background, so the loading state appears only
 * when nothing is cached. Screens asking for the same key at the same time share one
 * request. `deps` request it again when they change.
 */
export function useLoad<T>(key: string, load: () => Promise<T>, deps: unknown[] = []): Loaded<T> {
  const loader = useRef(load);
  loader.current = load;
  const subscribe = useCallback((notify: () => void) => cache.subscribe(key, notify), [key]);
  const entry = useSyncExternalStore(
    subscribe,
    () => cache.peek<T>(key),
    () => undefined,
  );
  // The error belongs to a key: one left over from another key is not this one's.
  const [failure, setFailure] = useState<{ key: string; error: unknown }>({ key, error: undefined });
  const error = failure.key === key ? failure.error : undefined;
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const ticket = ++latest.current;
    try {
      await cache.fetch(key, () => loader.current());
      if (ticket === latest.current) setFailure({ key, error: undefined });
    } catch (caught) {
      if (ticket === latest.current) setFailure({ key, error: caught });
    }
  }, [key]);

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, ...deps]);

  // An action elsewhere made this answer out of date while it is on screen: ask again.
  useEffect(
    () =>
      cache.subscribe(key, (event) => {
        if (event === "invalidated") void reload();
      }),
    [key, reload],
  );

  const setData = useCallback((value: T) => cache.set(key, value), [key]);
  return { data: entry?.data, error, loading: entry === undefined && error === undefined, reload, setData };
}

export const POLL_INTERVAL_MS = 3000;

/**
 * Calls `tick` every few seconds while `active` is true and the tab is visible.
 * Used only while something is still being processed, so an idle page makes no requests.
 */
export function usePolling(tick: () => void | Promise<void>, active: boolean, intervalMs = POLL_INTERVAL_MS): void {
  const callback = useRef(tick);
  callback.current = tick;

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void callback.current();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [active, intervalMs]);
}
