/** Keeps signed-in users off /login and /register. */

import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { useAuth } from "../hooks/useAuth";
import { homeFor, type FromState } from "./routes";

export function RedirectIfAuthed({ children }: { children: ReactNode }) {
  const { isAuthenticated, role } = useAuth();
  const location = useLocation();

  if (isAuthenticated) {
    const from = (location.state as FromState | null)?.from;
    return <Navigate to={from ?? homeFor(role)} replace />;
  }
  return <>{children}</>;
}
