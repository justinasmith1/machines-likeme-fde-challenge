// Microsoft Entra ID through Azure Container Apps authentication: who is signed in comes
// from /.auth/me, the role is the app role with the most privilege, and the provider is
// chosen by NEXT_PUBLIC_AUTH_MODE.

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  API_ME_URL,
  AUTH_ME_URL,
  SIGN_OUT_URL,
  createEntraAuthProvider,
  loadSession,
  roleFrom,
  sessionFromAuthMe,
} from "@/lib/auth/entra";
import { router } from "./navigation-mock";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

const ROLE_URI = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role";

/** An answer of /.auth/me as Easy Auth gives it. Fictional people only. */
function authMe(roles: string[], extra: Array<{ typ: string; val: string }> = [], userId: string | null = "ana@contoso.example") {
  return [
    {
      provider_name: "aad",
      ...(userId === null ? {} : { user_id: userId }),
      user_claims: [
        { typ: "aud", val: "00000000-0000-0000-0000-000000000000" },
        ...roles.map((val) => ({ typ: "roles", val })),
        ...extra,
      ],
    },
  ];
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

/** A fetch that answers each URL with what the test says. */
function fakeFetch(answers: Record<string, () => Response>) {
  return vi.fn(async (...args: Parameters<typeof fetch>) => {
    const url = String(args[0]);
    const answer = answers[url];
    if (!answer) throw new Error(`unexpected request to ${url}`);
    return answer();
  });
}

describe("reading /.auth/me", () => {
  test("a valid role gives a session with the sign-in name and that role", () => {
    for (const role of ["INTAKE", "AP_ANALYST", "AP_MANAGER"] as const) {
      expect(sessionFromAuthMe(authMe([role]))).toEqual({ username: "ana@contoso.example", role });
    }
  });

  test("no roles: no session", () => {
    expect(sessionFromAuthMe(authMe([]))).toBeNull();
    expect(sessionFromAuthMe([{ user_id: "ana@contoso.example" }])).toBeNull();
  });

  test("a role this application does not know: no session", () => {
    expect(sessionFromAuthMe(authMe(["Global Administrator", "ADMIN"]))).toBeNull();
  });

  test("several roles: the one with the most privilege wins, whatever the order", () => {
    expect(sessionFromAuthMe(authMe(["INTAKE", "AP_MANAGER", "AP_ANALYST"]))?.role).toBe("AP_MANAGER");
    expect(sessionFromAuthMe(authMe(["INTAKE", "AP_ANALYST"]))?.role).toBe("AP_ANALYST");
    expect(sessionFromAuthMe(authMe(["AP_ANALYST", "INTAKE"]))?.role).toBe("AP_ANALYST");
    expect(sessionFromAuthMe(authMe(["SOMETHING_ELSE", "INTAKE"]))?.role).toBe("INTAKE");
    expect(roleFrom([])).toBeNull();
    expect(roleFrom([" ap_manager ", 7, null])).toBe("AP_MANAGER");
  });

  test("roles are read from either claim type and from a plain 'roles' list", () => {
    expect(sessionFromAuthMe(authMe([], [{ typ: ROLE_URI, val: "AP_ANALYST" }]))?.role).toBe("AP_ANALYST");
    expect(sessionFromAuthMe({ user_id: "ana@contoso.example", roles: ["INTAKE", "AP_MANAGER"] })?.role).toBe("AP_MANAGER");
  });

  test("the sign-in name in the claims is preferred to user_id; with neither there is no session", () => {
    const withClaim = authMe(["INTAKE"], [{ typ: "preferred_username", val: "ines@contoso.example" }], "opaque-id");
    expect(sessionFromAuthMe(withClaim)?.username).toBe("ines@contoso.example");
    expect(sessionFromAuthMe(authMe(["INTAKE"], [{ typ: "name", val: "Ines Intake" }], null))?.username).toBe("Ines Intake");
    expect(sessionFromAuthMe(authMe(["INTAKE"], [], null))).toBeNull();
  });

  test.each([null, undefined, "", 7, [], [null], {}, [{ user_claims: "nope" }], [{ user_claims: [null, 3, { typ: "roles" }] }]])(
    "an answer that is not what is expected gives no session (%j)",
    (payload) => {
      expect(sessionFromAuthMe(payload)).toBeNull();
    },
  );
});

describe("asking who is signed in", () => {
  test("/.auth/me with a valid role: that session, and the backend is not asked", async () => {
    const fetchImpl = fakeFetch({ [AUTH_ME_URL]: () => json(authMe(["AP_ANALYST"])) });
    expect(await loadSession(fetchImpl)).toEqual({ username: "ana@contoso.example", role: "AP_ANALYST" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0][1]).toMatchObject({ credentials: "same-origin" });
  });

  test("/.auth/me answers 401: nobody is signed in", async () => {
    const fetchImpl = fakeFetch({ [AUTH_ME_URL]: () => new Response("", { status: 401 }) });
    expect(await loadSession(fetchImpl)).toBeNull();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  test("/.auth/me names a person without a role: no session, and the backend is not asked", async () => {
    const fetchImpl = fakeFetch({ [AUTH_ME_URL]: () => json(authMe(["SOMETHING_ELSE"])) });
    expect(await loadSession(fetchImpl)).toBeNull();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  test("/.auth/me does not exist: the backend's reading of the same identity is used", async () => {
    const fetchImpl = fakeFetch({
      [AUTH_ME_URL]: () => new Response("Not Found", { status: 404 }),
      [API_ME_URL]: () => json({ username: "max@contoso.example", role: "AP_MANAGER", upload: { max_mb: 10 } }),
    });
    expect(await loadSession(fetchImpl)).toEqual({ username: "max@contoso.example", role: "AP_MANAGER" });
  });

  test("/.auth/me answers an empty list: the backend is asked too", async () => {
    const fetchImpl = fakeFetch({
      [AUTH_ME_URL]: () => json([]),
      [API_ME_URL]: () => json({ username: "ines@contoso.example", role: "INTAKE" }),
    });
    expect(await loadSession(fetchImpl)).toEqual({ username: "ines@contoso.example", role: "INTAKE" });
  });

  test("neither knows the person: no session", async () => {
    const refused = json({ error: { code: "UNAUTHENTICATED", message: "Your account has no role in this application." } }, 401);
    expect(
      await loadSession(fakeFetch({ [AUTH_ME_URL]: () => new Response("", { status: 404 }), [API_ME_URL]: () => refused })),
    ).toBeNull();
    const odd = fakeFetch({ [AUTH_ME_URL]: () => json([]), [API_ME_URL]: () => json({ username: "x", role: "ROOT" }) });
    expect(await loadSession(odd)).toBeNull();
  });

  test("no answer at all is treated as signed out, never as signed in", async () => {
    const failing = vi.fn(async () => {
      throw new TypeError("network down");
    });
    expect(await loadSession(failing as unknown as typeof fetch)).toBeNull();
  });
});

describe("the provider", () => {
  test("is not known yet, then tells its listeners once the answer arrives", async () => {
    const fetchImpl = fakeFetch({ [AUTH_ME_URL]: () => json(authMe(["AP_MANAGER"])) });
    const provider = createEntraAuthProvider({ fetchImpl });
    const listener = vi.fn();
    provider.subscribe(listener);

    expect(provider.mode).toBe("entra");
    expect(provider.getSession()).toBeUndefined();
    await waitFor(() => expect(listener).toHaveBeenCalledTimes(1));
    expect(provider.getSession()).toEqual({ username: "ana@contoso.example", role: "AP_MANAGER" });
    // Asked once, however many times the screens look.
    provider.getSession();
    provider.subscribe(() => {});
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  test("with 401 the session is null, which the app shows as 'no access'", async () => {
    const provider = createEntraAuthProvider({ fetchImpl: fakeFetch({ [AUTH_ME_URL]: () => new Response("", { status: 401 }) }) });
    const listener = vi.fn();
    provider.subscribe(listener);
    await waitFor(() => expect(listener).toHaveBeenCalled());
    expect(provider.getSession()).toBeNull();
  });

  test("adds no header to API calls: the browser's session cookie authenticates them", () => {
    const provider = createEntraAuthProvider({ fetchImpl: fakeFetch({ [AUTH_ME_URL]: () => json(authMe(["INTAKE"])) }) });
    expect(provider.authHeaders()).toEqual({});
  });

  test("signing out forgets the session and sends the browser to Azure's sign-out", async () => {
    const navigate = vi.fn();
    const provider = createEntraAuthProvider({ fetchImpl: fakeFetch({ [AUTH_ME_URL]: () => json(authMe(["INTAKE"])) }), navigate });
    const listener = vi.fn();
    provider.subscribe(listener);
    await waitFor(() => expect(provider.getSession()).toBeTruthy());

    provider.signOut();
    expect(provider.getSession()).toBeNull();
    expect(navigate).toHaveBeenCalledWith(SIGN_OUT_URL);
    expect(SIGN_OUT_URL).toBe("/.auth/logout?post_logout_redirect_uri=/");
    expect(listener).toHaveBeenCalledTimes(2);
  });
});

describe("which provider the app uses", () => {
  async function authWith(mode: string | undefined) {
    vi.resetModules();
    if (mode === undefined) vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", undefined as unknown as string);
    else vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", mode);
    vi.stubGlobal("fetch", fakeFetch({ [AUTH_ME_URL]: () => json(authMe(["AP_ANALYST"])) }));
    return import("@/lib/auth");
  }
  afterEach(() => vi.unstubAllGlobals());

  test("NEXT_PUBLIC_AUTH_MODE=entra: Entra, and the development notice is off", async () => {
    const { auth, isDevelopmentLogin } = await authWith("entra");
    expect(auth.mode).toBe("entra");
    expect(isDevelopmentLogin).toBe(false);
    expect(auth.authHeaders()).toEqual({});
  });

  test.each([undefined, "", "development", "ENTRA", "production"])("%j: the development login", async (mode) => {
    const { auth, isDevelopmentLogin } = await authWith(mode);
    expect(auth.mode).toBe("development");
    expect(isDevelopmentLogin).toBe(true);
  });

  test("in Entra mode a stored development session authenticates nobody", async () => {
    window.sessionStorage.setItem("contoso.dev-session", JSON.stringify({ username: "mallory", role: "AP_MANAGER" }));
    const { auth } = await authWith("entra");
    expect(auth.getSession()).toBeUndefined(); // still asking Azure, not reading the storage
    await waitFor(() => expect(auth.getSession()).toEqual({ username: "ana@contoso.example", role: "AP_ANALYST" }));
  });
});

describe("the login page in Entra mode", () => {
  async function loginPage(me: () => Response) {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "entra");
    vi.stubGlobal("fetch", fakeFetch({ [AUTH_ME_URL]: me }));
    const { default: LoginPage } = await import("@/app/login/page");
    const { Providers } = await import("@/components/Providers");
    return render(
      <Providers apiBaseUrl="/api">
        <LoginPage />
      </Providers>,
    );
  }
  afterEach(() => vi.unstubAllGlobals());

  test("has no development login; without a role it says so and offers Microsoft sign-in", async () => {
    await loginPage(() => json(authMe(["SOMETHING_ELSE"])));
    expect(await screen.findByText("You do not have access.")).toBeTruthy();
    expect(screen.queryByLabelText("User name")).toBeNull();
    expect(screen.queryByText(/Development login only/)).toBeNull();
    expect(screen.getByRole("link", { name: "Sign in with Microsoft" }).getAttribute("href")).toBe(
      "/.auth/login/aad?post_login_redirect_uri=/",
    );
    expect(screen.getByRole("link", { name: "Sign out and use another account" }).getAttribute("href")).toBe(SIGN_OUT_URL);
  });

  test("someone with a role is sent to their start page", async () => {
    await loginPage(() => json(authMe(["AP_ANALYST"])));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/review"));
    expect(screen.queryByText("You do not have access.")).toBeNull();
  });
});
