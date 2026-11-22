/** Phase 8.1: what each page shows when the backend refuses, fails or cannot be reached. */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BehaviourResponse } from "../api/behaviour";
import { NETWORK_ERROR } from "../api/client";
import { HEALTH_OK, mockApi, renderApp, user } from "../test/helpers";

const ADMIN = { ...user("admin@veridoc.local"), role: "admin" as const };
// What the backend reports, e.g. MAX_UPLOAD_MB=35 in backend/.env (not the 25 MB default).
const MAX_UPLOAD_MB = 35;
const ADMIN_BASE = {
  "GET /api/auth/me": { body: ADMIN },
  "GET /api/health": { body: HEALTH_OK },
  "GET /api/admin/documents/limits": {
    body: { max_upload_mb: MAX_UPLOAD_MB, accepted_extensions: [".csv", ".pdf"] },
  },
};

const DOC = {
  id: 1,
  filename: "guide.md",
  size_bytes: 2048,
  status: "ready" as const,
  chunk_count: 6,
  error: null,
  created_at: "2026-11-21T10:00:00Z",
};

function networkDown(): never {
  throw new TypeError("Failed to fetch");
}

function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Message to Doctor AI"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

async function pickFile(file: File) {
  const input = await screen.findByLabelText("Choose a document to upload");
  fireEvent.change(input, { target: { files: [file] } });
}

describe("admin page errors", () => {
  it("refuses a file over the upload limit before sending it", async () => {
    const calls = mockApi({ ...ADMIN_BASE, "GET /api/admin/documents": { body: [] } });
    renderApp("/admin", "admin");
    await waitFor(() => expect(calls.some((c) => c.key.endsWith("/limits"))).toBe(true));
    await screen.findByText(/No documents yet/);
    const big = new File(["x"], "huge.csv");
    Object.defineProperty(big, "size", { value: MAX_UPLOAD_MB * 1024 * 1024 + 1 });

    await pickFile(big);

    expect((await screen.findByRole("alert")).textContent).toContain(
      `larger than the ${MAX_UPLOAD_MB} MB upload limit`,
    );
    expect(calls.some((call) => call.key.startsWith("POST"))).toBe(false);
  });

  it("sends a file under the server's limit even if it is over the old 25 MB default", async () => {
    const calls = mockApi({
      ...ADMIN_BASE,
      "GET /api/admin/documents": { body: [] },
      "POST /api/admin/documents": { status: 201, body: { ...DOC, status: "pending" } },
    });
    renderApp("/admin", "admin");
    await waitFor(() => expect(calls.some((c) => c.key.endsWith("/limits"))).toBe(true));
    await screen.findByText(/No documents yet/);
    const file = new File(["x"], "medquad.csv");
    Object.defineProperty(file, "size", { value: 30 * 1024 * 1024 });

    await pickFile(file);

    await waitFor(() =>
      expect(calls.some((c) => c.key === "POST /api/admin/documents")).toBe(true),
    );
  });

  it.each([
    [413, "file is larger than the 25 MB upload limit"],
    [415, "file is not a valid PDF"],
  ])("shows the server's %i rejection of an upload", async (status, detail) => {
    mockApi({
      ...ADMIN_BASE,
      "GET /api/admin/documents": { body: [] },
      "POST /api/admin/documents": { status, body: { detail } },
    });
    renderApp("/admin", "admin");

    await pickFile(new File(["%PDF"], "ref.pdf"));

    expect((await screen.findByRole("alert")).textContent).toBe(detail);
  });

  it("shows why a delete failed", async () => {
    const detail =
      "The AI service could not remove this document from the search index, so nothing was deleted.";
    mockApi({
      ...ADMIN_BASE,
      "GET /api/admin/documents": { body: [DOC] },
      "DELETE /api/admin/documents/1": { status: 503, body: { detail } },
    });
    renderApp("/admin", "admin");

    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect((await screen.findByRole("alert")).textContent).toBe(detail);
  });

  it("shows a list that cannot be loaded", async () => {
    mockApi({ ...ADMIN_BASE, "GET /api/admin/documents": networkDown });
    renderApp("/admin", "admin");

    expect((await screen.findByRole("alert")).textContent).toBe(NETWORK_ERROR);
  });

  it("shows a behaviour save that failed", async () => {
    const settings = {
      persona: "Warm.",
      scope: "Health.",
      out_of_scope_message: "Sorry.",
      extra_guardrails: "",
    };
    const loaded: BehaviourResponse = {
      settings,
      defaults: settings,
      locked_rules: ["Grounded only."],
      updated_at: null,
    };
    const detail = "The AI service is unavailable, so nothing was saved.";
    mockApi({
      "GET /api/auth/me": { body: ADMIN },
      "GET /api/admin/behaviour": { body: loaded },
      "PUT /api/admin/behaviour": { status: 503, body: { detail } },
    });
    renderApp("/admin/behaviour", "admin");

    fireEvent.change(await screen.findByLabelText("Extra guardrails"), {
      target: { value: "Be brief." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect((await screen.findByRole("alert")).textContent).toContain(detail);
  });
});

describe("chat errors", () => {
  it("shows one plain sentence when the backend cannot be reached", async () => {
    mockApi({ "GET /api/auth/me": { body: user() }, "POST /api/chat/message": networkDown });
    renderApp("/chat", "user");

    ask("What helps a migraine?");

    expect((await screen.findByRole("alert")).textContent).toContain(NETWORK_ERROR);
  });

  it("shows the backend's cause-specific message", async () => {
    const detail = "Doctor AI's language model is offline right now. Please try again later.";
    mockApi({
      "GET /api/auth/me": { body: user() },
      "POST /api/chat/message": { status: 503, body: { detail } },
    });
    renderApp("/chat", "user");

    ask("What helps a migraine?");

    expect((await screen.findByRole("alert")).textContent).toContain(detail);
  });

  it("signs out and goes to login when the session expires mid-chat", async () => {
    mockApi({
      "GET /api/auth/me": { body: user() },
      "POST /api/chat/message": { status: 401, body: { detail: "token expired" } },
    });
    renderApp("/chat", "user");

    ask("What helps a migraine?");

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeDefined();
  });
});

describe("auth errors", () => {
  it("shows a duplicate registration", async () => {
    mockApi({
      "POST /api/auth/register": {
        status: 409,
        body: { detail: "an account with this email already exists" },
      },
    });
    renderApp("/register");

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "long-enough-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "an account with this email already exists",
    );
  });

  it("shows the network message on login when the backend is down", async () => {
    mockApi({ "POST /api/auth/login": networkDown });
    renderApp("/login");

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "whatever-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect((await screen.findByRole("alert")).textContent).toBe(NETWORK_ERROR);
  });
});
