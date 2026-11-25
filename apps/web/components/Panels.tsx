"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useState, type RefObject } from "react";
import { ApiError, getPublished, listDocuments, publishJob, stepUrl, submitReview, unpublishJob } from "@/lib/api";
import { docCitationLabel, parseDocCitation, parseInputCitation } from "@/lib/citations";
import { fmtNum } from "@/lib/format";
import type { Clarification, Fidelity, IntakeItem, JobOutcome, PublishedSimulation, ResearchOutput, ReviewDecision, SceneReport, SimSpec } from "@/lib/types";

const card = "rounded-xl border border-line bg-surface p-4";
const h3 = "mb-2 text-sm font-bold tracking-tight";

const FIDELITY: Record<Fidelity, { label: string; tone: string; text: string }> = {
  analytic: {
    label: "Analytic",
    tone: "border-ok/60 bg-ok/10 text-ok",
    text: "The result follows from a closed-form solution of the governing equations.",
  },
  validated_numerical: {
    label: "Validated numerical",
    tone: "border-data/60 bg-data/10 text-data",
    text: "Numerical solution checked against invariants, bounds, analytic references or a convergence test.",
  },
  heuristic: {
    label: "Heuristic",
    tone: "border-warn/60 bg-warn/10 text-warn",
    text: "A plausible, simplified model without a rigorous reference. Treat numbers as illustrative, not predictive.",
  },
};

