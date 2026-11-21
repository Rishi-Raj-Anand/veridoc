/** Public landing page at /home (CLAUDE.md §10). Standalone layout, no app header. */

import { Link } from "react-router";

import { Logo } from "../components/Logo";
import { homeFor } from "../components/routes";
import { DISCLAIMER } from "../constants";
import { useAuth } from "../hooks/useAuth";

const STEPS = [
  {
    title: "Retrieve",
    text: "Finds the most relevant passages in the medical references the admin uploaded.",
  },
  {
    title: "Generate",
    text: "The Generator drafts an answer using only those passages and cites every claim.",
  },
  {
    title: "Verify",
    text: "The Verifier audits the draft for faithfulness and relevance before you see it.",
  },
  {
    title: "Escalate or decline",
    text: "If the audit fails, it searches wider, up to three rounds. Still nothing? A safe fallback, never a guess.",
  },
];

const FEATURES = [
  {
    title: "Cited answers only",
    text: "Every medical statement comes from your documents and points back to the file and page.",
  },
  {
    title: "Runs on local AI",
    text: "Generation and embeddings run through Ollama on this machine. No cloud AI, no data leaves.",
  },
  {
    title: "Emergency red flags",
    text: "Messages that sound like an emergency get a clear call-for-help notice before anything else.",
  },
  {
    title: "Honest about gaps",
    text: "Doses, pregnancy and interactions are stated only when the documents say so. Otherwise it tells you to ask a doctor.",
  },
];

export function HomePage() {
  const { isAuthenticated, role } = useAuth();

  return (
    <div className="min-h-screen bg-white text-slate-900">
      <header className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4">
        <Logo className="text-lg" />
        <nav className="flex items-center gap-2 text-sm font-medium">
          {isAuthenticated ? (
            <Link
              to={homeFor(role)}
              className="rounded-md px-3 py-1.5 text-slate-600 hover:bg-slate-100"
            >
              {role === "admin" ? "Admin" : "Open chat"}
            </Link>
          ) : (
            <>
              <Link
                to="/login"
                className="rounded-md px-3 py-1.5 text-slate-600 hover:bg-slate-100"
              >
                Sign in
              </Link>
              <Link
                to="/register"
                className="rounded-md border border-slate-300 px-3 py-1.5 text-slate-700 hover:bg-slate-50"
              >
                Create account
              </Link>
            </>
          )}
        </nav>
      </header>

      <main>
        <section className="bg-gradient-to-b from-teal-50 to-white">
          <div className="mx-auto max-w-5xl px-4 py-16 text-center sm:py-24">
            <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">
              Self-correcting dual-agent RAG
            </p>
            <h1 className="mt-3 bg-gradient-to-r from-teal-700 to-emerald-500 bg-clip-text text-5xl font-extrabold tracking-tight text-transparent sm:text-7xl">
              VERIDOC AI
            </h1>
            <p className="mx-auto mt-5 max-w-xl text-lg text-slate-600">
              Medical answers you can trace back to the source. One AI drafts, another checks, and
              if it can&apos;t be verified, it says so.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <Link
                to="/chat"
                className="rounded-lg bg-teal-600 px-6 py-3 text-base font-semibold text-white shadow-sm hover:bg-teal-700"
              >
                Try out AI Doctor
              </Link>
              <a
                href="#how-it-works"
                className="rounded-lg border border-slate-300 px-6 py-3 text-base font-medium text-slate-700 hover:bg-slate-50"
              >
                How it works
              </a>
            </div>
          </div>
        </section>

        <section id="how-it-works" className="mx-auto max-w-5xl px-4 py-14">
          <h2 className="text-center text-2xl font-bold">How an answer is made</h2>
          <p className="mx-auto mt-2 max-w-xl text-center text-sm text-slate-500">
            Two agents, one rule: nothing reaches you unless it checks out against the references.
          </p>
          <ol className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, index) => (
              <li key={step.title} className="rounded-xl border border-slate-200 bg-white p-5">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-teal-600 text-sm font-bold text-white">
                  {index + 1}
                </span>
                <h3 className="mt-3 font-semibold">{step.title}</h3>
                <p className="mt-1 text-sm text-slate-600">{step.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="bg-slate-50">
          <div className="mx-auto max-w-5xl px-4 py-14">
            <h2 className="text-center text-2xl font-bold">Built to be careful</h2>
            <div className="mt-8 grid gap-4 sm:grid-cols-2">
              {FEATURES.map((feature) => (
                <div
                  key={feature.title}
                  className="rounded-xl border border-slate-200 border-l-4 border-l-teal-600 bg-white p-5"
                >
                  <h3 className="font-semibold">{feature.title}</h3>
                  <p className="mt-1 text-sm text-slate-600">{feature.text}</p>
                </div>
              ))}
            </div>
            <div className="mt-10 text-center">
              <Link
                to="/chat"
                className="inline-block rounded-lg bg-teal-600 px-6 py-3 font-semibold text-white hover:bg-teal-700"
              >
                Try out AI Doctor
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-slate-200">
        <div className="mx-auto max-w-5xl space-y-2 px-4 py-8 text-center text-xs text-slate-500">
          <p className="mx-auto max-w-2xl">{DISCLAIMER}</p>
          <p>
            B.Tech CSE Major Project · Rishi Raj Anand (23103121) · Vedant Salve (23117040) ·
            Mentor: Dr. Jagdeep Kaur
          </p>
        </div>
      </footer>
    </div>
  );
}
