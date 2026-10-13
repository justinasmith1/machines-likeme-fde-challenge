import { act, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { auth, isDevelopmentLogin, useSession } from "@/lib/auth";
import { devSignIn } from "@/lib/auth/dev";
import { cache } from "@/lib/cache";

const KEY = "contoso.dev-session";

describe("development identity", () => {
  test("nobody is signed in to start with, and no headers are sent", () => {
    expect(auth.getSession()).toBeNull();
    expect(auth.authHeaders()).toEqual({});
  });

  test("signing in stores the session and produces the development headers", () => {
    const session = devSignIn("  ana.analyst ", "AP_ANALYST");
    expect(session).toEqual({ username: "ana.analyst", role: "AP_ANALYST" });
    expect(auth.getSession()).toEqual(session);
    expect(auth.authHeaders()).toEqual({ "X-Dev-User": "ana.analyst", "X-Dev-Role": "AP_ANALYST" });
    expect(JSON.parse(window.sessionStorage.getItem(KEY)!)).toEqual(session);
  });

  test("signing out clears the session and the headers", () => {
    devSignIn("max", "AP_MANAGER");
    auth.signOut();
    expect(auth.getSession()).toBeNull();
    expect(auth.authHeaders()).toEqual({});
    expect(window.sessionStorage.getItem(KEY)).toBeNull();
  });

  test("a name is required and the role must be a known one", () => {
    expect(() => devSignIn("   ", "INTAKE")).toThrow();
    expect(() => devSignIn("eve", "ADMIN" as never)).toThrow();
    expect(auth.getSession()).toBeNull();
  });

  test.each(["not json", "{}", '{"username":"eve","role":"ADMIN"}', '{"username":"","role":"INTAKE"}'])(
    "a stored value that is not a valid session (%s) counts as signed out",
    (stored) => {
      window.sessionStorage.setItem(KEY, stored);
      expect(auth.getSession()).toBeNull();
      expect(auth.authHeaders()).toEqual({});
    },
  );

  test("the same session object is returned while nothing changes", () => {
    devSignIn("ines", "INTAKE");
    expect(auth.getSession()).toBe(auth.getSession());
  });

  test("subscribers hear about sign in and sign out", () => {
    const listener = vi.fn();
    const unsubscribe = auth.subscribe(listener);
    devSignIn("ines", "INTAKE");
    auth.signOut();
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    devSignIn("ines", "INTAKE");
    expect(listener).toHaveBeenCalledTimes(2);
  });

  test("the app knows it is running with the development login", () => {
    expect(auth.mode).toBe("development");
    expect(isDevelopmentLogin).toBe(true);
  });
});

describe("useSession", () => {
  function Who() {
    const session = useSession();
    return <p>{session ? `${session.username}/${session.role}` : "signed out"}</p>;
  }

  test("follows sign in and sign out", () => {
    render(<Who />);
    expect(screen.getByText("signed out")).toBeTruthy();
    act(() => {
      devSignIn("max", "AP_MANAGER");
    });
    expect(screen.getByText("max/AP_MANAGER")).toBeTruthy();
    act(() => auth.signOut());
    expect(screen.getByText("signed out")).toBeTruthy();
  });
});

describe("what was loaded does not survive a change of person (DL-80)", () => {
  const seed = async () => {
    await cache.fetch("invoices:queue", async () => ["an invoice only reviewers may see"]);
    expect(cache.keys()).toEqual(["invoices:queue"]);
  };

  test("signing out empties the cache", async () => {
    devSignIn("ana", "AP_ANALYST");
    await seed();
    auth.signOut();
    expect(cache.keys()).toEqual([]);
  });

  test("signing in with another role empties it, without signing out first", async () => {
    devSignIn("ana", "AP_ANALYST");
    await seed();
    devSignIn("ana", "INTAKE");
    expect(cache.keys()).toEqual([]);
  });

  test("another person with the same role empties it too", async () => {
    devSignIn("ines", "INTAKE");
    await seed();
    devSignIn("ivan", "INTAKE");
    expect(cache.keys()).toEqual([]);
  });

  test("the same person signing in again keeps it", async () => {
    devSignIn("ana", "AP_ANALYST");
    await seed();
    devSignIn("ana", "AP_ANALYST");
    expect(cache.keys()).toEqual(["invoices:queue"]);
    cache.clear();
  });

  test("an answer still on its way when the person changes is not stored for the next one", async () => {
    devSignIn("max", "AP_MANAGER");
    let answer!: (value: string[]) => void;
    const pending = cache.fetch("invoice:1:audit", () => new Promise<string[]>((resolve) => (answer = resolve)));
    await Promise.resolve();
    devSignIn("ines", "INTAKE");
    answer(["audit trail of the manager"]);
    await pending;
    expect(cache.keys()).toEqual([]);
  });
});
