"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import AttachmentPicker from "@/components/AttachmentPicker";
import Icon from "@/components/Icon";
import { FidelityBadge } from "@/components/Panels";
import StrobeThumb from "@/components/StrobeThumb";
import { createJob, fetchJson, listDocuments, uploadInput } from "@/lib/api";
import type { CreateJobBody, DemoIndexEntry, Fidelity, LibraryDocument } from "@/lib/types";

const FIDELITIES: Fidelity[] = ["analytic", "validated_numerical", "heuristic"];

function parseDemos(raw: unknown): DemoIndexEntry[] {
  const list = Array.isArray(raw) ? raw : raw && typeof raw === "object" && Array.isArray((raw as { demos?: unknown }).demos) ? (raw as { demos: unknown[] }).demos : [];
  return list.flatMap((d: unknown) => {
    if (!d || typeof d !== "object") return [];
    const o = d as Record<string, unknown>;
    if (typeof o.slug !== "string") return [];
    const fidelity = FIDELITIES.find((f) => f === o.fidelity);
    return [{
      slug: o.slug,
      title: typeof o.title === "string" ? o.title : o.slug,
      description: typeof o.description === "string" ? o.description : typeof o.prompt === "string" ? o.prompt : "",
      fidelity,
    }];
  });
}

const EXAMPLES = [
  "A ball rolls down a 3 m ramp at 20°, then across the floor.",
  "How fast does a pizza bake on the surface of Venus?",
  "A double pendulum released from rest at 90° and 120°.",
];

