/** The app's TanStack Query client and its retry rule (Phase 8.1). */

import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";

/**
 * Retry a failed query once, and never on a 4xx: a 401, 403 or 404 will not change on a
 * second try, and three silent retries only delay the error the user needs to see.
 */
export function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return false;
  }
  return failureCount < 1;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: shouldRetry } } });
}
