"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { createJob, fetchJson } from "@/lib/api";
import type { CreateJobBody, DemoIndexEntry } from "@/lib/types";

function parseDemos(raw: unknown): DemoIndexEntry[] {
  const list = Array.isArray(raw) ? raw : raw && typeof raw === "object" && Array.isArray((raw as { demos?: unknown }).demos) ? (raw as { demos: unknown[] }).demos : [];
  return list.flatMap((d: unknown) => {
    if (!d || typeof d !== "object") return [];
    const o = d as Record<string, unknown>;
    if (typeof o.slug !== "string") return [];
    return [{
      slug: o.slug,
      title: typeof o.title === "string" ? o.title : o.slug,
      description: typeof o.description === "string" ? o.description : typeof o.prompt === "string" ? o.prompt : typeof o.fidelity === "string" ? `Fidelity: ${o.fidelity.replace("_", " ")}` : "",
    }];
  });
}

export default function Home() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [maxRevisions, setMaxRevisions] = useState(3);
  const [maxRounds, setMaxRounds] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [demos, setDemos] = useState<DemoIndexEntry[]>([]);

  useEffect(() => {
    fetchJson("/demo/index.json").then((r) => setDemos(parseDemos(r))).catch(() => setDemos([]));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) return;
    setBusy(true);
    setError(null);
    const body: CreateJobBody = { prompt: prompt.trim(), max_revisions: maxRevisions, max_review_rounds: maxRounds };
    try {
      const { job_id } = await createJob(body);
      router.push(`/jobs/${job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the job");
      setBusy(false);
    }
  }

  const field = "mt-1 w-24 rounded-md border border-line bg-bg px-3 py-2 text-sm";
  return (
    <div className="space-y-10">
      <section className="max-w-3xl">
        <h1 className="text-2xl font-semibold tracking-tight">Simulate a physical scenario</h1>
        <p className="mt-1 text-sm text-muted">
          Describe it in plain language. Omnific researches the physics, builds and runs a solver, checks the result, and
          shows you the assumptions behind it.
        </p>
        <form onSubmit={submit} className="mt-4 space-y-3">
          <label className="block text-sm font-medium">
            Scenario
            <textarea
              className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-base"
              rows={4}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="e.g. How fast does a pizza bake on the surface of Venus?"
              required
            />
          </label>
          <details className="rounded-md border border-line bg-surface px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium">Advanced</summary>
            <div className="mt-2 flex flex-wrap gap-4">
              <label className="text-sm">
                Max automatic revisions
                <input type="number" min={0} max={10} className={field} value={maxRevisions} onChange={(e) => setMaxRevisions(Number(e.target.value))} />
              </label>
              <label className="text-sm">
                Max review rounds
                <input type="number" min={1} max={10} className={field} value={maxRounds} onChange={(e) => setMaxRounds(Number(e.target.value))} />
              </label>
            </div>
          </details>
          {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
          <button type="submit" disabled={busy || !prompt.trim()} className="h-10 rounded-md bg-accent px-5 text-sm font-semibold text-accent-fg disabled:opacity-50">
            {busy ? "Starting…" : "Run simulation"}
          </button>
        </form>
      </section>

      {demos.length > 0 ? (
        <section>
          <h2 className="mb-3 text-lg font-semibold">Demo simulations</h2>
          <p className="mb-3 text-sm text-muted">Pre-computed examples that need no backend.</p>
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {demos.map((d) => (
              <li key={d.slug}>
                <Link href={`/demo/${d.slug}`} className="block h-full rounded-lg border border-line bg-surface p-4 hover:border-accent">
                  <div className="font-medium">{d.title}</div>
                  {d.description ? <p className="mt-1 line-clamp-3 text-sm text-muted">{d.description}</p> : null}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
