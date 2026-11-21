import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DocumentItem } from "../api/admin";
import { HEALTH_OK, mockApi, renderApp, user } from "../test/helpers";

const ADMIN = { ...user("admin@veridoc.local"), role: "admin" as const };

function doc(overrides: Partial<DocumentItem>): DocumentItem {
  return {
    id: 1,
    filename: "guide.md",
    size_bytes: 2048,
    status: "ready",
    chunk_count: 6,
    error: null,
    created_at: "2026-11-21T10:00:00Z",
    ...overrides,
  };
}

const BASE = {
  "GET /api/auth/me": { body: ADMIN },
  "GET /api/health": { body: HEALTH_OK },
};

describe("AdminPage", () => {
  it("lists documents with their status and the error of a failed one", async () => {
    mockApi({
      ...BASE,
      "GET /api/admin/documents": {
        body: [
          doc({ id: 1, filename: "guide.md" }),
          doc({
            id: 2,
            filename: "scan.pdf",
            status: "failed",
            chunk_count: 0,
            error: "scanned or image-only PDF is not supported",
          }),
          doc({ id: 3, filename: "new.docx", status: "pending", chunk_count: 0 }),
        ],
      },
    });
    renderApp("/admin", "admin");

    expect(await screen.findByText("guide.md")).toBeDefined();
    expect(screen.getByText("ready")).toBeDefined();
    expect(screen.getByText("failed")).toBeDefined();
    expect(screen.getByText("pending")).toBeDefined();
    expect(screen.getByText("scanned or image-only PDF is not supported")).toBeDefined();
  });

  it("disables delete while a document is pending", async () => {
    mockApi({
      ...BASE,
      "GET /api/admin/documents": { body: [doc({ status: "pending", chunk_count: 0 })] },
    });
    renderApp("/admin", "admin");

    const button = (await screen.findByRole("button", { name: "Delete" })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("asks for confirmation before deleting", async () => {
    const calls = mockApi({
      ...BASE,
      "GET /api/admin/documents": { body: [doc({})] },
      "DELETE /api/admin/documents/1": { status: 204 },
    });
    renderApp("/admin", "admin");

    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(calls.some((call) => call.key.startsWith("DELETE"))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByRole("button", { name: "Delete" });
    expect(calls.some((call) => call.key === "DELETE /api/admin/documents/1")).toBe(true);
  });

  it("cancelling a delete sends nothing", async () => {
    const calls = mockApi({ ...BASE, "GET /api/admin/documents": { body: [doc({})] } });
    renderApp("/admin", "admin");

    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Delete" })).toBeDefined();
    expect(calls.some((call) => call.key.startsWith("DELETE"))).toBe(false);
  });

  it("uploads a file as multipart and shows the server's rejection", async () => {
    const calls = mockApi({
      ...BASE,
      "GET /api/admin/documents": { body: [] },
      "POST /api/admin/documents": {
        status: 409,
        body: { detail: "this document has already been uploaded" },
      },
    });
    renderApp("/admin", "admin");

    const input = await screen.findByLabelText("Choose a document to upload");
    fireEvent.change(input, { target: { files: [new File(["hello"], "notes.txt")] } });

    expect((await screen.findByRole("alert")).textContent).toBe(
      "this document has already been uploaded",
    );
    const upload = calls.find((call) => call.key === "POST /api/admin/documents");
    expect(upload?.init?.body).toBeInstanceOf(FormData);
  });

  it("rejects an unsupported file type before uploading", async () => {
    const calls = mockApi({ ...BASE, "GET /api/admin/documents": { body: [] } });
    renderApp("/admin", "admin");

    const input = await screen.findByLabelText("Choose a document to upload");
    fireEvent.change(input, { target: { files: [new File(["x"], "photo.png")] } });

    expect((await screen.findByRole("alert")).textContent).toContain("Unsupported file type");
    expect(calls.some((call) => call.key.startsWith("POST"))).toBe(false);
  });
});
