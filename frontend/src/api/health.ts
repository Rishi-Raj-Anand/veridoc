/** Health endpoint types and call (CLAUDE.md §9; contract frozen in task 0.4). */

import { request } from "./client";

export type HealthStatus = "ok" | "degraded";

export interface OllamaHealth {
  ok: boolean;
  host: string;
  error: string | null;
}

export interface ModelsHealth {
  llm: boolean;
  embed: boolean;
  missing: string[];
}

export interface QdrantHealth {
  ok: boolean;
  path: string;
  error: string | null;
}

export interface KnowledgeBaseHealth {
  /** Indexed child chunks; null when the AI service could not count them. */
  chunks: number | null;
}

export interface AiServiceHealth {
  status: HealthStatus;
  ollama: OllamaHealth;
  models: ModelsHealth;
  qdrant: QdrantHealth;
  /** Added in Phase 8; optional so an older AI service still type-checks at runtime. */
  knowledge_base?: KnowledgeBaseHealth;
}

export interface HealthResponse {
  status: HealthStatus;
  backend: "ok";
  ai_service: AiServiceHealth | null;
  error: string | null;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health");
}
