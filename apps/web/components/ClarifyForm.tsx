"use client";

import { useState } from "react";
import { ApiError, answerClarification } from "@/lib/api";
import { buildAnswers, defaultValues, validateAnswer } from "@/lib/clarify";
import type { ClarifyQuestion } from "@/lib/types";

const input = "mt-1 rounded-md border border-line bg-bg px-3 py-2 text-sm";

export default function ClarifyForm({ jobId, questions, onDone }: { jobId: string; questions: ClarifyQuestion[]; onDone: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(() => defaultValues(questions));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const errors = Object.fromEntries(questions.map((q) => [q.id, validateAnswer(q, values[q.id] ?? "")]));
  const invalid = Object.values(errors).some(Boolean);

  async function send(useDefaults: boolean) {
    setBusy(true);
    setError(null);
    try {
      await answerClarification(jobId, buildAnswers(questions, values, useDefaults));
      setMessage("Answers sent. Continuing…");
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setMessage("This job has already moved on. Refreshing…");
        onDone();
      } else {
        setError(e instanceof Error ? e.message : String(e));
        setBusy(false);
      }
    }
  }

  const set = (id: string, v: string) => setValues((s) => ({ ...s, [id]: v }));

  return (
    <form
      aria-labelledby="clarify-title"
      onSubmit={(e) => { e.preventDefault(); if (!invalid) void send(false); }}
      className="space-y-4 rounded-lg border border-accent bg-surface p-4"
    >
      <div>
        <h2 id="clarify-title" className="text-lg font-semibold">A few questions before we start</h2>
        <p className="text-sm text-muted">Each answer is prefilled with a sensible default. Change what matters to you.</p>
      </div>
      {questions.map((q) => {
        const v = values[q.id] ?? "";
        const isDefault = v.trim() === "" || v === q.default;
        const hid = `q-${q.id}-why`;
        const eid = `q-${q.id}-err`;
        const common = {
          id: `q-${q.id}`,
          "aria-describedby": q.why ? hid : undefined,
        };
        return (
          <div key={q.id}>
            {q.kind === "choice" || q.kind === "boolean" ? (
              <fieldset aria-describedby={q.why ? hid : undefined}>
                <legend className="text-sm font-medium">
                  {q.question}
                  {isDefault ? <span className="ml-2 rounded-full border border-warn px-2 py-0.5 text-xs font-normal text-warn">default</span> : null}
                </legend>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                  {(q.kind === "boolean" ? ["yes", "no"] : q.options).map((o) => (
                    <label key={o} className="flex items-center gap-1.5 text-sm">
                      <input type="radio" name={`q-${q.id}`} value={o} checked={v.toLowerCase() === o.toLowerCase()} onChange={() => set(q.id, o)} />
                      {o}
                    </label>
                  ))}
                </div>
              </fieldset>
            ) : (
              <>
                <label htmlFor={common.id} className="text-sm font-medium">
                  {q.question}
                  {isDefault ? <span className="ml-2 rounded-full border border-warn px-2 py-0.5 text-xs font-normal text-warn">default</span> : null}
                </label>
                <div className="flex items-center gap-2">
                  <input
                    {...common}
                    type="text"
                    inputMode={q.kind === "number" ? "decimal" : undefined}
                    value={v}
                    aria-invalid={errors[q.id] ? true : undefined}
                    aria-errormessage={errors[q.id] ? eid : undefined}
                    onChange={(e) => set(q.id, e.target.value)}
                    className={`${input} ${q.kind === "number" ? "w-40" : "w-full"}`}
                  />
                  {q.kind === "number" && q.unit ? <span className="mt-1 text-sm text-muted">{q.unit}</span> : null}
                </div>
              </>
            )}
            {q.why ? <p id={hid} className="mt-1 text-xs text-muted">{q.why}</p> : null}
            {errors[q.id] ? <p id={eid} role="alert" className="text-xs text-bad">{errors[q.id]}</p> : null}
          </div>
        );
      })}
      {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
      <p aria-live="polite" className="text-sm text-muted">{message}</p>
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy || invalid} className="h-10 rounded-md bg-accent px-5 text-sm font-semibold text-accent-fg disabled:opacity-50">
          Submit answers
        </button>
        <button type="button" disabled={busy} onClick={() => void send(true)} className="h-10 rounded-md border border-line px-5 text-sm disabled:opacity-50">
          Use defaults for all
        </button>
      </div>
    </form>
  );
}
