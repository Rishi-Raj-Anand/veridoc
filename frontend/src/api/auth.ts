/** Auth endpoint types and calls (CLAUDE.md §9). */

import { request } from "./client";

export type Role = "user" | "admin";

export interface Credentials {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  role: Role;
}

export interface User {
  id: number;
  email: string;
  role: Role;
  created_at: string;
}

export function register(credentials: Credentials): Promise<User> {
  return request<User>("/api/auth/register", { method: "POST", body: credentials });
}

export function login(credentials: Credentials): Promise<TokenResponse> {
  return request<TokenResponse>("/api/auth/login", { method: "POST", body: credentials });
}

export function getMe(): Promise<User> {
  return request<User>("/api/auth/me");
}
