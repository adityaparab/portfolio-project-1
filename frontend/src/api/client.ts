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

// API base: "/api" by default (the Vite dev proxy / compose ui service
// proxy -> FastAPI). The Docker UI build sets VITE_API_BASE_URL="" so the
// built app calls /v1/* same-origin on :8000 — the api serves the SPA.
const RAW_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";
export const apiBaseUrl =
  RAW_BASE === ""
    ? ""
    : typeof window !== "undefined" && window.location
      ? new URL(RAW_BASE, window.location.origin).href
      : RAW_BASE;

export const api = createClient<paths>({
  baseUrl: apiBaseUrl,
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
