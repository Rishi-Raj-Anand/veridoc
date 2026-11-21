import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ChatResponse } from "../api/chat";
import { mockApi, renderApp, user, type Reply } from "../test/helpers";

const DISCLAIMER = "Test disclaimer from the API.";

function reply(overrides: Partial<ChatResponse>): Reply {
  const body: ChatResponse = {
    answer: "Rest is suggested [1].",
    citations: [{ n: 1, filename: "guide.md", page: 3 }],
    status: "verified",
    emergency: false,
    disclaimer: DISCLAIMER,
    ...overrides,
  };
  return { body };
}

function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Message to Doctor AI"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

describe("ChatPage", () => {
  it("shows the persistent disclaimer before any message", () => {
    mockApi({ "GET /api/auth/me": { body: user() } });
    renderApp("/chat", "user");

    expect(screen.getByRole("note").textContent).toContain("not a substitute");
  });

  it("shows Thinking, then the answer with citations and the API disclaimer", async () => {
    let finish: (value: Reply) => void = () => undefined;
    const pending = new Promise<Reply>((resolve) => {
      finish = resolve;
    });
    const calls = mockApi({
      "GET /api/auth/me": { body: user() },
      "POST /api/chat/message": () => pending,
    });
    renderApp("/chat", "user");

    ask("What helps a cold?");

    expect(await screen.findByRole("status")).toBeDefined();
    expect(screen.getByText("What helps a cold?")).toBeDefined();
    finish(reply({}));

    expect(await screen.findByText("Rest is suggested [1].")).toBeDefined();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText(/guide\.md/)).toBeDefined();
    expect(screen.getByText(DISCLAIMER)).toBeDefined();

    const sent = calls.find((call) => call.key === "POST /api/chat/message");
    const body = JSON.parse(String(sent?.init?.body)) as { session_id: string; message: string };
    expect(body.message).toBe("What helps a cold?");
    expect(body.session_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("puts the emergency notice in the red banner and not in the body", async () => {
    mockApi({
      "GET /api/auth/me": { body: user() },
      "POST /api/chat/message": reply({
        emergency: true,
        answer: "NOTICE: call for help now.\n\nRest is suggested [1].",
      }),
    });
    renderApp("/chat", "user");

    ask("I have chest pain");

    const banner = await screen.findByRole("alert");
    expect(banner.textContent).toContain("call for help now");
    expect(screen.getAllByText(/call for help now/)).toHaveLength(1);
    expect(screen.getByText("Rest is suggested [1].")).toBeDefined();
  });

  it("never renders raw HTML from an answer", async () => {
    mockApi({
      "GET /api/auth/me": { body: user() },
      "POST /api/chat/message": reply({
        answer: 'Safe text <img src="x" onerror="alert(1)"> [link](https://evil.example) end',
        citations: [],
      }),
    });
    const { container } = renderApp("/chat", "user");

    ask("hello");

    expect(await screen.findByText(/Safe text/)).toBeDefined();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a[href*='evil']")).toBeNull();
  });

  it("shows the friendly error with a working Retry", async () => {
    let attempts = 0;
    mockApi({
      "GET /api/auth/me": { body: user() },
      "POST /api/chat/message": () => {
        attempts += 1;
        if (attempts === 1) {
          return { status: 503, body: { detail: "Doctor AI is unavailable right now." } };
        }
        return reply({ answer: "Back online [1]." });
      },
    });
    renderApp("/chat", "user");

    ask("hello");

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Doctor AI is unavailable right now.")).toBeDefined();
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Back online [1].")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("renders small talk as a plain reply with no Sources section", async () => {
    mockApi({
      "GET /api/auth/me": { body: user() },
      "POST /api/chat/message": reply({
        answer: "Hi! I'm Doctor AI. Ask me a health question.",
        citations: [],
        status: "smalltalk",
      }),
    });
    renderApp("/chat", "user");

    ask("hi");

    expect(await screen.findByText("Hi! I'm Doctor AI. Ask me a health question.")).toBeDefined();
    expect(screen.queryByText("Sources")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(DISCLAIMER)).toBeDefined();
  });

  it("does not send a blank message", () => {
    const calls = mockApi({ "GET /api/auth/me": { body: user() } });
    renderApp("/chat", "user");

    fireEvent.change(screen.getByLabelText("Message to Doctor AI"), { target: { value: "   " } });

    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls.some((call) => call.key.startsWith("POST"))).toBe(false);
  });
});
