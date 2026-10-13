// What the screens have already loaded, kept in memory so that going back to a view shows
// it at once while a fresh copy is requested (stale-while-revalidate, DL-80).
//
// It lives in this module, for the life of the page: nothing is written to the browser's
// storage, and a full page load starts empty. It holds JSON answers of the API only; the
// original document of an invoice is never put here.

export type CacheEvent = "changed" | "invalidated" | "cleared";
type Listener = (event: CacheEvent) => void;

/** A stored answer. A new object every time the value changes, so React can compare it. */
export interface CacheEntry<T> {
  readonly data: T;
}

const entries = new Map<string, CacheEntry<unknown>>();
/** Requests on their way, by key. A second caller for the same key joins the first. */
const flights = new Map<string, Promise<unknown>>();
const listeners = new Map<string, Set<Listener>>();
/** Kept only because a screen is showing them; they go when that screen does. */
const stale = new Set<string>();

function emit(key: string, event: CacheEvent): void {
  listeners.get(key)?.forEach((listener) => listener(event));
}

function peek<T>(key: string): CacheEntry<T> | undefined {
  return entries.get(key) as CacheEntry<T> | undefined;
}

function store(key: string, value: unknown): void {
  entries.set(key, { data: value });
  stale.delete(key);
  emit(key, "changed");
}

/**
 * Requests the value for a key. While a request for that key is on its way, every other
 * caller gets the same one. The answer is stored unless the key was invalidated, set or
 * the cache was cleared in the meantime: then it describes a state that is no longer
 * current (or another person's data) and is dropped.
 */
function fetch<T>(key: string, load: () => Promise<T>): Promise<T> {
  const running = flights.get(key);
  if (running) return running as Promise<T>;
  const flight: Promise<T> = Promise.resolve()
    .then(load)
    .then(
      (value) => {
        if (flights.get(key) === flight) {
          flights.delete(key);
          store(key, value);
        }
        return value;
      },
      (error: unknown) => {
        if (flights.get(key) === flight) flights.delete(key);
        throw error;
      },
    );
  flights.set(key, flight);
  return flight;
}

/** Stores a value the caller already has, for example the answer to an action. */
function set<T>(key: string, value: T): void {
  flights.delete(key); // an older request must not overwrite it when it answers
  store(key, value);
}

/**
 * Marks every key that starts with `prefix` as no longer current. A key no screen is
 * showing is forgotten, so the next visit loads it. A key on screen keeps its value and
 * its listeners are told, so the screen requests it again without going blank.
 */
function invalidate(prefix: string): void {
  const keys = new Set([...entries.keys(), ...flights.keys(), ...listeners.keys()]);
  for (const key of keys) {
    if (!key.startsWith(prefix)) continue;
    flights.delete(key);
    if (listeners.get(key)?.size) {
      if (entries.has(key)) stale.add(key);
      emit(key, "invalidated");
    } else {
      entries.delete(key);
    }
  }
}

/** Forgets everything, including answers still on their way. Used when the person changes. */
function clear(): void {
  entries.clear();
  flights.clear();
  stale.clear();
  for (const key of [...listeners.keys()]) emit(key, "cleared");
}

function subscribe(key: string, listener: Listener): () => void {
  let set = listeners.get(key);
  if (!set) listeners.set(key, (set = new Set()));
  set.add(listener);
  return () => {
    set.delete(listener);
    if (set.size > 0) return;
    listeners.delete(key);
    // Invalidated while on screen and never refreshed: do not keep it for the next visit.
    if (stale.delete(key)) entries.delete(key);
  };
}

export const cache = { peek, fetch, set, invalidate, clear, subscribe, keys: () => [...entries.keys()] };
