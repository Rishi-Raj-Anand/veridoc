/** Shared chrome for /login, /register, /chat and /admin. */

import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Outlet } from "react-router";

import { getMe } from "../api/auth";
import { useAuth } from "../hooks/useAuth";
import { Logo } from "./Logo";

const navClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium ${
    isActive ? "bg-teal-50 text-teal-700" : "text-slate-600 hover:bg-slate-100"
  }`;

export function AppLayout() {
  const { isAuthenticated, role, logout } = useAuth();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, enabled: isAuthenticated, retry: false });

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <Link to="/home" aria-label="VeriDoc AI home">
            <Logo className="text-lg" />
          </Link>
          <nav className="flex flex-wrap items-center gap-1">
            {isAuthenticated ? (
              <>
                <NavLink to="/chat" className={navClass}>
                  Chat
                </NavLink>
                {role === "admin" && (
                  <NavLink to="/admin" className={navClass}>
                    Admin
                  </NavLink>
                )}
                {me.data !== undefined && (
                  <span className="hidden px-2 text-sm text-slate-500 sm:inline">
                    {me.data.email}
                  </span>
                )}
                <button
                  type="button"
                  onClick={logout}
                  className="rounded-md px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
                >
                  Log out
                </button>
              </>
            ) : (
              <>
                <NavLink to="/login" className={navClass}>
                  Sign in
                </NavLink>
                <NavLink to="/register" className={navClass}>
                  Create account
                </NavLink>
              </>
            )}
          </nav>
        </div>
      </header>
      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
