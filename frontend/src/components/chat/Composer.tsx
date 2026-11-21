/** Message input: Enter sends, Shift+Enter inserts a newline. */

import { useRef, type KeyboardEvent } from "react";

import { MAX_MESSAGE_CHARS } from "../../constants";

interface ComposerProps {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
}

export function Composer({ value, disabled, onChange, onSend }: ComposerProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const tooLong = value.length > MAX_MESSAGE_CHARS;
  const canSend = !disabled && value.trim().length > 0 && !tooLong;

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // isComposing: Enter while confirming an IME candidate must not send.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (canSend) {
        onSend();
      }
    }
  }

  return (
    <div className="sticky bottom-0 bg-slate-50 pb-2 pt-2">
      <div className="flex items-end gap-2">
        <textarea
          ref={inputRef}
          aria-label="Message to Doctor AI"
          rows={2}
          value={value}
          disabled={disabled}
          placeholder="Describe your question about a condition or remedy…"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          className="min-h-[3rem] flex-1 resize-none rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-teal-600 focus:outline-none focus:ring-2 focus:ring-teal-600/30 disabled:bg-slate-100"
        />
        <button
          type="button"
          onClick={onSend}
          disabled={!canSend}
          className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Send
        </button>
      </div>
      <p className={`mt-1 text-right text-xs ${tooLong ? "text-red-600" : "text-slate-400"}`}>
        {value.length}/{MAX_MESSAGE_CHARS}
      </p>
    </div>
  );
}
