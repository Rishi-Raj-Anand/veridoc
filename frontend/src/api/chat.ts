/** Chat endpoint types and call (CLAUDE.md §9; `ChatResponse` is frozen across tracks). */

import { request } from "./client";

export interface Citation {
  n: number;
  filename: string;
  page: number;
}

export interface ChatMessageRequest {
  session_id: string;
  message: string;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  status: "verified" | "fallback";
  emergency: boolean;
  disclaimer: string;
}

export function sendMessage(body: ChatMessageRequest): Promise<ChatResponse> {
  return request<ChatResponse>("/api/chat/message", { method: "POST", body });
}
