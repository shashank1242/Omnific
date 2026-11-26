"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, askAboutSimulation } from "@/lib/api";
import { blocks, CHAT_MAX_CHARS, type Inline, outgoing, starterQuestions } from "@/lib/chat";
import type { ChatMessage, Playback, SimSpec } from "@/lib/types";
import type { Player } from "./player";

interface Turn extends ChatMessage { grounded?: string[]; followUps?: string[] }

const storeKey = (jobId: string) => `omnific.chat.${jobId}`;

function load(jobId: string): Turn[] {
  try {
    const raw = sessionStorage.getItem(storeKey(jobId));
    const v = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(v) ? (v as Turn[]).filter((m) => (m.role === "user" || m.role === "assistant") && typeof m.content === "string") : [];
  } catch {
    return [];
  }
}

function save(jobId: string, turns: Turn[]) {
  try {
    sessionStorage.setItem(storeKey(jobId), JSON.stringify(turns.slice(-40)));
  } catch {
    /* private window or storage blocked: the conversation just isn't kept */
  }
}

function Spans({ parts }: { parts: Inline[] }) {
  return <>{parts.map((p, i) => (p.bold ? <strong key={i}>{p.text}</strong> : <span key={i}>{p.text}</span>))}</>;
}

function Answer({ text }: { text: string }) {
  return (
    <div className="space-y-2">
      {blocks(text).map((b, i) =>
        b.kind === "p" ? <p key={i}><Spans parts={b.parts} /></p>
          : b.kind === "ul" ? <ul key={i} className="list-disc space-y-1 pl-5">{b.items.map((it, j) => <li key={j}><Spans parts={it} /></li>)}</ul>
            : <ol key={i} className="list-decimal space-y-1 pl-5">{b.items.map((it, j) => <li key={j}><Spans parts={it} /></li>)}</ol>,
      )}
    </div>
  );
}

/** Ask the guide about this simulation (what happens, why, its numbers, how far to trust it) or the physics behind
 *  it. Answers come from this run's own record; the viewer's current moment goes with each question. */
export default function ChatPanel({ jobId, spec, pb, player, otherRun = false }: { jobId: string; spec: SimSpec | null; pb: Playback | null; player: Player | null; otherRun?: boolean }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement | null>(null);
  const box = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => setTurns(load(jobId)), [jobId]);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [turns, busy]);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    const history: ChatMessage[] = turns.map(({ role, content }) => ({ role, content }));
    const sent = outgoing(history, q);
    const asked: Turn[] = [...turns, { role: "user", content: sent[sent.length - 1]?.content ?? q }];
    setTurns(asked);
    setDraft("");
    setError(null);
    setBusy(true);
    const viewTime = player && !otherRun ? player.getState().time : null;
    try {
      const reply = await askAboutSimulation(jobId, { messages: sent, view_time: viewTime });
      const next: Turn[] = [...asked, { role: "assistant", content: reply.answer, grounded: reply.grounded_in, followUps: reply.follow_ups }];
      setTurns(next);
      save(jobId, next);
    } catch (e) {
      setTurns(turns);  // the question goes back in the box to try again
      setDraft(q);
      setError(e instanceof ApiError ? e.message : "Could not reach the guide. Check your connection and try again.");
    } finally {
      setBusy(false);
      box.current?.focus();
    }
  }

  const starters = starterQuestions(spec, pb);
  const lastFollowUps = turns.length && !busy ? (turns[turns.length - 1]?.followUps ?? []) : [];

  return (
    <section aria-labelledby="chat-h" className="flex flex-col rounded-xl border border-line bg-surface p-3">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 id="chat-h" className="text-sm font-bold tracking-tight">Ask about this simulation</h3>
        {turns.length ? (
          <button type="button" disabled={busy} onClick={() => { setTurns([]); save(jobId, []); setError(null); }} className="text-xs text-muted underline hover:text-fg">
            Clear
          </button>
        ) : null}
      </div>
      <p className="mb-3 text-xs text-muted">
        Questions about what happens, why, its numbers and how far to trust it, or the physics behind it. Answers use this
        run&apos;s own model, sources and results{pb ? " and the moment you are looking at" : ""}.
      </p>

      {otherRun ? <p className="mb-3 text-xs text-warn">Answers are about the original run, not the tuned run on screen.</p> : null}
      <div role="log" aria-live="polite" aria-label="Conversation" className="max-h-[28rem] min-h-[6rem] space-y-3 overflow-y-auto pr-1 text-sm">
        {turns.length === 0 ? (
          <ul aria-label="Suggested questions" className="flex flex-wrap gap-2">
            {starters.map((q) => (
              <li key={q}>
                <button type="button" onClick={() => void ask(q)} disabled={busy}
                  className="rounded-full border border-line px-3 py-1 text-left text-xs hover:border-fg/40 disabled:opacity-50">
                  {q}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {turns.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
            {m.role === "user" ? (
              <p className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-surface-2 px-3 py-2">{m.content}</p>
            ) : (
              <div className="max-w-[95%]">
                <Answer text={m.content} />
                {m.grounded?.length ? <p className="mt-1 text-[11px] text-muted">Based on: {m.grounded.join(" · ")}</p> : null}
              </div>
            )}
          </div>
        ))}
        {busy ? <p className="text-xs text-muted">Thinking…</p> : null}
        <div ref={end} />
      </div>

      {lastFollowUps.length ? (
        <ul aria-label="Follow-up questions" className="mt-3 flex flex-wrap gap-2">
          {lastFollowUps.map((q) => (
            <li key={q}>
              <button type="button" onClick={() => void ask(q)} className="rounded-full border border-line px-3 py-1 text-left text-xs hover:border-fg/40">
                {q}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <p role="alert" className="mt-3 text-sm text-bad">{error}</p> : null}

      <form className="mt-3 flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); void ask(draft); }}>
        <label className="min-w-0 flex-1">
          <span className="sr-only">Your question</span>
          <textarea
            ref={box}
            value={draft}
            maxLength={CHAT_MAX_CHARS}
            rows={2}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void ask(draft); }
            }}
            placeholder="Ask about this run or the physics behind it"
            className="w-full resize-y rounded-lg border border-line bg-bg px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          />
        </label>
        <button type="submit" disabled={busy || !draft.trim()} className="h-10 shrink-0 rounded-lg bg-flash px-4 text-sm font-bold text-flash-fg disabled:opacity-50">
          Ask
        </button>
      </form>
    </section>
  );
}
