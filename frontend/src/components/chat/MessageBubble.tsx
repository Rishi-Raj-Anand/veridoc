/** One entry in the conversation: the user's message, a verified/fallback answer, or an error. */

import type { ChatResponse } from "../../api/chat";
import { AnswerBody } from "./AnswerBody";
import { CitationList } from "./CitationList";
import { EmergencyBanner, splitEmergencyNotice } from "./EmergencyBanner";

export type ChatMessage =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "assistant"; response: ChatResponse }
  | { id: number; role: "error"; text: string; retryText: string };

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-teal-600 px-4 py-2 text-sm text-white">
        {text}
      </p>
    </div>
  );
}

export function AssistantBubble({ response }: { response: ChatResponse }) {
  const { notice, body } = response.emergency
    ? splitEmergencyNotice(response.answer)
    : { notice: null, body: response.answer };
  // A fallback is a safe non-answer, not a failure: same bubble, softer accent.
  const accent = response.status === "fallback" ? "border-amber-200" : "border-slate-200";

  return (
    <div className="flex">
      <div
        className={`max-w-[85%] rounded-2xl rounded-bl-sm border ${accent} bg-white px-4 py-3 shadow-sm`}
      >
        {notice !== null && <EmergencyBanner notice={notice} />}
        {body !== "" && <AnswerBody text={body} />}
        <CitationList citations={response.citations} />
        <p className="mt-3 text-xs italic text-slate-400">{response.disclaimer}</p>
      </div>
    </div>
  );
}

export function ErrorBubble({ text, onRetry }: { text: string; onRetry: (() => void) | null }) {
  return (
    <div className="flex">
      <div
        role="alert"
        className="max-w-[85%] rounded-2xl rounded-bl-sm border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
      >
        <p>{text}</p>
        {onRetry !== null && (
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 rounded-md bg-red-600 px-3 py-1 text-xs font-semibold text-white hover:bg-red-700"
          >
            Retry
          </button>
        )}
      </div>
    </div>
  );
}
