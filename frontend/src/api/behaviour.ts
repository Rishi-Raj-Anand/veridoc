/** Admin assistant-behaviour types and calls (CLAUDE.md §6.9, §9). Admin token required. */

import { request } from "./client";

export interface BehaviourSettings {
  persona: string;
  scope: string;
  out_of_scope_message: string;
  extra_guardrails: string;
}

export interface BehaviourResponse {
  settings: BehaviourSettings;
  defaults: BehaviourSettings;
  locked_rules: string[];
  updated_at: string | null;
}

export function getBehaviour(): Promise<BehaviourResponse> {
  return request<BehaviourResponse>("/api/admin/behaviour");
}

export function updateBehaviour(body: BehaviourSettings): Promise<BehaviourResponse> {
  return request<BehaviourResponse>("/api/admin/behaviour", { method: "PUT", body });
}
