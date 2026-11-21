/** Red banner shown when the safety check flagged the message (CLAUDE.md §7.2). */

import { AnswerBody } from "./AnswerBody";

export function EmergencyBanner({ notice }: { notice: string }) {
  return (
    <div
      role="alert"
      className="mb-3 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-red-800"
    >
      <AnswerBody text={notice} />
    </div>
  );
}

/**
 * The AI service prefixes the emergency notice as the answer's first paragraph (Phase 5).
 * Split it off so it can be shown in the banner without repeating the text here or showing
 * it twice. If there is no blank line, the whole answer is the notice and the body is empty.
 */
export function splitEmergencyNotice(answer: string): { notice: string; body: string } {
  const index = answer.indexOf("\n\n");
  if (index === -1) {
    return { notice: answer.trim(), body: "" };
  }
  return { notice: answer.slice(0, index).trim(), body: answer.slice(index).trim() };
}