export function FidelityBadge({ fidelity }: { fidelity: Fidelity }) {
  const f = FIDELITY[fidelity];
  return (
    <span className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-semibold ${f.tone}`} title={f.text}>
      {f.label}
    </span>
  );
}

const DocTitles = createContext<Record<string, string>>({});

function SafeLink({ url, children }: { url: string; children: React.ReactNode }) {
  const doc = parseDocCitation(url);
  const titles = useContext(DocTitles);
  if (doc) {
    return (
      <Link href={`/library?doc=${encodeURIComponent(doc.docId)}`} className="text-accent underline underline-offset-2">
        {docCitationLabel(titles[doc.docId] ?? "Document", doc.page)}
      </Link>
    );
  }
  const attachment = parseInputCitation(url);
  if (attachment === "prompt") return <span>From your prompt</span>;
  if (attachment === "clarifications") return <span>From your answers</span>;
  if (attachment) return <span title={attachment}>From your attachment <span className="font-mono">{attachment}</span></span>;
  if (!/^https?:\/\//i.test(url)) return <>{children}</>;
  return (
    <a href={url} target="_blank" rel="noreferrer noopener" className="text-accent underline underline-offset-2">
      {children}
    </a>
  );
}

export function TrustPanel({ spec, research, fidelity, clarifications = [], intake = [] }: { spec: SimSpec | null; research: ResearchOutput | null; fidelity: Fidelity; clarifications?: Clarification[]; intake?: IntakeItem[] }) {
  const [titles, setTitles] = useState<Record<string, string>>({});
  const usesDocs =
    (spec?.citations.some((c) => c.url.startsWith("omnific://")) ?? false) ||
    (research?.facts.some((f) => f.citation.url.startsWith("omnific://")) ?? false);
  useEffect(() => {
    if (!usesDocs) return;
    let live = true;
    listDocuments()
      .then((ds) => { if (live) setTitles(Object.fromEntries(ds.map((d) => [d.id, d.title || d.filename]))); })
      .catch(() => undefined);
    return () => { live = false; };
  }, [usesDocs]);
  return (
    <DocTitles.Provider value={titles}>
    <div className="space-y-4">
      {intake.length > 0 ? (
        <section className={card}>
          <h3 className={h3}>Read from your attachments</h3>
          <ul className="space-y-3 text-sm">
            {intake.map((it) => (
              <li key={it.input_id}>
                <div className="font-medium">
                  {it.kind === "image" ? "Sketch / photo" : "Voice note"} <span className="font-mono text-xs text-muted">{it.filename}</span>
                </div>
                <p className="mt-1 whitespace-pre-wrap leading-relaxed">{it.text}</p>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted">Where your typed prompt and an attachment disagree, the typed prompt wins. Values marked “estimated” were read off a picture.</p>
        </section>
      ) : null}
      {clarifications.length > 0 ? (
        <section className={card}>
          <h3 className={h3}>Your answers</h3>
          <ul className="space-y-1 text-sm">
            {clarifications.map((c) => (
              <li key={c.question}>
                {c.question}{" "}
                <strong>{c.answer}{c.unit ? ` ${c.unit}` : ""}</strong>
                {c.used_default ? <span className="ml-2 rounded-full border border-warn px-2 py-0.5 text-xs text-warn">default</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {spec?.environment ? (
        <section className={card}>
          <h3 className={h3}>Environment</h3>
          <p className="text-sm"><strong>{spec.environment.name}</strong>: {spec.environment.description}</p>
          {spec.environment.parameters.length > 0 ? (
            <ul className="mt-2 space-y-0.5 text-sm">
              {spec.environment.parameters.map((n) => {
                const p = spec.parameters.find((x) => x.name === n);
                return p ? (
                  <li key={n}><span className="font-mono">{n}</span> = <span className="tabular font-mono">{fmtNum(p.value)}</span> {p.unit}
                    {p.description ? <span className="text-muted">: {p.description}</span> : null}</li>
                ) : null;
              })}
            </ul>
          ) : null}
          <p className="mt-2 text-xs text-muted">These values come from the setting, and the model uses every one of them.</p>
        </section>
      ) : null}
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
                      <td className="tabular pr-3 font-mono text-[13px]">{fmtNum(p.value)}</td>
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
                <span className="font-mono">{f.name}</span> = <span className="tabular font-mono">{fmtNum(f.value)}</span> {f.unit} — {f.description}{" "}
                <SafeLink url={f.citation.url}>[{f.citation.title}]</SafeLink>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
    </DocTitles.Provider>
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

const FINAL_LABEL: Record<string, string> = {
  generated_rigged: "Generated and rigged", generated: "Generated", library_rigged: "Library model, rigged",
  library: "Library model", cad_rigged: "CAD model, jointed", cad: "CAD model", procedural: "Simple shape",
};

export function ScenePanel({ report, jobId }: { report: SceneReport; jobId: string | null }) {
  const credits = report.entities.filter((r) => r.attribution);
  return (
    <div className="space-y-4">
      <section className={card}>
        <h3 className={h3}>3D models</h3>
        <p className="text-sm text-muted">How each object in the scene was made.</p>
      </section>
      <ul className="space-y-2">
        {report.entities.map((r) => (
          <li key={r.entity} className={card}>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-medium">{r.entity}</span>
              <span className={`rounded-full border px-2 py-0.5 text-xs ${r.final === "procedural" ? "border-line text-muted" : "border-ok text-ok"}`}>
                {FINAL_LABEL[r.final] ?? r.final}
              </span>
            </div>
            {r.attribution ? (
              <p className="mt-1 text-sm">
                Model: <a className="underline" href={r.attribution.url} target="_blank" rel="noopener noreferrer">{r.attribution.title}</a>{" "}
                by {r.attribution.author || "unknown"} ({r.attribution.license}, via {r.attribution.source})
              </p>
            ) : null}
            {r.step_key && jobId ? (
              <p className="mt-1 text-sm">
                <a className="underline" href={stepUrl(jobId, r.entity)} download>Download CAD model (STEP)</a>
              </p>
            ) : null}
          </li>
        ))}
      </ul>
      {credits.length ? (
        <section className={card}>
          <h3 className={h3}>Attribution</h3>
          <ul className="list-disc pl-5 text-sm">
            {credits.map((r) => r.attribution ? (
              <li key={r.entity}>
                {r.entity}: &ldquo;{r.attribution.title}&rdquo; by {r.attribution.author || "unknown"}, {r.attribution.license},{" "}
                <a className="underline" href={r.attribution.url} target="_blank" rel="noopener noreferrer">{r.attribution.source}</a>
              </li>
            ) : null)}
          </ul>
        </section>
      ) : null}
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

// Sent as the review signal. A change request goes back to the modeler with the user's own words as feedback.
const CREATOR = "creator";

/** Accept the simulation, or describe what should change (another build round starts). */
export function DecisionPanel({
  jobId, localOnly, outcome, onSubmitted,
}: { jobId: string; localOnly: boolean; outcome: JobOutcome; onSubmitted?: () => void }) {
  const [changes, setChanges] = useState("");
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<ReviewDecision | null>(null);
  const earlier = outcome.reviews.filter((r) => !r.approved && r.notes);

  async function decide(approved: boolean) {
    if (!approved && !changes.trim()) { setError("Describe what you'd like changed."); return; }
    const d: ReviewDecision = { approved, reviewer: CREATOR, notes: approved ? "" : changes.trim(), blame: approved ? null : "modeler" };
    setError(null);
    if (localOnly) { setDone(d); return; }
    setBusy(true);
    try {
      await submitReview(jobId, d);
      setDone(d);
      onSubmitted?.();
    } catch (e) {
      const moved = e instanceof ApiError && e.status === 409;
      setError(moved ? "This simulation has already moved on. Refreshing…" : e instanceof Error ? e.message : "Could not send your decision");
      if (moved) onSubmitted?.();
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <p role="status" className="rounded-lg border border-line bg-surface p-3 text-sm">
        {done.approved ? "Accepted." : "Got it. Rebuilding with your changes…"}
        {localOnly ? " (This is a bundled demo, so nothing was sent.)" : ""}
      </p>
    );
  }
  return (
    <section className="rounded-xl border border-flash/60 bg-flash/[0.07] p-4" aria-label="Your decision">
      <h3 className="text-sm font-semibold">Happy with it?</h3>
      {earlier.length > 0 ? (
        <div className="mt-2 text-xs text-muted">
          Changes you asked for:
          <ul className="list-disc pl-5">{earlier.map((r, i) => <li key={i}>{r.notes}</li>)}</ul>
        </div>
      ) : null}
      {asking ? (
        <label className="mt-3 block text-sm">
          What should change?
          <textarea
            className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm"
            rows={3}
            value={changes}
            onChange={(e) => setChanges(e.target.value)}
            placeholder="e.g. Make the ramp steeper, around 30°, and add friction."
            autoFocus
          />
        </label>
      ) : null}
      {error ? <p role="alert" className="mt-2 text-sm text-bad">{error}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {asking ? (
          <>
            <button type="button" disabled={busy} onClick={() => decide(false)} className="h-10 rounded-md bg-flash px-4 text-sm font-bold text-flash-fg disabled:opacity-50">
              Rebuild with these changes
            </button>
            <button type="button" disabled={busy} onClick={() => { setAsking(false); setError(null); }} className="h-10 rounded-md border border-line px-4 text-sm">
              Cancel
            </button>
          </>
        ) : (
          <>
            <button type="button" disabled={busy} onClick={() => decide(true)} className="h-10 rounded-md bg-flash px-4 text-sm font-bold text-flash-fg disabled:opacity-50">
              Accept
            </button>
            <button type="button" disabled={busy} onClick={() => setAsking(true)} className="h-10 rounded-md border border-line px-4 text-sm font-semibold">
              Ask for changes
            </button>
          </>
        )}
      </div>
    </section>
  );
}

/** Publish an accepted simulation to the public library (opt-in), with a snapshot of the 3D view as its picture. */
export function PublishPanel({ jobId, outcome, capture }: { jobId: string; outcome: JobOutcome; capture: RefObject<(() => string | null) | null> }) {
  const [entry, setEntry] = useState<PublishedSimulation | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "unavailable" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [title, setTitle] = useState(outcome.spec?.title ?? "");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getPublished(jobId)
      .then((e) => {
        if (!live) return;
        setEntry(e);
        setTitle(e.title);
        setDescription(e.description);
        setState("ready");
      })
      .catch((e: unknown) => {
        if (!live) return;
        const status = e instanceof ApiError ? e.status : 0;
        setState(status === 404 ? "ready" : status === 503 ? "unavailable" : "error");
      });
    return () => { live = false; };
  }, [jobId, attempt]);

  if (state === "loading") return null;
  if (state === "unavailable") return <p className="text-xs text-muted">The public library isn&apos;t available on this server.</p>;
  if (state === "error") {
    return (
      <p role="alert" className="text-sm text-bad">
        Couldn&apos;t check whether this simulation is published.{" "}
        <button type="button" onClick={() => { setState("loading"); setAttempt((n) => n + 1); }} className="underline underline-offset-2">Try again</button>
      </p>
    );
  }

  async function run(action: () => Promise<void>, failure: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : failure);
    } finally {
      setBusy(false);
    }
  }

  const publish = () => run(async () => {
    let thumbnail: string | undefined;
    try { thumbnail = capture.current?.() ?? undefined; } catch { thumbnail = undefined; }
    setEntry(await publishJob(jobId, { title: title.trim(), description: description.trim(), thumbnail }));
  }, "Could not publish");
  const unpublish = () => run(async () => { await unpublishJob(jobId); setEntry(null); }, "Could not unpublish");

  const field = "mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm";
  return (
    <section className={`${card} space-y-3`} aria-label="Public library">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">{entry ? "Published in the public library" : "Share it in the public library"}</h3>
        {entry ? <Link href="/explore" className="text-xs text-accent underline underline-offset-2">Open the library</Link> : null}
      </div>
      <label className="block text-sm">
        Title
        <input className={field} value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="block text-sm">
        Description <span className="text-muted">(optional)</span>
        <textarea className={field} rows={2} maxLength={1000} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <p className="text-xs text-muted">Anyone with the site link can view published simulations. The current 3D view becomes its picture.</p>
      {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy} onClick={publish} className="h-10 rounded-md bg-flash px-4 text-sm font-bold text-flash-fg disabled:opacity-50">
          {entry ? "Update" : "Publish"}
        </button>
        {entry ? (
          <button type="button" disabled={busy} onClick={unpublish} className="h-10 rounded-md border border-line px-4 text-sm">
            Unpublish
          </button>
        ) : null}
      </div>
    </section>
  );
}
