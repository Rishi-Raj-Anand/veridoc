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

/**
 * The sentence to show for any failed call. `request` already turns network failures and
 * timeouts into an `ApiError`, so the fallback only covers truly unexpected errors.
 */
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : NETWORK_ERROR;
}

type TokenProvider = () => string | null;

let getAuthToken: TokenProvider = () => null;
let onUnauthorized: () => void = () => undefined;

/**
 * Register where the bearer token comes from. Wired to AuthContext so this module
 * stays free of storage details.
 */
export function setAuthTokenProvider(provider: TokenProvider): void {
  getAuthToken = provider;
}

/**
 * Register what happens when the backend rejects a request that carried a token
 * (expired or invalid JWT). A 401 on a request without a token, such as a wrong
 * password at login, is just an error to show and does not trigger this.
 */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

/** Status of an `ApiError` that never got an HTTP answer (backend down, or timed out). */
export const NO_RESPONSE = 0;

export const NETWORK_ERROR =
  "Could not reach the VeriDoc server. Check that it is running and try again.";
export const TIMEOUT_ERROR = "The VeriDoc server took too long to answer. Please try again.";
export const DEFAULT_TIMEOUT_MS = 30_000;

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  /** A JSON-serialisable value, or `FormData` for a multipart upload. */
  body?: unknown;
  signal?: AbortSignal;
  /** Give up after this long (default 30 s); long calls such as chat pass their own. */
  timeoutMs?: number;
  /** What to tell the user when `timeoutMs` runs out. */
  timeoutMessage?: string;
}

/**
 * FastAPI sends `detail` as a string for HTTP errors and as a list of
 * `{msg, ...}` objects for 422 validation errors; show both as readable text.
 */
async function errorDetail(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json();
    if (typeof data === "object" && data !== null && "detail" in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === "string") {
        return detail;
      }
      if (Array.isArray(detail)) {
        const messages = detail
          .map((item: unknown) =>
            typeof item === "object" && item !== null && "msg" in item
              ? String((item as { msg: unknown }).msg)
              : null,
          )
          .filter((message): message is string => message !== null);
        if (messages.length > 0) {
          return messages.join("; ");
        }
      }
    }
  } catch {
    // Body was not JSON; fall back to the status text below.
  }
  return response.statusText || `request failed with status ${response.status}`;
}

/**
 * Perform a request against the backend and parse the JSON response as `T`.
 *
 * Every failure is an `ApiError`: the backend's own `detail` for an HTTP error, or
 * `NO_RESPONSE` with one friendly sentence when the backend is unreachable or too slow,
 * so pages never show raw browser text such as "Failed to fetch".
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { signal, timeoutMs = DEFAULT_TIMEOUT_MS, timeoutMessage = TIMEOUT_ERROR } = options;
  const controller = new AbortController();
  const forwardAbort = () => controller.abort();
  signal?.addEventListener("abort", forwardAbort);
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timedOut = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new ApiError(NO_RESPONSE, timeoutMessage));
    }, timeoutMs);
  });
  try {
    return await Promise.race([send<T>(path, options, controller.signal), timedOut]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
  }
}

async function send<T>(path: string, options: RequestOptions, signal: AbortSignal): Promise<T> {
  const { method = "GET", body } = options;

  const headers = new Headers();
  const token = getAuthToken();
  if (token !== null) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    // Leave Content-Type unset: the browser adds the multipart boundary itself.
    payload = body;
  } else if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, { method, headers, signal, body: payload });
  } catch (caught) {
    // Browsers reject with a TypeError when nothing answers (backend down, CORS failure).
    if (caught instanceof TypeError) {
      throw new ApiError(NO_RESPONSE, NETWORK_ERROR);
    }
    throw caught;
  }

  if (!response.ok) {
    if (response.status === 401 && token !== null) {
      onUnauthorized();
    }
    throw new ApiError(response.status, await errorDetail(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
