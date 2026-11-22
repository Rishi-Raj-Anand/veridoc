/**
 * Doctor AI chat (CLAUDE.md §10).
 *
 * The session id lives in React state only, so a refresh starts a fresh session by design
 * (§6.7). Users see the final answer only: no agent states, scores or iteration counts.
 */

import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { sendMessage } from "../api/chat";
import { errorMessage } from "../api/client";
import { Composer } from "../components/chat/Composer";
import { DisclaimerBanner } from "../components/chat/DisclaimerBanner";
import {
  AssistantBubble,
  ErrorBubble,
  UserBubble,
  type ChatMessage,
} from "../components/chat/MessageBubble";
import { ThinkingIndicator } from "../components/chat/ThinkingIndicator";

const EXAMPLE_QUESTIONS = [
  "What are the symptoms of malaria?",
  "How is tuberculosis treated?",
  "What helps with a common cold?",
];

export function ChatPage() {
  const [sessionId] = useState(() => crypto.randomUUID());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const nextId = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  const turn = useMutation({
    mutationFn: (text: string) => sendMessage({ session_id: sessionId, message: text }),
    onSuccess: (response) => {
      setMessages((current) => [...current, { id: nextId.current++, role: "assistant", response }]);
    },
    onError: (error, text) => {
      const message = errorMessage(error);
      setMessages((current) => [
        ...current,
        { id: nextId.current++, role: "error", text: message, retryText: text },
      ]);
    },
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, turn.isPending]);

  function send(text: string) {
    const trimmed = text.trim();
    if (trimmed === "" || turn.isPending) {
      return;
    }
    setMessages((current) => [...current, { id: nextId.current++, role: "user", text: trimmed }]);
    setDraft("");
    turn.mutate(trimmed);
  }

  function retry(errorId: number, text: string) {
    // Drop the error bubble and resend; the user's message is already in the list.
    setMessages((current) => current.filter((message) => message.id !== errorId));
    turn.mutate(text);
  }

  const lastId = messages.at(-1)?.id;

  return (
    <div className="flex flex-1 flex-col gap-4">
      <DisclaimerBanner />

      <div className="flex-1 space-y-4" aria-live="polite">
        {messages.length === 0 && !turn.isPending && (
          <div className="mx-auto mt-10 max-w-md text-center">
            <h1 className="text-xl font-semibold">Ask Doctor AI</h1>
            <p className="mt-2 text-sm text-slate-500">
              Answers come only from the uploaded medical references, with sources. Each visit
              starts a fresh conversation.
            </p>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {EXAMPLE_QUESTIONS.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => setDraft(question)}
                  className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs text-slate-600 hover:border-teal-600 hover:text-teal-700"
                >
                  {question}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message) => {
          switch (message.role) {
            case "user":
              return <UserBubble key={message.id} text={message.text} />;
            case "assistant":
              return <AssistantBubble key={message.id} response={message.response} />;
            case "error":
              return (
                <ErrorBubble
                  key={message.id}
                  text={message.text}
                  onRetry={
                    message.id === lastId && !turn.isPending
                      ? () => retry(message.id, message.retryText)
                      : null
                  }
                />
              );
          }
        })}

        {turn.isPending && <ThinkingIndicator />}
        <div ref={bottomRef} />
      </div>

      <Composer
        value={draft}
        disabled={turn.isPending}
        onChange={setDraft}
        onSend={() => send(draft)}
      />
    </div>
  );
}
