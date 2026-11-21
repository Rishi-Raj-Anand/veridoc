/** Admin document endpoint types and calls (CLAUDE.md §9). Admin token required. */

import { request } from "./client";

export type DocumentStatus = "pending" | "ready" | "failed";

export interface DocumentItem {
  id: number;
  filename: string;
  size_bytes: number;
  status: DocumentStatus;
  chunk_count: number;
  error: string | null;
  created_at: string;
}

export function listDocuments(): Promise<DocumentItem[]> {
  return request<DocumentItem[]>("/api/admin/documents");
}

export function uploadDocument(file: File): Promise<DocumentItem> {
  const form = new FormData();
  form.append("file", file);
  return request<DocumentItem>("/api/admin/documents", { method: "POST", body: form });
}

export function deleteDocument(id: number): Promise<void> {
  return request<void>(`/api/admin/documents/${id}`, { method: "DELETE" });
}
