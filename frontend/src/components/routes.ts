import type { Role } from "../api/auth";

/** Where a signed-in user lands by default (CLAUDE.md §10). */
export function homeFor(role: Role | null): string {
  return role === "admin" ? "/admin" : "/chat";
}

/** Router state carried to /login so a guarded visit can return afterwards. */
export interface FromState {
  from?: string;
}