export default function Home() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [demos, setDemos] = useState<DemoIndexEntry[]>([]);
  const [ready, setReady] = useState<LibraryDocument[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [clarify, setClarify] = useState(true);
  const [attachments, setAttachments] = useState<File[]>([]);
  const [progress, setProgress] = useState<string | null>(null);
  const canSubmit = prompt.trim().length > 0 || attachments.length > 0;
  const scenario = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    fetchJson("/demo/index.json").then((r) => setDemos(parseDemos(r))).catch(() => setDemos([]));
    listDocuments().then((d) => setReady(d.filter((x) => x.status === "ready"))).catch(() => setReady([]));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!canSubmit) {
      setError("Describe a situation first, or attach a sketch or voice note.");
      scenario.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    const body: CreateJobBody = { prompt: prompt.trim(), clarify };
    if (selected.length > 0) body.document_ids = selected;
    try {
      if (attachments.length > 0) {
        const ids: string[] = [];
        for (const [i, f] of attachments.entries()) {
          setProgress(`Uploading ${i + 1} of ${attachments.length}…`);
          ids.push((await uploadInput(f)).input_id);
        }
        body.input_ids = ids;
      }
      setProgress(null);
      const { job_id } = await createJob(body);
      router.push(`/jobs/${job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the simulation");
      setProgress(null);
      setBusy(false);
    }
  }

  const pill = "inline-flex h-9 items-center gap-1.5 rounded-full border border-line bg-surface px-3.5 text-sm font-medium text-muted transition-colors hover:border-fg/40 hover:text-fg";
  return (
    <div className="space-y-16 pb-8">
      <section className="pt-4 sm:pt-10">
        <h1 className="max-w-4xl text-[2.6rem] font-extrabold leading-[0.95] tracking-display sm:text-[4.25rem]">
          Any motion, <span className="text-accent">frozen</span> and checked.
        </h1>
        <p className="mt-5 max-w-2xl text-lg text-muted">
          Describe a physical situation in plain words. Omnific researches it, solves it with real code, tests the result
          against physics and hands you a 3D simulation you can play, take apart and trust.
        </p>

        <form onSubmit={submit} className="mt-8 max-w-4xl space-y-4">
          <div className="rounded-2xl border border-line bg-surface p-2 shadow-[0_18px_50px_-30px_rgb(0_0_0/0.6)] focus-within:border-accent/70">
            <label htmlFor="scenario" className="sr-only">Scenario</label>
            <textarea
              id="scenario"
              ref={scenario}
              aria-describedby={error ? "run-error" : undefined}
              className="block w-full resize-none bg-transparent px-3 pb-2 pt-3 text-lg leading-snug outline-none placeholder:text-muted/80"
              rows={3}
              value={prompt}
              onChange={(e) => { setPrompt(e.target.value); if (error) setError(null); }}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.currentTarget.form?.requestSubmit(); }}
              placeholder={EXAMPLES[0]}
            />
            <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-1">
              <div className="flex flex-wrap gap-1.5" aria-label="Example prompts">
                {EXAMPLES.slice(1).map((ex) => (
                  <button key={ex} type="button" onClick={() => setPrompt(ex)} className="rounded-full px-2.5 py-1 text-left text-xs text-muted hover:bg-surface-2 hover:text-fg">
                    {ex}
                  </button>
                ))}
              </div>
              <button
                type="submit"
                disabled={busy}
                className="inline-flex h-11 items-center gap-2 rounded-xl bg-flash px-5 text-sm font-bold text-flash-fg transition-[filter,transform] hover:brightness-105 active:scale-[0.98] disabled:bg-surface-2 disabled:text-muted"
              >
                {busy ? (progress ?? "Starting…") : "Run simulation"}
                {!busy ? <Icon name="arrowRight" size={16} /> : null}
              </button>
            </div>
          </div>

          <div className="flex flex-wrap items-start gap-2">
            <AttachmentPicker files={attachments} setFiles={setAttachments} disabled={busy} />
            <details className="group relative">
              <summary className={`${pill} cursor-pointer list-none`}>
                <Icon name="file" size={15} />
                Documents{selected.length > 0 ? <span className="tabular font-semibold text-accent">{selected.length}</span> : null}
              </summary>
              <div className="absolute left-0 top-11 z-10 w-80 rounded-xl border border-line bg-surface p-3 shadow-[0_18px_40px_-20px_rgb(0_0_0/0.6)]">
                {ready.length === 0 ? (
                  <p className="text-sm text-muted">No documents ready yet. <Link href="/library" className="text-accent underline">Upload some</Link> and the agents can cite them.</p>
                ) : (
                  <>
                    <ul className="max-h-48 space-y-1 overflow-y-auto">
                      {ready.map((d) => (
                        <li key={d.id}>
                          <label className="flex items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-surface-2">
                            <input
                              type="checkbox"
                              className="accent-[rgb(var(--accent))]"
                              checked={selected.includes(d.id)}
                              onChange={(e) => setSelected((s) => (e.target.checked ? [...s, d.id] : s.filter((x) => x !== d.id)))}
                            />
                            <span className="truncate">{d.title || d.filename}</span>
                          </label>
                        </li>
                      ))}
                    </ul>
                    <Link href="/library" className="mt-2 inline-block text-xs text-accent underline">Manage documents</Link>
                  </>
                )}
              </div>
            </details>
            <label className={`${pill} cursor-pointer`}>
              <input type="checkbox" className="peer sr-only" checked={clarify} onChange={(e) => setClarify(e.target.checked)} />
              <span aria-hidden className="relative h-4 w-7 rounded-full bg-line transition-colors peer-checked:bg-accent after:absolute after:left-0.5 after:top-0.5 after:h-3 after:w-3 after:rounded-full after:bg-surface after:transition-transform peer-checked:after:translate-x-3" />
              Ask me clarifying questions
            </label>
          </div>
          {error ? <p id="run-error" role="alert" className="text-sm text-bad">{error}</p> : null}
        </form>
      </section>

      {demos.length > 0 ? (
        <section aria-labelledby="demos">
          <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="demos" className="text-xl font-bold tracking-tight">Ready to explore</h2>
            <Link href="/explore" className="text-sm text-accent underline">See what others published</Link>
          </div>
          {/* one exposure leads, the rest sit beside it as a contact strip */}
          <ul className="grid gap-px overflow-hidden rounded-2xl border border-line bg-line lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:grid-rows-3">
            {demos.map((d, i) => (
              <li key={d.slug} className={`bg-surface ${i === 0 ? "lg:row-span-3" : ""}`}>
                <Link href={`/demo/${d.slug}`} className={`group h-full ${i === 0 ? "flex flex-col" : "grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] items-stretch"}`}>
                  <div className={i === 0 ? "aspect-[16/9] flex-1 overflow-hidden lg:aspect-auto" : "aspect-[16/9] min-w-0 overflow-hidden lg:aspect-auto lg:h-full"}>
                    <StrobeThumb playbackUrl={`/demo/${encodeURIComponent(d.slug)}/playback.json`} className="transition-transform duration-500 group-hover:scale-[1.04]" />
                  </div>
                  <div className={i === 0 ? "flex items-start justify-between gap-3 p-5" : "relative flex min-w-0 flex-col items-start justify-center gap-2 bg-surface p-4"}>
                    <span className={`font-semibold leading-snug group-hover:text-accent ${i === 0 ? "text-xl" : ""}`}>{d.title}</span>
                    {d.fidelity ? <FidelityBadge fidelity={d.fidelity} /> : null}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
