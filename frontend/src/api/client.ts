/**
 Typed API client (openapi-fetch over the generated schema) with the
 persona identity headers the API RBAC checks. The PersonaProvider pushes
 header updates here; every request carries them.
*/
import createClient from "openapi-fetch";
import type { paths } from "./schema";

let identityHeaders: Record<string, string> = {
  "X-IO-User": "maria@invoiceops",
  "X-IO-Role": "analyst",
};

export function setApiIdentityHeaders(headers: Record<string, string>): void {
  identityHeaders = headers;
}

// Absolute base keeps the polyfilled Request happy under jsdom while the
// same-origin path still flows through the dev proxy in browsers.
const API_BASE =
  typeof window !== "undefined" && window.location
    ? new URL("/api", window.location.origin).href
    : "/api";

export const api = createClient<paths>({
  baseUrl: API_BASE, // vite dev proxy / compose ui service proxy -> FastAPI
  fetch: (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (input instanceof Request) {
      for (const [key, value] of input.headers.entries()) headers[key] = value;
    }
    return fetch(input, {
      ...init,
      headers: { ...headers, ...(init?.headers as object | undefined), ...identityHeaders },
    });
  },
});
