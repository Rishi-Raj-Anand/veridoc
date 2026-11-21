/** Persistent banner above the conversation (CLAUDE.md §7.5). */

import { DISCLAIMER } from "../../constants";

export function DisclaimerBanner() {
  return (
    <p
      role="note"
      className="sticky top-0 z-10 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 shadow-sm"
    >
      {DISCLAIMER}
    </p>
  );
}
