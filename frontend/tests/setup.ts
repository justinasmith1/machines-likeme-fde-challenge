import { cleanup } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, vi } from "vitest";
import { location, router } from "./navigation-mock";

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => location.pathname,
  useSearchParams: () => new URLSearchParams(location.search),
  useParams: () => ({ id: "1" }),
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) =>
    createElement("a", { href, ...rest }, children),
}));

beforeEach(() => {
  router.replace.mockClear();
  router.push.mockClear();
  location.pathname = "/invoices";
  location.search = "";
  if (typeof URL !== "undefined") {
    // jsdom does not implement blob URLs; the document viewer only needs a string back.
    URL.createObjectURL = vi.fn(() => "blob:document");
    URL.revokeObjectURL = vi.fn();
  }
});

afterEach(() => {
  // The live API check runs in a Node environment, with no DOM.
  if (typeof window === "undefined") return;
  cleanup();
  window.sessionStorage.clear();
});
