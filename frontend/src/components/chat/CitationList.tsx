import type { Citation } from "../../api/chat";

export function CitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) {
    return null;
  }
  return (
    <div className="mt-3 border-t border-slate-200 pt-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Sources</p>
      <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
        {citations.map((citation) => (
          <li key={citation.n}>
            <span className="font-medium text-teal-700">[{citation.n}]</span> {citation.filename} ·
            p. {citation.page}
          </li>
        ))}
      </ul>
    </div>
  );
}
