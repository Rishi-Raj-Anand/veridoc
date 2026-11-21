import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BehaviourResponse, BehaviourSettings } from "../api/behaviour";
import { BEHAVIOUR_LIMITS } from "../constants";
import { mockApi, renderApp, user, type Reply } from "../test/helpers";

const ADMIN = { ...user("admin@veridoc.local"), role: "admin" as const };

const DEFAULTS: BehaviourSettings = {
  persona: "Warm and clear.",
  scope: "Medical and health questions.",
  out_of_scope_message: "Sorry, I can't help with that.",
  extra_guardrails: "",
};

function behaviour(overrides: Partial<BehaviourResponse> = {}): BehaviourResponse {
  return {
    settings: DEFAULTS,
    defaults: DEFAULTS,
    locked_rules: ["Medical answers come only from the uploaded documents."],
    updated_at: null,
    ...overrides,
  };
}

function textbox(name: string): HTMLTextAreaElement {
  return screen.getByLabelText(name) as HTMLTextAreaElement;
}

function button(name: string): HTMLButtonElement {
  return screen.getByRole("button", { name }) as HTMLButtonElement;
}

describe("AdminBehaviourPage", () => {
  it("shows the current settings and the locked rules", async () => {
    mockApi({
      "GET /api/auth/me": { body: ADMIN },
      "GET /api/admin/behaviour": { body: behaviour() },
    });
    renderApp("/admin/behaviour", "admin");

    expect(((await screen.findByLabelText("Tone and style")) as HTMLTextAreaElement).value).toBe(
      "Warm and clear.",
    );
    expect(
      screen.getByText("Medical answers come only from the uploaded documents."),
    ).toBeDefined();
    expect(button("Save").disabled).toBe(true); // nothing changed yet
  });

  it("saves the edited settings and confirms", async () => {
    const calls = mockApi({
      "GET /api/auth/me": { body: ADMIN },
      "GET /api/admin/behaviour": { body: behaviour() },
      "PUT /api/admin/behaviour": (init): Reply => {
        const sent = JSON.parse(String(init?.body)) as BehaviourSettings;
        return { body: behaviour({ settings: sent, updated_at: "2026-11-21T10:00:00Z" }) };
      },
    });
    renderApp("/admin/behaviour", "admin");

    await screen.findByLabelText("Extra guardrails");
    fireEvent.change(textbox("Extra guardrails"), { target: { value: "Never use slang." } });
    fireEvent.click(button("Save"));

    expect((await screen.findByRole("status")).textContent).toContain("from the next message");
    const put = calls.find((call) => call.key === "PUT /api/admin/behaviour");
    expect(JSON.parse(String(put?.init?.body))).toEqual({
      ...DEFAULTS,
      extra_guardrails: "Never use slang.",
    });
    expect(textbox("Extra guardrails").value).toBe("Never use slang.");
  });

  it("restores the defaults into the form without saving", async () => {
    const calls = mockApi({
      "GET /api/auth/me": { body: ADMIN },
      "GET /api/admin/behaviour": {
        body: behaviour({ settings: { ...DEFAULTS, persona: "Pirate voice." } }),
      },
    });
    renderApp("/admin/behaviour", "admin");

    await screen.findByLabelText("Tone and style");
    fireEvent.click(button("Restore defaults"));

    expect(textbox("Tone and style").value).toBe("Warm and clear.");
    expect(button("Save").disabled).toBe(false);
    expect(calls.some((call) => call.key.startsWith("PUT"))).toBe(false);
  });

  it("blocks saving text over the limit or an empty required field", async () => {
    mockApi({
      "GET /api/auth/me": { body: ADMIN },
      "GET /api/admin/behaviour": { body: behaviour() },
    });
    renderApp("/admin/behaviour", "admin");

    await screen.findByLabelText("Extra guardrails");
    fireEvent.change(textbox("Extra guardrails"), {
      target: { value: "x".repeat(BEHAVIOUR_LIMITS.extra_guardrails + 1) },
    });
    expect(button("Save").disabled).toBe(true);
    expect(screen.getByText(/1 characters too long/)).toBeDefined();

    fireEvent.change(textbox("Extra guardrails"), { target: { value: "" } });
    fireEvent.change(textbox("What counts as in scope"), { target: { value: "   " } });
    expect(button("Save").disabled).toBe(true);
    expect(screen.getByText(/cannot be empty/)).toBeDefined();
  });

  it("shows the friendly error when the AI service is down", async () => {
    mockApi({
      "GET /api/auth/me": { body: ADMIN },
      "GET /api/admin/behaviour": {
        status: 503,
        body: {
          detail: "The AI service is unavailable, so the assistant behaviour could not be loaded.",
        },
      },
    });
    renderApp("/admin/behaviour", "admin");

    expect((await screen.findByRole("alert")).textContent).toContain("could not be loaded");
  });

  it("keeps a plain user out", () => {
    mockApi({ "GET /api/auth/me": { body: user() } });
    renderApp("/admin/behaviour", "user");

    expect(screen.getByRole("heading", { level: 1, name: "Ask Doctor AI" })).toBeDefined();
  });
});
