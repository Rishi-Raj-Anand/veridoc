import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { clearAuth } from "../context/authStorage";

// jsdom does not implement scrolling; the chat page scrolls to the newest message.
Element.prototype.scrollIntoView = () => undefined;

afterEach(() => {
  cleanup();
  clearAuth();
  vi.unstubAllGlobals();
});
