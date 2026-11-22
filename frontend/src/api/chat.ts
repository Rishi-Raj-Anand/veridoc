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
  /** verified/fallback: the medical RAG loop. smalltalk/out_of_scope: intent routing (§6.9). */
  status: "verified" | "fallback" | "smalltalk" | "out_of_scope";
  emergency: boolean;
  disclaimer: string;
}

/** Above the backend's 300 s wait for the AI service, so the backend's own message wins. */
export const CHAT_TIMEOUT_MS = 330_000;
export const CHAT_TIMEOUT_ERROR = "Doctor AI took too long to answer. Please try again.";

export function sendMessage(body: ChatMessageRequest): Promise<ChatResponse> {
  return request<ChatResponse>("/api/chat/message", {
    method: "POST",
    body,
    timeoutMs: CHAT_TIMEOUT_MS,
    timeoutMessage: CHAT_TIMEOUT_ERROR,
  });
}
