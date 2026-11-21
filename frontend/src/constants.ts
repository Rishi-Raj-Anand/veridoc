/**
 * UI copy that must be on screen before the first API reply.
 *
 * The disclaimer mirrors `DISCLAIMER` in `ai/ai_service/config.py` (CLAUDE.md §7.5, §10).
 * Each answer still displays the `disclaimer` the API returns; this constant only feeds the
 * persistent banner. Change both together.
 */

export const DISCLAIMER =
  "VeriDoc AI provides information from reference documents and is not a substitute for professional medical advice, diagnosis, or treatment.";

export const ACCEPTED_EXTENSIONS = [".pdf", ".txt", ".md", ".docx", ".csv"] as const;

export const MAX_MESSAGE_CHARS = 2000;
export const MIN_PASSWORD_CHARS = 8;

/** Mirrors the caps in `ai/ai_service/behaviour.py` and `backend/app/schemas/behaviour.py`. */
export const BEHAVIOUR_LIMITS = {
  persona: 600,
  scope: 600,
  out_of_scope_message: 400,
  extra_guardrails: 1500,
} as const;
