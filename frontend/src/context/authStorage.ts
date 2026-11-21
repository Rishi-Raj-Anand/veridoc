/**
 * Where the signed-in token and role live (CLAUDE.md §10: `localStorage` for v1).
 *
 * `localStorage` can be missing or throw (private windows, blocked site data), so every
 * access is wrapped and the value is also held in memory: the app keeps working for the
 * current tab either way.
 */

import type { Role } from "../api/auth";

export interface StoredAuth {
  token: string;
  role: Role;
}

const STORAGE_KEY = "veridoc.auth";

function isStoredAuth(value: unknown): value is StoredAuth {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.token === "string" &&
    candidate.token.length > 0 &&
    (candidate.role === "user" || candidate.role === "admin")
  );
}

function readFromStorage(): StoredAuth | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    return isStoredAuth(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

let current: StoredAuth | null = readFromStorage();

export function getStoredAuth(): StoredAuth | null {
  return current;
}

export function saveAuth(auth: StoredAuth): void {
  current = auth;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(auth));
  } catch {
    // Storage unavailable: the in-memory copy still works for this tab.
  }
}

export function clearAuth(): void {
  current = null;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to clear.
  }
}
