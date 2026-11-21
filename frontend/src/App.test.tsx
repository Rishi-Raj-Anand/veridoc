import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import App from "./App";

function renderApp(path: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new Error("network disabled in tests"))),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App shell", () => {
  it("renders the product heading", () => {
    renderApp("/login");
    expect(screen.getByRole("heading", { level: 1, name: "VeriDoc AI" })).toBeDefined();
  });

  it("routes to the placeholder pages", () => {
    renderApp("/admin");
    expect(screen.getByRole("heading", { level: 2, name: "Admin" })).toBeDefined();
  });

  it("shows a not-found message for unknown routes", () => {
    renderApp("/nope");
    expect(screen.getByText("Page not found.")).toBeDefined();
  });
});
