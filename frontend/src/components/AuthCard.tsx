/** Centred card shared by the login and register forms. */

import type { FormEvent, ReactNode } from "react";

interface AuthCardProps {
  title: string;
  subtitle: string;
  submitLabel: string;
  pending: boolean;
  error: string | null;
  email: string;
  password: string;
  passwordHint?: string;
  onEmailChange: (value: string) => void;
  onPasswordChange: (value: string) => void;
  onSubmit: () => void;
  footer: ReactNode;
  autoCompletePassword: "current-password" | "new-password";
}

const inputClass =
  "mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-teal-600 focus:outline-none focus:ring-2 focus:ring-teal-600/30";

export function AuthCard(props: AuthCardProps) {
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    props.onSubmit();
  }

  return (
    <section className="mx-auto mt-6 w-full max-w-sm rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h1 className="text-xl font-semibold">{props.title}</h1>
      <p className="mt-1 text-sm text-slate-500">{props.subtitle}</p>

      <form onSubmit={handleSubmit} className="mt-5 space-y-4">
        <div>
          <label htmlFor="email" className="text-sm font-medium text-slate-700">
            Email
          </label>
          <input
            id="email"
            type="text"
            inputMode="email"
            autoComplete="email"
            required
            value={props.email}
            onChange={(event) => props.onEmailChange(event.target.value)}
            className={inputClass}
          />
        </div>
        <div>
          <label htmlFor="password" className="text-sm font-medium text-slate-700">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete={props.autoCompletePassword}
            required
            value={props.password}
            onChange={(event) => props.onPasswordChange(event.target.value)}
            className={inputClass}
          />
          {props.passwordHint !== undefined && (
            <p className="mt-1 text-xs text-slate-500">{props.passwordHint}</p>
          )}
        </div>

        {props.error !== null && (
          <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {props.error}
          </p>
        )}

        <button
          type="submit"
          disabled={props.pending}
          className="w-full rounded-md bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {props.pending ? "Please wait…" : props.submitLabel}
        </button>
      </form>

      <p className="mt-4 text-center text-sm text-slate-500">{props.footer}</p>
    </section>
  );
}
