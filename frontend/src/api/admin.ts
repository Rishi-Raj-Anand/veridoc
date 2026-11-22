/** Admin document endpoint types and calls (CLAUDE.md §9). Admin token required. */

import { request } from "./client";

export const UPLOAD_TIMEOUT_MS = 300_000;

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

export interface UploadLimits {
  max_upload_mb: number;
  accepted_extensions: string[];
}

/** The backend's real `MAX_UPLOAD_MB`, so the page never refuses a file the server accepts. */
export function getUploadLimits(): Promise<UploadLimits> {
  return request<UploadLimits>("/api/admin/documents/limits");
}

export function listDocuments(): Promise<DocumentItem[]> {
  return request<DocumentItem[]>("/api/admin/documents");
}

export function uploadDocument(file: File): Promise<DocumentItem> {
  const form = new FormData();
  form.append("file", file);
  // Indexing runs in the background; this only covers sending the file (up to 25 MB).
  return request<DocumentItem>("/api/admin/documents", {
    method: "POST",
    body: form,
    timeoutMs: UPLOAD_TIMEOUT_MS,
    timeoutMessage: "The upload took too long. Check the connection and try again.",
  });
}

export function deleteDocument(id: number): Promise<void> {
  return request<void>(`/api/admin/documents/${id}`, { method: "DELETE" });
}
