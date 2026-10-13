// Shared by the screen tests: rendering inside the providers, and small actions.
// Navigation is mocked in tests/setup.ts.

import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach } from "vitest";
import { Providers } from "@/components/Providers";
import type { ApiClient } from "@/lib/api/client";
import type { Role } from "@/lib/api/types";
import type { Session } from "@/lib/auth";
import { cache } from "@/lib/cache";

export { router } from "./navigation-mock";

// What one test loaded must not be on screen in the next.
afterEach(() => cache.clear());

export const session = (role: Role, username = "ana.analyst"): Session => ({ username, role });

export function show(api: ApiClient, ui: ReactNode) {
  return render(
    <Providers apiBaseUrl="/api" client={api}>
      {ui}
    </Providers>,
  );
}

export function chooseFile(name: string, content = "%PDF-1.7", type = "application/pdf") {
  const input = screen.getByLabelText("Document") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File([content], name, { type })] } });
}
