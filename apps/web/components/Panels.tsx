"use client";

import { useState } from "react";
import { ApiError, submitReview } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import type { Blame, EvalReport, Fidelity, JobOutcome, ResearchOutput, ReviewDecision, SimSpec } from "@/lib/types";

const card = "rounded-lg border border-line bg-surface p-4";
const h3 = "mb-2 text-sm font-semibold uppercase tracking-wide text-muted";

const FIDELITY: Record<Fidelity, { label: string; tone: string; text: string }> = {
  analytic: {
    label: "Analytic",
    tone: "border-ok text-ok",
    text: "The result follows from a closed-form solution of the governing equations.",
  },
  validated_numerical: {
    label: "Validated numerical",
    tone: "border-accent text-accent",
    text: "Numerical solution checked against invariants, bounds, analytic references or a convergence test.",
  },
  heuristic: {
    label: "Heuristic",
    tone: "border-warn text-warn",
    text: "A plausible, simplified model without a rigorous reference. Treat numbers as illustrative, not predictive.",
  },
};

export function FidelityBadge({ fidelity }: { fidelity: Fidelity }) {
  const f = FIDELITY[fidelity];
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${f.tone}`} title={f.text}>
      {f.label}
    </span>
  );
}

function SafeLink({ url, children }: { url: string; children: React.ReactNode }) {
  if (!/^https?:\/\//i.test(url)) return <>{children}</>;
  return (
    <a href={url} target="_blank" rel="noreferrer noopener" className="text-accent underline underline-offset-2">
      {children}
    </a>
  );
}

export function TrustPanel({ spec, research, fidelity }: { spec: SimSpec | null; research: ResearchOutput | null; fidelity: Fidelity }) {
  return (
    <div className="space-y-4">
      <section className={card}>
        <h3 className={h3}>Fidelity</h3>
        <FidelityBadge fidelity={fidelity} />
        <p className="mt-2 text-sm">{FIDELITY[fidelity].text}</p>
      </section>
      {research ? (
        <section className={card}>
          <h3 className={h3}>Research summary</h3>
          <p className="text-sm leading-relaxed">{research.summary}</p>
          {research.phenomena.length > 0 ? (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {research.phenomena.map((p) => (
                <li key={p} className="rounded-full bg-line/50 px-2 py-0.5 text-xs">{p}</li>
              ))}
            </ul>
          ) : null}
          {research.open_questions.length > 0 ? (
            <>
              <h4 className="mt-3 text-xs font-semibold text-muted">Open questions</h4>
              <ul className="list-disc pl-5 text-sm">{research.open_questions.map((q) => <li key={q}>{q}</li>)}</ul>
            </>
          ) : null}
        </section>
      ) : null}
      {spec ? (
        <>
          <section className={card}>
            <h3 className={h3}>Assumptions</h3>
            {spec.assumptions.length ? (
              <ul className="list-disc space-y-1 pl-5 text-sm">{spec.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            ) : <p className="text-sm text-muted">None stated.</p>}
          </section>
          <section className={card}>
            <h3 className={h3}>Parameters</h3>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[28rem] text-left text-sm">
                <thead className="text-xs text-muted">
                  <tr><th className="py-1 pr-3 font-medium">Name</th><th className="pr-3 font-medium">Value</th><th className="pr-3 font-medium">Unit</th><th className="font-medium">Source</th></tr>
                </thead>
                <tbody>
                  {spec.parameters.map((p) => (
                    <tr key={p.name} className="border-t border-line align-top">
                      <td className="py-1.5 pr-3"><span className="font-mono">{p.name}</span>{p.description ? <div className="text-xs text-muted">{p.description}</div> : null}</td>
                      <td className="pr-3 tabular-nums">{fmtNum(p.value)}</td>
                      <td className="pr-3">{p.unit || "—"}</td>
                      <td>{p.source ? <SafeLink url={p.source.url}>{p.source.title}</SafeLink> : <span className="text-muted">assumed</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className={card}>
            <h3 className={h3}>Equations</h3>
            <ul className="space-y-2">
              {spec.equations.map((e) => (
                <li key={e.label}>
                  <div className="text-sm font-medium">{e.label}</div>
                  <code className="block overflow-x-auto rounded bg-line/40 px-2 py-1 font-mono text-sm">{e.expression}</code>
                  {e.description ? <p className="text-xs text-muted">{e.description}</p> : null}
                </li>
              ))}
            </ul>
          </section>
          <Citations citations={spec.citations} />
        </>
      ) : null}
      {research && research.facts.length > 0 ? (
        <section className={card}>
          <h3 className={h3}>Researched facts</h3>
          <ul className="space-y-2 text-sm">
            {research.facts.map((f) => (
              <li key={f.name}>
                <span className="font-mono">{f.name}</span> = {fmtNum(f.value)} {f.unit} — {f.description}{" "}
                <SafeLink url={f.citation.url}>[{f.citation.title}]</SafeLink>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function Citations({ citations }: { citations: SimSpec["citations"] }) {
  if (citations.length === 0) return null;
  return (
    <section className={card}>
      <h3 className={h3}>Citations</h3>
      <ol className="list-decimal space-y-1 pl-5 text-sm">
        {citations.map((c) => (
          <li key={c.url + c.title}>
            <SafeLink url={c.url}>{c.title}</SafeLink>
            {c.note ? <span className="text-muted"> — {c.note}</span> : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

export function EvalPanel({ report, failure }: { report: EvalReport | null; failure: string }) {
  if (!report) return <p className="text-sm text-muted">{failure || "No evaluation report yet."}</p>;
  return (
    <div className="space-y-4">
      <section className={card}>
        <h3 className={h3}>Overall</h3>
        <p className={`text-sm font-semibold ${report.passed ? "text-ok" : "text-bad"}`}>
          {report.passed ? "All checks passed" : "Some checks failed"}
        </p>
        {report.critic_summary ? <p className="mt-2 text-sm leading-relaxed">{report.critic_summary}</p> : null}
        {failure ? <p className="mt-2 text-sm text-bad">{failure}</p> : null}
      </section>
      <ul className="space-y-2">
        {report.checks.map((c) => (
          <li key={c.name} className={`${card} flex gap-3`}>
            <span
              className={`mt-0.5 inline-flex h-5 shrink-0 items-center rounded px-1.5 text-xs font-bold ${c.passed ? "bg-ok/15 text-ok" : "bg-bad/15 text-bad"}`}
            >
              {c.passed ? "PASS" : "FAIL"}
            </span>
            <div className="min-w-0">
              <div className="text-sm font-medium">{c.name}</div>
              <p className="break-words text-sm text-muted">{c.detail}</p>
              {!c.passed && c.blame ? (
                <p className="mt-1 text-xs">Blame: <span className="font-mono">{c.blame}</span></p>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CodePanel({ code }: { code: string | null }) {
  if (code === null) return <p className="text-sm text-muted">Loading solver…</p>;
  return (
    <pre tabIndex={0} aria-label="solver.py" className="max-h-[70vh] overflow-auto rounded-lg border border-line bg-line/30 p-3 font-mono text-xs leading-relaxed">
      <code>{code}</code>
    </pre>
  );
}

export function ReviewPanel({
  jobId, localOnly, outcome, onSubmitted,
}: { jobId: string; localOnly: boolean; outcome: JobOutcome; onSubmitted?: () => void }) {
  const [reviewer, setReviewer] = useState("");
  const [notes, setNotes] = useState("");
  const [blame, setBlame] = useState<Blame>("modeler");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<ReviewDecision | null>(null);

  async function decide(approved: boolean) {
    if (!reviewer.trim()) { setError("Enter your name as reviewer."); return; }
    const d: ReviewDecision = { approved, reviewer: reviewer.trim(), notes, blame: approved ? null : blame };
    setError(null);
    if (localOnly) { setDone(d); return; }
    setBusy(true);
    try {
      await submitReview(jobId, d);
      setDone(d);
      onSubmitted?.();
    } catch (e) {
      setError(e instanceof ApiError && e.status === 409
        ? "This job is no longer awaiting review (another reviewer may have decided, or it was already processed)."
        : e instanceof Error ? e.message : "Review failed");
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm";
  return (
    <div className="space-y-4">
      {localOnly ? (
        <p className="rounded-lg border border-warn/50 bg-warn/10 p-3 text-sm">
          This is a bundled demo. Your decision is shown below but is not sent anywhere.
        </p>
      ) : null}
      {outcome.reviews.length > 0 ? (
        <section className={card}>
          <h3 className={h3}>Previous rounds</h3>
          <ul className="space-y-1 text-sm">
            {outcome.reviews.map((r, i) => (
              <li key={i}>
                <strong>{r.reviewer}</strong> {r.approved ? "approved" : `rejected (redo ${r.blame ?? "modeler"})`}
                {r.notes ? <span className="text-muted"> — {r.notes}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {done ? (
        <p role="status" className="rounded-lg border border-line bg-surface p-3 text-sm">
          Decision recorded: <strong>{done.approved ? "Approved" : "Rejected"}</strong> by {done.reviewer}
          {done.notes ? ` — “${done.notes}”` : ""}.
          {!done.approved ? ` Redo: ${done.blame}.` : ""}
          {localOnly ? " (local only)" : " The job status will update shortly."}
        </p>
      ) : (
        <form className={`${card} space-y-3`} onSubmit={(e) => e.preventDefault()}>
          <label className="block text-sm font-medium">
            Reviewer name
            <input className={field} value={reviewer} onChange={(e) => setReviewer(e.target.value)} autoComplete="name" required />
          </label>
          <label className="block text-sm font-medium">
            Notes
            <textarea className={field} rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
          <label className="block text-sm font-medium">
            If rejected, redo
            <select className={field} value={blame} onChange={(e) => setBlame(e.target.value as Blame)}>
              <option value="modeler">Modeler (the physical model)</option>
              <option value="solver_coder">Solver coder (the numerical code)</option>
            </select>
          </label>
          {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
          <div className="flex gap-2">
            <button type="button" disabled={busy} onClick={() => decide(true)} className="h-10 rounded-md bg-accent px-4 text-sm font-semibold text-accent-fg disabled:opacity-50">
              Approve
            </button>
            <button type="button" disabled={busy} onClick={() => decide(false)} className="h-10 rounded-md border border-bad px-4 text-sm font-semibold text-bad disabled:opacity-50">
              Reject
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
