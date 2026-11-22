/**
 * Admin: how Doctor AI behaves (CLAUDE.md §6.9).
 *
 * The admin edits tone, scope, the off-topic reply and extra guardrails. These are appended
 * to the prompts from the next message. The §7 safety rules are shown read-only: admin text
 * can add restrictions but never loosen them.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  getBehaviour,
  updateBehaviour,
  type BehaviourResponse,
  type BehaviourSettings,
} from "../api/behaviour";
import { errorMessage } from "../api/client";
import { AdminTabs } from "../components/admin/AdminTabs";
import { BEHAVIOUR_LIMITS } from "../constants";

type Field = keyof BehaviourSettings;

const FIELDS: { key: Field; label: string; help: string; rows: number; required: boolean }[] = [
  {
    key: "persona",
    label: "Tone and style",
    help: "How Doctor AI sounds in small talk and answers. It changes the voice, not what Doctor AI is allowed to say.",
    rows: 3,
    required: false,
  },
  {
    key: "scope",
    label: "What counts as in scope",
    help: "Used to decide which messages are medical questions. Anything clearly outside it is declined.",
    rows: 3,
    required: true,
  },
  {
    key: "out_of_scope_message",
    label: "Reply to off-topic requests",
    help: "Shown word for word when someone asks for help with something else, such as chores, recipes or homework.",
    rows: 2,
    required: true,
  },
  {
    key: "extra_guardrails",
    label: "Extra guardrails",
    help: "Additional rules for answers and small talk, one per line. They can only add restrictions: the locked safety rules below always win.",
    rows: 5,
    required: false,
  },
];

const STEPS = [
  "Safety check: red-flag emergencies get the call-for-help notice first and always go to the medical path.",
  "Sort the message: small talk gets a short friendly reply, off-topic requests get your refusal text, and everything health-related goes on to step 3.",
  "Medical answers: find passages in the uploaded documents, draft an answer citing them, have the Verifier audit it, and search wider up to three rounds. If it still can't be verified, reply with the safe fallback instead of a guess.",
];

function describe(error: unknown): string {
  return errorMessage(error);
}

function sameSettings(a: BehaviourSettings, b: BehaviourSettings): boolean {
  return FIELDS.every(({ key }) => a[key] === b[key]);
}

function problems(form: BehaviourSettings): Partial<Record<Field, string>> {
  const found: Partial<Record<Field, string>> = {};
  for (const { key, label, required } of FIELDS) {
    if (form[key].length > BEHAVIOUR_LIMITS[key]) {
      found[key] = `${label} is ${form[key].length - BEHAVIOUR_LIMITS[key]} characters too long.`;
    } else if (required && form[key].trim() === "") {
      found[key] = `${label} cannot be empty.`;
    }
  }
  return found;
}

type SaveMutation = UseMutationResult<BehaviourResponse, Error, BehaviourSettings>;

function BehaviourForm({ data, save }: { data: BehaviourResponse; save: SaveMutation }) {
  const [form, setForm] = useState<BehaviourSettings>(data.settings);

  const dirty = !sameSettings(form, data.settings);
  const errors = problems(form);
  const valid = Object.keys(errors).length === 0;
  const isDefault = sameSettings(form, data.defaults);

  useEffect(() => {
    if (!dirty) {
      return;
    }
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  return (
    <form
      className="space-y-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (dirty && valid) {
          save.mutate(form);
        }
      }}
    >
      {FIELDS.map(({ key, label, help, rows }) => {
        const length = form[key].length;
        const limit = BEHAVIOUR_LIMITS[key];
        return (
          <div key={key}>
            <label htmlFor={key} className="text-sm font-semibold text-slate-700">
              {label}
            </label>
            <p className="mt-0.5 text-xs text-slate-500">{help}</p>
            <textarea
              id={key}
              rows={rows}
              value={form[key]}
              onChange={(event) => setForm({ ...form, [key]: event.target.value })}
              className="mt-2 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-teal-600 focus:outline-none focus:ring-2 focus:ring-teal-600/30"
            />
            <div className="mt-1 flex justify-between gap-2 text-xs">
              <span className="text-red-600">{errors[key] ?? ""}</span>
              <span className={length > limit ? "text-red-600" : "text-slate-400"}>
                {length}/{limit}
              </span>
            </div>
          </div>
        );
      })}

      {save.isError && (
        <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {describe(save.error)}
        </p>
      )}
      {save.isSuccess && !dirty && (
        <p role="status" className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          Saved. Doctor AI uses these settings from the next message.
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="submit"
          disabled={!dirty || !valid || save.isPending}
          className="rounded-md bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {save.isPending ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          disabled={!dirty || save.isPending}
          onClick={() => setForm(data.settings)}
          className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Discard changes
        </button>
        <button
          type="button"
          disabled={isDefault || save.isPending}
          onClick={() => setForm(data.defaults)}
          className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Restore defaults
        </button>
      </div>
    </form>
  );
}

export function AdminBehaviourPage() {
  const queryClient = useQueryClient();
  const behaviour = useQuery({ queryKey: ["behaviour"], queryFn: getBehaviour });
  // Owned here, not by the form: the form remounts after a save, and the "Saved" message
  // must survive that.
  const save = useMutation({
    mutationFn: updateBehaviour,
    onSuccess: (saved) => queryClient.setQueryData(["behaviour"], saved),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Assistant behaviour</h1>
      <AdminTabs />

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700">How Doctor AI handles a message</h2>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-slate-600">
          {STEPS.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700">Behaviour and guardrails</h2>
        {behaviour.data?.updated_at && (
          <p className="mt-0.5 text-xs text-slate-500">
            Last saved {new Date(behaviour.data.updated_at).toLocaleString()}
          </p>
        )}
        <div className="mt-4">
          {behaviour.isPending && <p className="text-sm text-slate-500">Loading…</p>}
          {behaviour.isError && (
            <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              {describe(behaviour.error)}
            </p>
          )}
          {behaviour.data !== undefined && (
            // Remount after each save so the form starts from what was stored.
            <BehaviourForm
              key={behaviour.data.updated_at ?? "defaults"}
              data={behaviour.data}
              save={save}
            />
          )}
        </div>
      </section>

      {behaviour.data !== undefined && (
        <section className="rounded-xl border border-slate-200 bg-slate-50 p-4">
          <h2 className="text-sm font-semibold text-slate-700">Locked safety rules</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Built in and always applied. They cannot be edited here.
          </p>
          <ul className="mt-2 space-y-1.5 text-sm text-slate-700">
            {behaviour.data.locked_rules.map((rule) => (
              <li key={rule} className="flex gap-2">
                <span aria-hidden="true">🔒</span>
                <span>{rule}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
