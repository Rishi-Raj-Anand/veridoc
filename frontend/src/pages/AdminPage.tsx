/** Admin: system status, upload, the knowledge-base document list and delete (CLAUDE.md §10). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ChangeEvent } from "react";

import { deleteDocument, listDocuments, uploadDocument } from "../api/admin";
import { ApiError } from "../api/client";
import { AdminTabs } from "../components/admin/AdminTabs";
import { DocumentTable } from "../components/admin/DocumentTable";
import { HealthBanner } from "../components/HealthBanner";
import { ACCEPTED_EXTENSIONS } from "../constants";

const POLL_MS = 2000;
const NETWORK_ERROR = "Could not reach the server.";

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : NETWORK_ERROR;
}

function hasAcceptedExtension(filename: string): boolean {
  const lower = filename.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((extension) => lower.endsWith(extension));
}

export function AdminPage() {
  const queryClient = useQueryClient();
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [clientError, setClientError] = useState<string | null>(null);

  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: listDocuments,
    // Poll only while something is still being ingested.
    refetchInterval: (query) =>
      query.state.data?.some((document) => document.status === "pending") ? POLL_MS : false,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["documents"] });

  const upload = useMutation({ mutationFn: uploadDocument, onSuccess: refresh });
  const remove = useMutation({
    mutationFn: deleteDocument,
    onSuccess: () => {
      setConfirmingId(null);
      return refresh();
    },
  });

  function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // allow picking the same file again after a failure
    if (file === undefined) {
      return;
    }
    if (!hasAcceptedExtension(file.name)) {
      setClientError(`Unsupported file type. Use ${ACCEPTED_EXTENSIONS.join(", ")}.`);
      return;
    }
    setClientError(null);
    upload.reset();
    upload.mutate(file);
  }

  const uploadError = clientError ?? (upload.isError ? describe(upload.error) : null);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Knowledge base</h1>
      <AdminTabs />

      <HealthBanner />

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700">Upload a document</h2>
        <p className="mt-1 text-sm text-slate-500">
          Text-based {ACCEPTED_EXTENSIONS.join(", ")} files only. Scanned or image-only PDFs are
          rejected. A CSV becomes one searchable section per row (its first column is the heading),
          so each row can be cited on its own.
        </p>
        <div className="mt-3 flex items-center gap-3">
          <input
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            aria-label="Choose a document to upload"
            onChange={handleFile}
            disabled={upload.isPending}
            className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-teal-600 file:px-3 file:py-1.5 file:text-sm file:font-semibold file:text-white hover:file:bg-teal-700"
          />
          {upload.isPending && <span className="text-sm text-slate-500">Uploading…</span>}
        </div>
        {uploadError !== null && (
          <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {uploadError}
          </p>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">Documents</h2>
        {remove.isError && (
          <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {describe(remove.error)}
          </p>
        )}
        {documents.isPending && <p className="text-sm text-slate-500">Loading documents…</p>}
        {documents.isError && (
          <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {describe(documents.error)}
          </p>
        )}
        {documents.data !== undefined && (
          <DocumentTable
            documents={documents.data}
            confirmingId={confirmingId}
            deletingId={remove.isPending ? (remove.variables ?? null) : null}
            onAskDelete={(id) => {
              remove.reset();
              setConfirmingId(id);
            }}
            onCancelDelete={() => setConfirmingId(null)}
            onConfirmDelete={(id) => remove.mutate(id)}
          />
        )}
      </section>
    </div>
  );
}
