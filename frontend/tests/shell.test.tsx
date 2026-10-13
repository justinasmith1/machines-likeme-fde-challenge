// Development login, the frame around every page, and how errors are shown.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import LoginPage from "@/app/login/page";
import { AppShell } from "@/components/AppShell";
import { ErrorNotice } from "@/components/Feedback";
import { ApiError } from "@/lib/api/errors";
import { auth } from "@/lib/auth";
import { devSignIn } from "@/lib/auth/dev";
import { createFakeApi } from "./fake-api";
import { router, show } from "./screen-helpers";

describe("development login", () => {
  test("says clearly that it is for development only", () => {
    show(createFakeApi({ role: "INTAKE" }).api, <LoginPage />);
    const warning = screen.getByRole("note");
    expect(warning.textContent).toContain("Development login only");
    expect(warning.textContent).toContain("identity is not verified");
  });

  test("signing in stores the session, checks it with the API and goes to the role's start page", async () => {
    const { api, calls } = createFakeApi({ role: "AP_MANAGER", username: "max" });
    show(api, <LoginPage />);
    fireEvent.change(screen.getByLabelText("User name"), { target: { value: "max" } });
    fireEvent.click(screen.getByLabelText("AP Manager"));
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/review"));
    expect(calls.me).toHaveBeenCalledTimes(1);
    expect(auth.getSession()).toEqual({ username: "max", role: "AP_MANAGER" });
  });

  test("a user name is required", () => {
    const { api, calls } = createFakeApi({ role: "INTAKE" });
    show(api, <LoginPage />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("alert").textContent).toBe("Enter a user name.");
    expect(calls.me).not.toHaveBeenCalled();
  });

  test("when the API refuses the development login, it explains why and nobody stays signed in", async () => {
    const { api, calls } = createFakeApi({ role: "INTAKE" });
    calls.me.mockRejectedValueOnce(new ApiError(401, "UNAUTHENTICATED", "Authentication is not configured for this environment."));
    show(api, <LoginPage />);
    fireEvent.change(screen.getByLabelText("User name"), { target: { value: "ines" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("APP_ENV=development"));
    expect(auth.getSession()).toBeNull();
    expect(router.replace).not.toHaveBeenCalled();
  });
});

describe("shell", () => {
  test("without a session it sends the person to the login and shows nothing else", async () => {
    show(createFakeApi({ role: "INTAKE" }).api, <AppShell title="Invoices">{() => <p>secret content</p>}</AppShell>);
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("secret content")).toBeNull();
  });

  test("navigation follows the role, and the development warning is always visible", () => {
    devSignIn("ines", "INTAKE");
    show(createFakeApi({ role: "INTAKE" }).api, <AppShell title="Invoices">{() => <p>content</p>}</AppShell>);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Invoices"]);
    expect(screen.getByRole("note").textContent).toContain("Development login");
    expect(screen.getByText(/ines · Intake/)).toBeTruthy();
  });

  test("a page for other roles explains itself instead of showing its content", () => {
    devSignIn("ines", "INTAKE");
    show(
      createFakeApi({ role: "INTAKE" }).api,
      <AppShell title="Parameters" roles={["AP_ANALYST", "AP_MANAGER"]}>
        {() => <p>parameters content</p>}
      </AppShell>,
    );
    expect(screen.queryByText("parameters content")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("Not available for your role");
  });

  test("signing out clears the session", () => {
    devSignIn("ana", "AP_ANALYST");
    show(createFakeApi({ role: "AP_ANALYST" }).api, <AppShell title="x">{() => <p>content</p>}</AppShell>);
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(auth.getSession()).toBeNull();
    expect(router.replace).toHaveBeenCalledWith("/login");
  });
});

describe("errors", () => {
  test("an unauthenticated answer offers to sign in again", () => {
    devSignIn("ana", "AP_ANALYST");
    show(createFakeApi({ role: "AP_ANALYST" }).api, <ErrorNotice error={new ApiError(401, "UNAUTHENTICATED", "raw")} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in again" }));
    expect(auth.getSession()).toBeNull();
    expect(router.replace).toHaveBeenCalledWith("/login");
  });

  test("a crash inside the app never shows its raw text", () => {
    show(
      createFakeApi({ role: "AP_ANALYST" }).api,
      <ErrorNotice error={new TypeError("Cannot read properties of undefined (reading 'lines')\n    at render (app.js:42)")} />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Unexpected error. Something went wrong.");
    expect(alert.textContent).not.toContain("undefined");
    expect(alert.textContent).not.toContain("app.js");
  });
});
