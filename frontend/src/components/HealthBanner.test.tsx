/** The admin's System status card (Phase 8.1): every failure names its cause and its fix. */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AiServiceHealth, HealthResponse } from "../api/health";
import { HEALTH_OK, mockApi } from "../test/helpers";
import { HealthBanner } from "./HealthBanner";

const AI_OK = HEALTH_OK.ai_service as AiServiceHealth;

function health(ai: Partial<AiServiceHealth> | null, error: string | null = null): HealthResponse {
  return {
    status: ai === null || error !== null ? "degraded" : "ok",
    backend: "ok",
    error,
    ai_service: ai === null ? null : { ...AI_OK, ...ai },
  };
}

function renderBanner(reply: Parameters<typeof mockApi>[0][string]) {
  mockApi({ "GET /api/health": reply });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <HealthBanner />
    </QueryClientProvider>,
  );
}

describe("HealthBanner", () => {
  it("shows every service up and no warnings", async () => {
    renderBanner({ body: health({ knowledge_base: { chunks: 120 } }) });

    expect(await screen.findByText("ai service ok")).toBeDefined();
    expect(screen.getByText("ollama up")).toBeDefined();
    expect(screen.queryByText(/No documents indexed/)).toBeNull();
    expect(screen.queryByText(/not pulled/)).toBeNull();
  });

  it("explains an AI service that cannot be reached", async () => {
    renderBanner({
      body: health(null, "AI service unreachable at http://127.0.0.1:8001/internal/health"),
    });

    expect(await screen.findByText("ai service unreachable")).toBeDefined();
    expect(screen.getByText(/AI service unreachable at http:\/\/127.0.0.1:8001/)).toBeDefined();
  });

  it("shows why Ollama is down and how to start it", async () => {
    renderBanner({
      body: health({
        status: "degraded",
        ollama: { ok: false, host: "h", error: "cannot reach Ollama at h (ConnectError)" },
        models: { llm: false, embed: false, missing: ["bge-m3:latest", "qwen3:8b"] },
      }),
    });

    expect(await screen.findByText("ollama down")).toBeDefined();
    expect(screen.getByText(/cannot reach Ollama at h/).textContent).toContain("ollama serve");
    // With Ollama down the models are unknown, so no misleading "pull" advice.
    expect(screen.queryByText(/not pulled/)).toBeNull();
  });

  it("names each missing model with the pull command", async () => {
    renderBanner({
      body: health({
        status: "degraded",
        models: { llm: false, embed: true, missing: ["qwen3:8b"] },
      }),
    });

    expect((await screen.findByText(/Model qwen3:8b is not pulled/)).textContent).toContain(
      "ollama pull qwen3:8b",
    );
  });

  it("shows a vector store error", async () => {
    renderBanner({
      body: health({
        status: "degraded",
        qdrant: { ok: false, path: "p", error: "local storage was not opened at startup" },
      }),
    });

    expect(await screen.findByText(/local storage was not opened at startup/)).toBeDefined();
  });

  it("warns when no documents are indexed", async () => {
    renderBanner({ body: health({ knowledge_base: { chunks: 0 } }) });

    expect(await screen.findByText(/No documents indexed/)).toBeDefined();
  });

  it("says the backend is unreachable only when nothing answered", async () => {
    renderBanner(() => {
      throw new TypeError("Failed to fetch");
    });

    expect((await screen.findByText(/Backend unreachable/)).textContent).toContain(
      "Could not reach the VeriDoc server",
    );
  });

  it("shows an HTTP error as it is, not as unreachable", async () => {
    renderBanner({ status: 500, body: { detail: "Something went wrong on the server." } });

    expect(await screen.findByText("Something went wrong on the server.")).toBeDefined();
    expect(screen.queryByText(/Backend unreachable/)).toBeNull();
  });
});
