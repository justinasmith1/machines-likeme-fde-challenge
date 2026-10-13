// The in-memory cache behind the screens (DL-80), without React.

import { afterEach, describe, expect, test, vi } from "vitest";
import { cache } from "@/lib/cache";

afterEach(() => cache.clear());

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

describe("storing answers", () => {
  test("an answer is kept under its key and given back as the same object until it changes", async () => {
    expect(cache.peek("a")).toBeUndefined();
    await cache.fetch("a", async () => [1, 2]);
    const first = cache.peek<number[]>("a");
    expect(first!.data).toEqual([1, 2]);
    expect(cache.peek("a")).toBe(first);
    await cache.fetch("a", async () => [3]);
    expect(cache.peek<number[]>("a")!.data).toEqual([3]);
    expect(cache.peek("a")).not.toBe(first);
  });

  test("keys are independent", async () => {
    await cache.fetch("a", async () => "A");
    await cache.fetch("b", async () => "B");
    expect([cache.peek("a")!.data, cache.peek("b")!.data]).toEqual(["A", "B"]);
  });

  test("a failed request stores nothing and leaves the previous answer", async () => {
    await cache.fetch("a", async () => "good");
    await expect(cache.fetch("a", async () => Promise.reject(new Error("down")))).rejects.toThrow("down");
    expect(cache.peek("a")!.data).toBe("good");
    // And the key is free again: the next request goes out.
    await cache.fetch("a", async () => "better");
    expect(cache.peek("a")!.data).toBe("better");
  });
});

describe("simultaneous requests for one key become one", () => {
  test("the second caller joins the first request", async () => {
    const answer = deferred<string>();
    const first = vi.fn(() => answer.promise);
    const second = vi.fn(async () => "never used");
    const one = cache.fetch("a", first);
    const two = cache.fetch("a", second);
    expect(two).toBe(one);
    answer.resolve("shared");
    expect(await Promise.all([one, two])).toEqual(["shared", "shared"]);
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).not.toHaveBeenCalled();
  });

  test("requests for different keys are not joined", async () => {
    const a = vi.fn(async () => "A");
    const b = vi.fn(async () => "B");
    await Promise.all([cache.fetch("a", a), cache.fetch("b", b)]);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });

  test("once answered, the next request goes out again", async () => {
    const load = vi.fn(async () => "x");
    await cache.fetch("a", load);
    await cache.fetch("a", load);
    expect(load).toHaveBeenCalledTimes(2);
  });
});

describe("invalidating", () => {
  test("a key nobody is showing is forgotten", async () => {
    await cache.fetch("invoices:queue", async () => ["old"]);
    cache.invalidate("invoices:");
    expect(cache.peek("invoices:queue")).toBeUndefined();
  });

  test("a key on screen keeps its value and its listeners are told to ask again", async () => {
    await cache.fetch("invoices:queue", async () => ["old"]);
    const events: string[] = [];
    const stop = cache.subscribe("invoices:queue", (event) => events.push(event));
    cache.invalidate("invoices:");
    expect(cache.peek("invoices:queue")!.data).toEqual(["old"]);
    expect(events).toEqual(["invalidated"]);
    stop();
  });

  test("if that screen goes away before a fresh answer arrives, the old value goes with it", async () => {
    await cache.fetch("a", async () => "old");
    const stop = cache.subscribe("a", () => {});
    cache.invalidate("a");
    stop();
    expect(cache.peek("a")).toBeUndefined();
  });

  test("a fresh answer makes the key current again", async () => {
    await cache.fetch("a", async () => "old");
    const stop = cache.subscribe("a", () => {});
    cache.invalidate("a");
    await cache.fetch("a", async () => "new");
    stop();
    expect(cache.peek("a")!.data).toBe("new");
  });

  test("an answer requested before the invalidation is dropped, not stored", async () => {
    const before = deferred<string>();
    const old = cache.fetch("a", () => before.promise);
    cache.invalidate("a");
    await cache.fetch("a", async () => "after the change");
    before.resolve("before the change");
    await old;
    expect(cache.peek("a")!.data).toBe("after the change");
  });

  test("after an invalidation a new request goes out instead of joining the old one", async () => {
    const before = deferred<string>();
    void cache.fetch("a", () => before.promise);
    cache.invalidate("a");
    const fresh = vi.fn(async () => "fresh");
    await cache.fetch("a", fresh);
    expect(fresh).toHaveBeenCalledTimes(1);
    before.resolve("late");
  });

  test("only keys under the prefix are touched: invoice 1 is not invoice 10", async () => {
    await cache.fetch("invoice:1:detail", async () => "one");
    await cache.fetch("invoice:10:detail", async () => "ten");
    await cache.fetch("parameters:current", async () => "p");
    cache.invalidate("invoice:1:");
    expect(cache.keys().sort()).toEqual(["invoice:10:detail", "parameters:current"]);
  });
});

describe("setting a value directly", () => {
  test("it replaces the stored answer and tells the listeners", async () => {
    const events: string[] = [];
    const stop = cache.subscribe("a", (event) => events.push(event));
    cache.set("a", "from the action");
    expect(cache.peek("a")!.data).toBe("from the action");
    expect(events).toEqual(["changed"]);
    stop();
  });

  test("a request that was already on its way does not overwrite it", async () => {
    const slow = deferred<string>();
    const old = cache.fetch("a", () => slow.promise);
    cache.set("a", "newer");
    slow.resolve("older");
    await old;
    expect(cache.peek("a")!.data).toBe("newer");
  });
});

describe("clearing", () => {
  test("everything is forgotten, on screen or not", async () => {
    await cache.fetch("a", async () => 1);
    await cache.fetch("b", async () => 2);
    const events: string[] = [];
    const stop = cache.subscribe("a", (event) => events.push(event));
    cache.clear();
    expect(cache.keys()).toEqual([]);
    expect(events).toEqual(["cleared"]);
    stop();
  });

  test("an answer that arrives afterwards is dropped: it belongs to whoever asked before", async () => {
    const slow = deferred<string>();
    const old = cache.fetch("a", () => slow.promise);
    cache.clear();
    slow.resolve("data of the previous person");
    await old;
    expect(cache.peek("a")).toBeUndefined();
  });
});
