/**
 * System status card for the admin page: proves the frontend -> backend -> AI service ->
 * Ollama/Qdrant chain is live, so an admin can see why an upload or a chat would fail.
 */

import { useQuery } from "@tanstack/react-query";

import { getHealth } from "../api/health";

function Pill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
        ok ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"
      }`}
    >
      {label}
    </span>
  );
}

export function HealthBanner() {
  const { data, isPending, error } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    retry: false,
    refetchInterval: 15_000,
  });

  let content;
  if (isPending) {
    content = <p className="text-sm text-slate-500">Checking services…</p>;
  } else if (error !== null) {
    content = <p className="text-sm text-red-700">Backend unreachable: {error.message}</p>;
  } else {
    const ai = data.ai_service;
    content = (
      <div className="space-y-2">
        <div className="flex flex-wrap gap-2">
          <Pill ok label="backend ok" />
          <Pill
            ok={ai !== null && ai.status === "ok"}
            label={`ai service ${ai?.status ?? "unreachable"}`}
          />
          {ai !== null && (
            <>
              <Pill ok={ai.ollama.ok} label={`ollama ${ai.ollama.ok ? "up" : "down"}`} />
              <Pill ok={ai.models.llm} label={`llm ${ai.models.llm ? "ready" : "missing"}`} />
              <Pill
                ok={ai.models.embed}
                label={`embeddings ${ai.models.embed ? "ready" : "missing"}`}
              />
              <Pill ok={ai.qdrant.ok} label={`vector store ${ai.qdrant.ok ? "open" : "closed"}`} />
            </>
          )}
        </div>
        {data.error !== null && <p className="text-sm text-amber-700">{data.error}</p>}
        {ai !== null && ai.models.missing.length > 0 && (
          <p className="text-sm text-amber-700">Missing models: {ai.models.missing.join(", ")}</p>
        )}
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="mb-2 text-sm font-semibold text-slate-700">System status</h2>
      {content}
    </section>
  );
}
