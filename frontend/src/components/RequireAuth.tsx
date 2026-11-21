/** Route guard: signed-out visitors go to /login, wrong-role users go to their own page. */

import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import type { Role } from "../api/auth";
import { useAuth } from "../hooks/useAuth";
import { homeFor, type FromState } from "./routes";

export function RequireAuth({ role, children }: { role?: Role; children: ReactNode }) {
  const { isAuthenticated, role: currentRole } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    const state: FromState = { from: location.pathname };
    return <Navigate to="/login" replace state={state} />;
  }
  if (role !== undefined && currentRole !== role) {
    return <Navigate to={homeFor(currentRole)} replace />;
  }
  return <>{children}</>;
}
