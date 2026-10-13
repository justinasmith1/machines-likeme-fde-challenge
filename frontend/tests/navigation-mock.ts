import { vi } from "vitest";

/** Stands in for the Next.js router in tests. Reset before each test in setup.ts. */
export const router = { replace: vi.fn(), push: vi.fn() };

/** The address the page under test is at. Reset before each test in setup.ts. */
export const location = { pathname: "/invoices", search: "" };
