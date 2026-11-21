/** Custom 404: a tongue-in-cheek medical case file for a page that does not exist. */

import { Link, useLocation } from "react-router";

import { LogoMark } from "../components/Logo";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 py-2 sm:grid-cols-[9rem_1fr] sm:gap-4">
      <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="text-sm text-slate-800">{children}</dd>
    </div>
  );
}

export function NotFoundPage() {
  const { pathname } = useLocation();

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-50 px-4 py-10 text-slate-900">
      <div className="w-full max-w-xl">
        <div className="flex items-center justify-center gap-2 text-sm font-semibold text-slate-500">
          <LogoMark className="h-6 w-6" /> VeriDoc AI · Patient file
        </div>

        <h1 className="mt-4 text-center text-7xl font-extrabold tracking-tight text-slate-900">
          404
        </h1>
        <p className="mt-1 text-center text-lg font-semibold text-teal-700">
          Diagnosis: Acute Page-Not-Found-itis
        </p>

        <div className="mt-5 rounded-xl border border-slate-800 bg-slate-900 p-3 shadow-sm">
          <svg
            viewBox="0 0 400 120"
            className="h-24 w-full"
            role="img"
            aria-label="A flat heartbeat line"
          >
            <path
              className="ekg-line"
              pathLength={1}
              d="M0 60 H40 L46 60 L52 30 L60 90 L66 50 L72 60 H130 L136 60 L142 30 L150 90 L156 50 L162 60 H400"
              fill="none"
              stroke="#34d399"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <p className="animate-pulse text-center text-xs font-semibold uppercase tracking-widest text-red-400">
            ● No pulse detected
          </p>
        </div>

        <dl className="mt-5 divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white px-4 py-2 shadow-sm">
          <Row label="Patient">
            <code className="break-all rounded bg-slate-100 px-1.5 py-0.5">{pathname}</code>
          </Row>
          <Row label="Symptoms">Clicked a link. Saw nothing. Felt lost.</Row>
          <Row label="Verifier audit">
            faithfulness 0.00 · relevance 0.00 · verdict:{" "}
            <strong className="text-red-700">fail</strong>
          </Row>
          <Row label="Escalation">Searched Top-3, Top-8 and Top-15. Still nothing.</Row>
          <Row label="Prescription">One click on a button below. Call us in the morning.</Row>
        </dl>

        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <Link
            to="/home"
            className="rounded-lg bg-teal-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-teal-700"
          >
            Take me home
          </Link>
          <Link
            to="/chat"
            className="rounded-lg border border-slate-300 bg-white px-5 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Ask Doctor AI instead
          </Link>
        </div>
        <p className="mt-4 text-center text-xs italic text-slate-400">
          Side effects may include finding what you were looking for.
        </p>
      </div>
    </div>
  );
}
