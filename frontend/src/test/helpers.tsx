/** Shared test helpers: a route-matching `fetch` stub and an app renderer with every provider. */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

import type { Role } from "../api/auth";
import { apiBaseUrl } from "../api/client";
import type { HealthResponse } from "../api/health";
import App from "../App";
import { AuthProvider } from "../context/AuthContext";
import { saveAuth } from "../context/authStorage";

export interface Reply {
  status?: number;
  body?: unknown;
}

export type Handler = Reply | ((init: RequestInit | undefined) => Reply | Promise<Reply>);

export interface RecordedCall {
  key: string;
  init: RequestInit | undefined;
}

export const HEALTH_OK: HealthResponse = {
  status: "ok",
  backend: "ok",
  error: null,
  ai_service: {
    status: "ok",
    ollama: { ok: true, host: "http://127.0.0.1:11434", error: null },
    models: { llm: true, embed: true, missing: [] },
    qdrant: { ok: true, path: "data/vectors", error: null },
  },
};

/**
 * Replace `fetch` with a stub keyed by "METHOD /path". A request with no matching entry
 * rejects, so a test fails loudly instead of hitting the network.
 */
export function mockApi(handlers: Record<string, Handler>): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const path = String(input).replace(apiBaseUrl, "");
      const key = `${init?.method ?? "GET"} ${path}`;
      calls.push({ key, init });
      const handler = handlers[key];
      if (handler === undefined) {
        throw new Error(`unexpected request: ${key}`);
      }
      const reply = typeof handler === "function" ? await handler(init) : handler;
      const status = reply.status ?? 200;
      if (status === 204 || reply.body === undefined) {
        return new Response(null, { status });
      }
      return new Response(JSON.stringify(reply.body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

export function user(email = "ada@example.com") {
  return { id: 1, email, role: "user" as Role, created_at: "2026-11-21T10:00:00Z" };
}

export function renderApp(path: string, signedInAs: Role | null = null) {
  if (signedInAs !== null) {
    saveAuth({ token: "test-token", role: signedInAs });
  }
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}
