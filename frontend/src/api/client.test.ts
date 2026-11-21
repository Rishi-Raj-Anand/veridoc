import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../test/helpers";
import { ApiError, request, setAuthTokenProvider, setUnauthorizedHandler } from "./client";

afterEach(() => {
  setAuthTokenProvider(() => null);
  setUnauthorizedHandler(() => undefined);
});

describe("request", () => {
  it("signs out on a 401 when a token was sent", async () => {
    const handler = vi.fn();
    setAuthTokenProvider(() => "stale-token");
    setUnauthorizedHandler(handler);
    mockApi({ "GET /api/auth/me": { status: 401, body: { detail: "token expired" } } });

    await expect(request("/api/auth/me")).rejects.toMatchObject({
      status: 401,
      message: "token expired",
    });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("treats a 401 without a token as an ordinary error", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockApi({
      "POST /api/auth/login": { status: 401, body: { detail: "incorrect email or password" } },
    });

    await expect(request("/api/auth/login", { method: "POST", body: {} })).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(handler).not.toHaveBeenCalled();
  });

  it("joins a list-form 422 detail into readable text", async () => {
    mockApi({
      "POST /api/auth/register": {
        status: 422,
        body: {
          detail: [{ msg: "String should have at least 8 characters" }, { msg: "bad email" }],
        },
      },
    });

    await expect(request("/api/auth/register", { method: "POST", body: {} })).rejects.toMatchObject(
      {
        message: "String should have at least 8 characters; bad email",
      },
    );
  });

  it("sends the bearer token and JSON content type", async () => {
    setAuthTokenProvider(() => "abc");
    const calls = mockApi({ "POST /api/chat/message": { body: {} } });

    await request("/api/chat/message", { method: "POST", body: { a: 1 } });

    const headers = calls[0]?.init?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer abc");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("leaves Content-Type to the browser for a FormData upload", async () => {
    const calls = mockApi({ "POST /api/admin/documents": { status: 201, body: {} } });
    const form = new FormData();
    form.append("file", new File(["x"], "a.txt"));

    await request("/api/admin/documents", { method: "POST", body: form });

    const init = calls[0]?.init;
    expect((init?.headers as Headers).has("Content-Type")).toBe(false);
    expect(init?.body).toBe(form);
  });
});
