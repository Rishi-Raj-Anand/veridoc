import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HEALTH_OK, mockApi, renderApp, user } from "../test/helpers";

function fillAndSubmit(email: string, password: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginPage", () => {
  it("shows the server's message for wrong credentials", async () => {
    mockApi({
      "POST /api/auth/login": { status: 401, body: { detail: "incorrect email or password" } },
    });
    renderApp("/login");

    fillAndSubmit("ada@example.com", "wrong-password");

    expect((await screen.findByRole("alert")).textContent).toBe("incorrect email or password");
  });

  it("signs a user in and lands on the chat", async () => {
    mockApi({
      "POST /api/auth/login": { body: { access_token: "t", token_type: "bearer", role: "user" } },
      "GET /api/auth/me": { body: user() },
    });
    renderApp("/login");

    fillAndSubmit("ada@example.com", "correct-horse");

    expect(await screen.findByRole("heading", { name: "Ask Doctor AI" })).toBeDefined();
    expect(await screen.findByText("ada@example.com")).toBeDefined();
  });

  it("signs the admin in and lands on /admin", async () => {
    mockApi({
      "POST /api/auth/login": { body: { access_token: "t", token_type: "bearer", role: "admin" } },
      "GET /api/auth/me": { body: { ...user("admin@veridoc.local"), role: "admin" } },
      "GET /api/health": { body: HEALTH_OK },
      "GET /api/admin/documents": { body: [] },
    });
    renderApp("/login");

    fillAndSubmit("admin@veridoc.local", "change-me-please");

    expect(await screen.findByRole("heading", { name: "Knowledge base" })).toBeDefined();
  });

  it("registers, then signs in with the same credentials", async () => {
    const calls = mockApi({
      "POST /api/auth/register": { status: 201, body: user() },
      "POST /api/auth/login": { body: { access_token: "t", token_type: "bearer", role: "user" } },
      "GET /api/auth/me": { body: user() },
    });
    renderApp("/register");

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "long-enough-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "Ask Doctor AI" })).toBeDefined();
    expect(calls.map((call) => call.key).slice(0, 2)).toEqual([
      "POST /api/auth/register",
      "POST /api/auth/login",
    ]);
  });

  it("rejects a short password without calling the server", () => {
    const calls = mockApi({});
    renderApp("/register");

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(screen.getByRole("alert").textContent).toContain("at least 8");
    expect(calls).toHaveLength(0);
  });
});
