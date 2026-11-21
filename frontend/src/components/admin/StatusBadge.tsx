import type { DocumentStatus } from "../../api/admin";

const STYLES: Record<DocumentStatus, string> = {
  pending: "bg-amber-100 text-amber-800",
  ready: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
};

export function StatusBadge({ status }: { status: DocumentStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${STYLES[status]}`}
    >
      {status === "pending" && (
        <span
          aria-hidden="true"
          className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-amber-600 border-t-transparent"
        />
      )}
      {status}
    </span>
  );
}
