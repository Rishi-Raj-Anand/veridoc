/** Phase 8.1: every failed call becomes an `ApiError` with one friendly sentence. */

import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../test/helpers";
import { CHAT_TIMEOUT_ERROR, CHAT_TIMEOUT_MS } from "./chat";
import {
  ApiError,
  DEFAULT_TIMEOUT_MS,
  NETWORK_ERROR,
  NO_RESPONSE,
  TIMEOUT_ERROR,
  errorMessage,
  request,
} from "./client";
import { shouldRetry } from "./queryClient";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("request failures", () => {
  it("turns an unreachable backend into one friendly message", async () => {
    mockApi({
      "GET /api/health": () => {
        throw new TypeError("Failed to fetch");
      },
    });

    await expect(request("/api/health")).rejects.toMatchObject({
      status: NO_RESPONSE,
      message: NETWORK_ERROR,
    });
  });

  it("does not hide errors that are not network failures", async () => {
    mockApi({}); // the stub rejects an unexpected route with a plain Error

    await expect(request("/api/nowhere")).rejects.not.toBeInstanceOf(ApiError);
  });

  it("gives up after the timeout and aborts the request", async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: string, init?: RequestInit) => {
        signal = init?.signal ?? undefined;
        return new Promise<Response>(() => undefined); // never answers
      }),
    );

    const pending = request("/api/health");
    const outcome = expect(pending).rejects.toMatchObject({
      status: NO_RESPONSE,
      message: TIMEOUT_ERROR,
    });
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS);

    await outcome;
    expect(signal?.aborted).toBe(true);
  });

  it("uses the caller's timeout and message", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => undefined)),
    );

    const pending = request("/api/chat/message", {
      method: "POST",
      body: {},
      timeoutMs: CHAT_TIMEOUT_MS,
      timeoutMessage: CHAT_TIMEOUT_ERROR,
    });
    const outcome = expect(pending).rejects.toMatchObject({ message: CHAT_TIMEOUT_ERROR });
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS); // the default would have fired here
    await vi.advanceTimersByTimeAsync(CHAT_TIMEOUT_MS - DEFAULT_TIMEOUT_MS);

    await outcome;
  });

  it("waits longer for chat than the backend waits for the AI service", () => {
    expect(CHAT_TIMEOUT_MS).toBeGreaterThan(300_000);
  });

  it("falls back to the status text when the error body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" }),
      ),
    );

    await expect(request("/api/health")).rejects.toMatchObject({
      status: 502,
      message: "Bad Gateway",
    });
  });
});

describe("errorMessage", () => {
  it("shows an ApiError's text and covers anything else", () => {
    expect(errorMessage(new ApiError(409, "already uploaded"))).toBe("already uploaded");
    expect(errorMessage(new Error("boom"))).toBe(NETWORK_ERROR);
  });
});

describe("shouldRetry", () => {
  it("never retries a 4xx and retries anything else once", () => {
    expect(shouldRetry(0, new ApiError(401, "token expired"))).toBe(false);
    expect(shouldRetry(0, new ApiError(404, "not found"))).toBe(false);
    expect(shouldRetry(0, new ApiError(503, "down"))).toBe(true);
    expect(shouldRetry(0, new ApiError(NO_RESPONSE, NETWORK_ERROR))).toBe(true);
    expect(shouldRetry(1, new ApiError(503, "down"))).toBe(false);
  });
});
