/** Auth state for the whole app: who is signed in, and how to sign in and out. */

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { login as loginRequest, type Role } from "../api/auth";
import { setAuthTokenProvider, setUnauthorizedHandler } from "../api/client";
import { clearAuth, getStoredAuth, saveAuth, type StoredAuth } from "./authStorage";

export interface AuthContextValue {
  token: string | null;
  role: Role | null;
  isAuthenticated: boolean;
  /** Sign in and resolve to the role, so callers can route by it. */
  login: (email: string, password: string) => Promise<Role>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

// Registered once at import so a child query can never fire before the token is wired.
setAuthTokenProvider(() => getStoredAuth()?.token ?? null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [auth, setAuth] = useState<StoredAuth | null>(() => getStoredAuth());

  const logout = useCallback(() => {
    clearAuth();
    setAuth(null);
    // Drop cached server state so the next user never sees the previous one's data.
    queryClient.clear();
  }, [queryClient]);

  const login = useCallback(async (email: string, password: string) => {
    const response = await loginRequest({ email, password });
    const next: StoredAuth = { token: response.access_token, role: response.role };
    saveAuth(next);
    setAuth(next);
    return response.role;
  }, []);

  // An expired or invalid token on a signed-in request signs the user out; the route
  // guards then send them to /login.
  useEffect(() => {
    setUnauthorizedHandler(logout);
    return () => setUnauthorizedHandler(() => undefined);
  }, [logout]);

  const value = useMemo<AuthContextValue>(
    () => ({
      token: auth?.token ?? null,
      role: auth?.role ?? null,
      isAuthenticated: auth !== null,
      login,
      logout,
    }),
    [auth, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
