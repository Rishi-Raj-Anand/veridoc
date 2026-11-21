import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HEALTH_OK, mockApi, renderApp, user } from "./test/helpers";

describe("routing", () => {
  it("redirects / to the landing page, whose call to action leads to /chat", () => {
    mockApi({});
    renderApp("/");

    expect(screen.getByRole("heading", { level: 1, name: "VERIDOC AI" })).toBeDefined();
    const cta = screen.getAllByRole("link", { name: "Try out AI Doctor" })[0];
    expect(cta?.getAttribute("href")).toBe("/chat");
  });

  it("sends a signed-out visitor from /chat to the login page", () => {
    mockApi({});
    renderApp("/chat");

    expect(screen.getByRole("heading", { level: 1, name: "Sign in" })).toBeDefined();
  });

  it("keeps a signed-in user off the login page", () => {
    mockApi({ "GET /api/auth/me": { body: user() } });
    renderApp("/login", "user");

    expect(screen.getByRole("heading", { level: 1, name: "Ask Doctor AI" })).toBeDefined();
  });

  it("sends a plain user from /admin to /chat", () => {
    mockApi({ "GET /api/auth/me": { body: user() } });
    renderApp("/admin", "user");

    expect(screen.getByRole("heading", { level: 1, name: "Ask Doctor AI" })).toBeDefined();
    expect(screen.queryByRole("link", { name: "Admin" })).toBeNull();
  });

  it("lets the admin open /admin", async () => {
    mockApi({
      "GET /api/auth/me": { body: { ...user("admin@veridoc.local"), role: "admin" } },
      "GET /api/health": { body: HEALTH_OK },
      "GET /api/admin/documents": { body: [] },
    });
    renderApp("/admin", "admin");

    expect(screen.getByRole("heading", { level: 1, name: "Knowledge base" })).toBeDefined();
    expect(await screen.findByText(/No documents yet/)).toBeDefined();
    expect(screen.getByRole("link", { name: "Admin" })).toBeDefined();
  });

  it("shows the funny 404 page for an unknown path", () => {
    mockApi({});
    renderApp("/definitely/not/here");

    expect(screen.getByText("Diagnosis: Acute Page-Not-Found-itis")).toBeDefined();
    expect(screen.getByText("/definitely/not/here")).toBeDefined();
    expect(screen.getByRole("link", { name: "Take me home" }).getAttribute("href")).toBe("/home");
  });
});
