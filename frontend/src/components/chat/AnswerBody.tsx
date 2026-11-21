/**
 * Renders a model answer as Markdown (CLAUDE.md §10).
 *
 * `skipHtml` drops raw HTML instead of rendering it, and links and images are removed
 * outright (their text is kept): an answer is plain prose with `[n]` citation markers and
 * has no reason to load or link anything.
 */

import ReactMarkdown from "react-markdown";

const BODY_CLASS =
  "break-words text-sm leading-relaxed " +
  "[&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0 " +
  "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 " +
  "[&_li]:my-0.5 [&_strong]:font-semibold [&_h1]:font-semibold [&_h2]:font-semibold " +
  "[&_h3]:font-semibold [&_code]:rounded [&_code]:bg-slate-100 [&_code]:px-1";

export function AnswerBody({ text }: { text: string }) {
  return (
    <div className={BODY_CLASS}>
      <ReactMarkdown skipHtml disallowedElements={["a", "img"]} unwrapDisallowed>
        {text}
      </ReactMarkdown>
    </div>
  );
}
