/** Shown while a turn is in flight. Answers can take up to a minute (CLAUDE.md §6.8). */

export function ThinkingIndicator() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="max-w-[85%] rounded-2xl rounded-bl-sm border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600 shadow-sm"
    >
      <span className="font-medium">Thinking</span>
      <span aria-hidden="true" className="ml-0.5 inline-flex gap-0.5">
        <span className="animate-bounce [animation-delay:-0.3s]">.</span>
        <span className="animate-bounce [animation-delay:-0.15s]">.</span>
        <span className="animate-bounce">.</span>
      </span>
      <p className="mt-1 text-xs text-slate-400">
        Checking the references and verifying the answer. This can take up to a minute.
      </p>
    </div>
  );
}
