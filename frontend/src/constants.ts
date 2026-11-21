/**
 * UI copy that must be on screen before the first API reply.
 *
 * The disclaimer mirrors `DISCLAIMER` in `ai/ai_service/config.py` (CLAUDE.md §7.5, §10).
 * Each answer still displays the `disclaimer` the API returns; this constant only feeds the
 * persistent banner. Change both together.
 */

export const DISCLAIMER =
  "VeriDoc AI provides information from reference documents and is not a substitute for professional medical advice, diagnosis, or treatment.";

export const ACCEPTED_EXTENSIONS = [".pdf", ".txt", ".md", ".docx"] as const;

export const MAX_MESSAGE_CHARS = 2000;
export const MIN_PASSWORD_CHARS = 8;
