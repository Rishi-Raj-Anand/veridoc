/**
 * Typed HTTP client for the backend (CLAUDE.md §10).
 *
 * Every network call in the app goes through `src/api/`; components never call
 * `fetch` directly. The frontend talks only to the backend, never to the AI
 * service or Ollama (§4).
 */

export const apiBaseUrl = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** A failed backend call, carrying the HTTP status and the `detail` message (§9). */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type TokenProvider = () => string | null;

let getAuthToken: TokenProvider = () => null;

/**
 * Register where the bearer token comes from. Wired to AuthContext in task 6.1 so
 * this module stays free of storage details.
 */
export function setAuthTokenProvider(provider: TokenProvider): void {
  getAuthToken = provider;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json();
    if (typeof data === "object" && data !== null && "detail" in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === "string") {
        return detail;
      }
    }
  } catch {
    // Body was not JSON; fall back to the status text below.
  }
  return response.statusText || `request failed with status ${response.status}`;
}

/** Perform a JSON request against the backend and parse the response as `T`. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal } = options;

  const headers = new Headers();
  const token = getAuthToken();
  if (token !== null) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${apiBaseUrl}${path}`, {
    method,
    headers,
    signal,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiError(response.status, await errorDetail(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
