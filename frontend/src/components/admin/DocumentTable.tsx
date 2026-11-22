import type { DocumentItem } from "../../api/admin";
import { StatusBadge } from "./StatusBadge";

interface DocumentTableProps {
  documents: DocumentItem[];
  confirmingId: number | null;
  deletingId: number | null;
  onAskDelete: (id: number) => void;
  onCancelDelete: () => void;
  onConfirmDelete: (id: number) => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function DocumentTable(props: DocumentTableProps) {
  if (props.documents.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">
        No documents yet. Upload a PDF, TXT, MD, DOCX or CSV to build the knowledge base.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-2 font-medium">Document</th>
            <th className="px-4 py-2 font-medium">Size</th>
            <th className="px-4 py-2 font-medium">Status</th>
            <th className="px-4 py-2 font-medium">Chunks</th>
            <th className="px-4 py-2 font-medium">Uploaded</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {props.documents.map((document) => {
            const confirming = props.confirmingId === document.id;
            const deleting = props.deletingId === document.id;
            return (
              <tr key={document.id} className="align-top">
                <td className="px-4 py-3">
                  <p className="break-all font-medium">{document.filename}</p>
                  {document.status === "failed" && document.error !== null && (
                    <p className="mt-1 text-xs text-red-700">{document.error}</p>
                  )}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                  {formatSize(document.size_bytes)}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={document.status} />
                </td>
                <td className="px-4 py-3 text-slate-600">
                  {document.status === "ready" ? document.chunk_count : "—"}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                  {new Date(document.created_at).toLocaleString()}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right">
                  {confirming ? (
                    <span className="inline-flex gap-2">
                      <button
                        type="button"
                        disabled={deleting}
                        onClick={() => props.onConfirmDelete(document.id)}
                        className="rounded-md bg-red-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60"
                      >
                        {deleting ? "Deleting…" : "Confirm"}
                      </button>
                      <button
                        type="button"
                        disabled={deleting}
                        onClick={props.onCancelDelete}
                        className="rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                      >
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      disabled={document.status === "pending"}
                      title={
                        document.status === "pending" ? "Wait until processing finishes" : undefined
                      }
                      onClick={() => props.onAskDelete(document.id)}
                      className="rounded-md border border-slate-300 px-2.5 py-1 text-xs text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:text-slate-400 disabled:hover:bg-transparent"
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
