// Same-origin access to the backend: /api/* on the frontend is forwarded to the API.
// All the logic, and the single place where identity is passed on, is in lib/proxy/forward.
//
// This runs on the Node.js server for every request, so API_PROXY_TARGET is read at
// runtime. Used when API_BASE_URL=/api; an absolute API_BASE_URL bypasses it.

import { forwardToBackend } from "@/lib/proxy/forward";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path: string[] }> };

async function handle(request: Request, context: Context): Promise<Response> {
  const { path } = await context.params;
  return forwardToBackend(request, path);
}

export {
  handle as DELETE,
  handle as GET,
  handle as HEAD,
  handle as OPTIONS,
  handle as PATCH,
  handle as POST,
  handle as PUT,
};
