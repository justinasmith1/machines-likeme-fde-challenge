// The loading hook: what is cached is shown at once and refreshed in the background (DL-80).

import { act, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { cache } from "@/lib/cache";
import { invoiceChanged, invoicesChanged, keys, parametersChanged, useLoad, usePolling } from "@/lib/hooks";

afterEach(() => {
  cache.clear();
  vi.useRealTimers();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

/** Shows what a screen would: the loading state, the error, or the data. */
function Probe({ name = "probe", cacheKey, load, poll = false }: { name?: string; cacheKey: string; load: () => Promise<string>; poll?: boolean }) {
  const loaded = useLoad(cacheKey, load);
  usePolling(loaded.reload, poll);
  return (
    <div data-testid={name}>
      {loaded.loading ? "loading" : ""}
      {loaded.error !== undefined ? `error: ${(loaded.error as Error).message}` : ""}
      {loaded.data !== undefined ? ` data: ${loaded.data}` : ""}
      <button type="button" onClick={() => void loaded.reload()}>
        reload {name}
      </button>
    </div>
  );
}

const text = (name = "probe") => screen.getByTestId(name).firstChild?.textContent?.trim() ?? "";
const shown = (name = "probe") => screen.getByTestId(name).textContent!.replace(`reload ${name}`, "").trim();
const settle = () => act(async () => {});

describe("first visit", () => {
  test("shows the loading state until the answer arrives", async () => {
    const answer = deferred<string>();
    render(<Probe cacheKey="a" load={() => answer.promise} />);
    expect(shown()).toBe("loading");
    await act(async () => answer.resolve("first"));
    expect(shown()).toBe("data: first");
    expect(text()).not.toBe("loading");
  });

  test("an error with nothing cached is shown, and is not the loading state", async () => {
    render(<Probe cacheKey="a" load={async () => Promise.reject(new Error("down"))} />);
    await settle();
    expect(shown()).toBe("error: down");
  });
});

describe("going back to a view", () => {
  test("what was loaded is on screen at once, with no loading state, while a fresh copy is requested", async () => {
    const first = render(<Probe cacheKey="a" load={async () => "first"} />);
    await settle();
    first.unmount();

    const fresh = deferred<string>();
    const load = vi.fn(() => fresh.promise);
    render(<Probe cacheKey="a" load={load} />);
    // Immediately, before any answer:
    expect(shown()).toBe("data: first");
    await settle();
    expect(load).toHaveBeenCalledTimes(1); // revalidated in the background
    expect(shown()).toBe("data: first");

    await act(async () => fresh.resolve("second"));
    expect(shown()).toBe("data: second");
  });

  test("if the background request fails, the cached data stays and the error is shown with it", async () => {
    const first = render(<Probe cacheKey="a" load={async () => "first"} />);
    await settle();
    first.unmount();
    render(<Probe cacheKey="a" load={async () => Promise.reject(new Error("down"))} />);
    await settle();
    expect(shown()).toBe("error: down data: first");
  });

  test("another key has nothing cached: it loads", async () => {
    const first = render(<Probe cacheKey="a" load={async () => "first"} />);
    await settle();
    first.unmount();
    const pending = deferred<string>();
    render(<Probe cacheKey="b" load={() => pending.promise} />);
    expect(shown()).toBe("loading");
  });

  test("changing the key on a mounted screen does not carry the other key's data or error over", async () => {
    const view = render(<Probe cacheKey="a" load={async () => Promise.reject(new Error("down"))} />);
    await settle();
    expect(shown()).toBe("error: down");
    const pending = deferred<string>();
    view.rerender(<Probe cacheKey="b" load={() => pending.promise} />);
    expect(shown()).toBe("loading");
    await act(async () => pending.resolve("b data"));
    expect(shown()).toBe("data: b data");
  });
});

describe("simultaneous requests for the same key", () => {
  test("two screens showing the same thing make one request and both get the answer", async () => {
    const one = vi.fn(async () => "shared");
    const two = vi.fn(async () => "shared");
    render(
      <>
        <Probe name="one" cacheKey="a" load={one} />
        <Probe name="two" cacheKey="a" load={two} />
      </>,
    );
    await settle();
    expect(one.mock.calls.length + two.mock.calls.length).toBe(1);
    expect(shown("one")).toBe("data: shared");
    expect(shown("two")).toBe("data: shared");
  });

  test("reloading while a request is on its way does not start another", async () => {
    const answer = deferred<string>();
    const load = vi.fn(() => answer.promise);
    render(<Probe cacheKey="a" load={load} />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "reload probe" }));
    fireEvent.click(screen.getByRole("button", { name: "reload probe" }));
    await settle();
    expect(load).toHaveBeenCalledTimes(1);
    await act(async () => answer.resolve("x"));
    fireEvent.click(screen.getByRole("button", { name: "reload probe" }));
    await settle();
    expect(load).toHaveBeenCalledTimes(2);
  });

  test("React's double mount in development (Strict Mode) results in one request, not two", async () => {
    const load = vi.fn(async () => "x");
    render(
      <StrictMode>
        <Probe cacheKey="a" load={load} />
      </StrictMode>,
    );
    await settle();
    expect(load).toHaveBeenCalledTimes(1);
    expect(shown()).toBe("data: x");
  });
});

describe("after an action that changes data", () => {
  test("a view that is not on screen forgets its data: the next visit loads, it does not show the old state", async () => {
    const first = render(<Probe cacheKey={keys.reviewQueue} load={async () => "one invoice waiting"} />);
    await settle();
    first.unmount();

    invoiceChanged(1, { id: 1 });

    const fresh = deferred<string>();
    render(<Probe cacheKey={keys.reviewQueue} load={() => fresh.promise} />);
    expect(shown()).toBe("loading");
    await act(async () => fresh.resolve("nothing waiting"));
    expect(shown()).toBe("data: nothing waiting");
  });

  test("a view that is on screen asks again by itself and does not go blank meanwhile", async () => {
    let answer = "before";
    const load = vi.fn(async () => answer);
    render(<Probe cacheKey={keys.reviewQueue} load={load} />);
    await settle();
    answer = "after";
    act(() => invoicesChanged());
    expect(shown()).toBe("data: before"); // not "loading"
    await settle();
    expect(load).toHaveBeenCalledTimes(2);
    expect(shown()).toBe("data: after");
  });

  test("an answer requested before the action is not shown as the current state", async () => {
    const before = deferred<string>();
    const after = deferred<string>();
    const load = vi.fn().mockReturnValueOnce(before.promise).mockReturnValueOnce(after.promise);
    render(<Probe cacheKey={keys.reviewQueue} load={load} />);
    await settle();
    act(() => invoicesChanged()); // the first request is still on its way
    await settle();
    expect(load).toHaveBeenCalledTimes(2); // a new one went out instead of waiting for the old
    await act(async () => after.resolve("state after the action"));
    await act(async () => before.resolve("state before the action"));
    expect(shown()).toBe("data: state after the action");
  });

  test("each action invalidates what it affects and nothing else", () => {
    const seed = () => {
      cache.clear();
      for (const key of [
        keys.invoiceList(),
        keys.invoiceList("HELD_FOR_REVIEW"),
        keys.reviewQueue,
        keys.invoice(1),
        keys.audit(1),
        keys.invoice(10),
        keys.audit(10),
        keys.parameters,
        keys.parameterHistory,
        keys.uploadLimits,
      ]) {
        cache.set(key, "old");
      }
    };
    const left = () => cache.keys().filter((key) => cache.peek(key)!.data === "old").sort();
    const OTHER_INVOICE = ["invoice:10:audit", "invoice:10:detail"];
    const PARAMETERS = ["parameters:current", "parameters:history"];

    seed(); // upload
    invoicesChanged();
    expect(left()).toEqual(["invoice:1:audit", "invoice:1:detail", ...OTHER_INVOICE, "me:upload", ...PARAMETERS].sort());

    seed(); // correction or decision: the API returned the invoice as it is now
    invoiceChanged(1, "as returned by the action");
    expect(left()).toEqual([...OTHER_INVOICE, "me:upload", ...PARAMETERS]);
    expect(cache.peek(keys.invoice(1))!.data).toBe("as returned by the action");

    seed(); // retry: nothing returned, the invoice is requested again
    invoiceChanged(1);
    expect(left()).toEqual([...OTHER_INVOICE, "me:upload", ...PARAMETERS]);
    expect(cache.peek(keys.invoice(1))).toBeUndefined();

    seed(); // new version of the parameters
    parametersChanged("version 2");
    expect(cache.peek(keys.parameters)!.data).toBe("version 2");
    expect(cache.peek(keys.parameterHistory)).toBeUndefined();
    expect(left()).toEqual(
      [
        "invoice:1:audit", "invoice:1:detail", ...OTHER_INVOICE,
        "invoices:list:HELD_FOR_REVIEW", "invoices:list:all", "invoices:queue", "me:upload",
      ].sort(),
    );
  });

  test("keys: one per list filter, per invoice and per kind of data", () => {
    expect(keys.invoiceList()).toBe("invoices:list:all");
    expect(keys.invoiceList("")).toBe("invoices:list:all");
    expect(keys.invoiceList("REJECTED")).toBe("invoices:list:REJECTED");
    expect(keys.invoice(7)).toBe("invoice:7:detail");
    expect(keys.audit(7)).toBe("invoice:7:audit");
  });
});

describe("when the cache is emptied (sign out, another person)", () => {
  test("a screen still mounted stops showing the data", async () => {
    render(<Probe cacheKey="a" load={async () => "of the previous person"} />);
    await settle();
    act(() => cache.clear());
    expect(shown()).toBe("loading");
  });

  test("the next visit starts from nothing", async () => {
    const first = render(<Probe cacheKey="a" load={async () => "of the previous person"} />);
    await settle();
    first.unmount();
    cache.clear();
    const pending = deferred<string>();
    render(<Probe cacheKey="a" load={() => pending.promise} />);
    expect(shown()).toBe("loading");
  });
});

describe("polling", () => {
  test("keeps asking at its interval while active, and each answer replaces what is shown", async () => {
    vi.useFakeTimers();
    let count = 0;
    const load = vi.fn(async () => `answer ${++count}`);
    const view = render(<Probe cacheKey="a" load={load} poll />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(shown()).toBe("data: answer 1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(shown()).toBe("data: answer 2");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(load).toHaveBeenCalledTimes(4);
    expect(shown()).toBe("data: answer 4");

    // Not active any more: no further requests.
    view.rerender(<Probe cacheKey="a" load={load} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(load).toHaveBeenCalledTimes(4);
  });
});
