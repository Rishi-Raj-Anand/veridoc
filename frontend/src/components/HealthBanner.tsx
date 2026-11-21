/**
 * Development-only status strip proving the frontend -> backend -> AI service ->
 * Ollama/Qdrant chain is live. Replaced by the real chat chrome in Phase 6.
 */

import { useQuery } from "@tanstack/react-query";

import { getHealth } from "../api/health";

export function HealthBanner() {
  const { data, isPending, error } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    retry: false,
    refetchInterval: 15_000,
  });

  if (isPending) {
    return <p className="text-sm text-slate-500">Checking services…</p>;
  }

  if (error !== null) {
    return <p className="text-sm text-red-700">Backend unreachable: {error.message}</p>;
  }

  const ai = data.ai_service;
  const tone = data.status === "ok" ? "text-emerald-700" : "text-amber-700";

  return (
    <div className={`text-sm ${tone}`}>
      <p>
        backend ok · ai service {ai === null ? "unreachable" : ai.status}
        {data.error !== null && ` · ${data.error}`}
      </p>
      {ai !== null && (
        <p className="text-slate-500">
          ollama {ai.ollama.ok ? "up" : "down"} · llm {ai.models.llm ? "✓" : "✗"} · embed{" "}
          {ai.models.embed ? "✓" : "✗"} · qdrant {ai.qdrant.ok ? "open" : "closed"}
          {ai.models.missing.length > 0 && ` · missing: ${ai.models.missing.join(", ")}`}
        </p>
      )}
    </div>
  );
}
