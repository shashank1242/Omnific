"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import SimulationView from "@/components/SimulationView";
import { artifactUrl, getJob } from "@/lib/api";
import type { JobView, Stage } from "@/lib/types";

const STAGES: { id: Stage; label: string }[] = [
  { id: "researching", label: "Research" },
  { id: "modeling", label: "Model" },
  { id: "coding", label: "Code" },
  { id: "running", label: "Run" },
  { id: "evaluating", label: "Evaluate" },
  { id: "packaging", label: "Package" },
  { id: "awaiting_review", label: "Review" },
];

function Timeline({ stage, revision, message, failed }: { stage: Stage | null; revision: number; message: string; failed: boolean }) {
  const idx = stage === "approved" ? STAGES.length : stage === "failed" ? -1 : STAGES.findIndex((s) => s.id === stage);
  const terminal: { id: string; label: string; tone: string } =
    stage === "failed" || failed ? { id: "failed", label: "Failed", tone: "bg-bad text-white" } : { id: "approved", label: "Approved", tone: "bg-ok text-white" };
  return (
    <section aria-label="Job progress" className="rounded-lg border border-line bg-surface p-4">
      <ol className="flex flex-wrap gap-2">
        {STAGES.map((s, i) => {
          const state = idx < 0 ? "todo" : i < idx ? "done" : i === idx ? "current" : "todo";
          return (
            <li
              key={s.id}
              aria-current={state === "current" ? "step" : undefined}
              className={`rounded-full border px-3 py-1 text-xs font-medium ${
                state === "done" ? "border-ok text-ok" : state === "current" ? "border-accent bg-accent text-accent-fg" : "border-line text-muted"
              }`}
            >
              {state === "done" ? "✓ " : ""}{s.label}
            </li>
          );
        })}
        <li className={`rounded-full px-3 py-1 text-xs font-semibold ${stage === "approved" || stage === "failed" || failed ? terminal.tone : "border border-line text-muted"}`}>
          {stage === "approved" || stage === "failed" || failed ? terminal.label : "Approved / Failed"}
        </li>
      </ol>
      <p className="mt-3 text-sm" aria-live="polite">
        <span className="text-muted">Revisions: </span><strong>{revision}</strong>
        {message ? <span className="text-muted"> — {message}</span> : null}
      </p>
    </section>
  );
}

export default function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [job, setJob] = useState<JobView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const poll = useCallback(() => setTick((n) => n + 1), []);

  const terminal = job !== null && job.state !== "running";
  useEffect(() => {
    if (terminal) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      try {
        const j = await getJob(id);
        if (!live) return;
        setJob(j);
        setError(null);
        if (j.state !== "running") return;
      } catch (e) {
        if (!live) return;
        setError(e instanceof Error ? e.message : String(e));
      }
      timer = setTimeout(run, 2000);
    };
    void run();
    return () => { live = false; clearTimeout(timer); };
  }, [id, terminal, tick]);

  const outcome = job?.result ?? job?.draft ?? null;
  const stage: Stage | null = job?.status?.stage ?? outcome?.status ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold">Job <span className="font-mono text-base">{id.slice(0, 8)}</span></h1>
        <Link href="/" className="text-sm text-accent underline underline-offset-2">New simulation</Link>
      </div>
      {error && !job ? <p role="alert" className="text-sm text-bad">Could not load job: {error}</p> : null}
      {job ? (
        <Timeline
          stage={stage}
          revision={job.status?.revision ?? outcome?.revisions ?? 0}
          message={job.status?.message ?? ""}
          failed={job.state !== "running" && job.state !== "completed"}
        />
      ) : !error ? <p className="text-sm text-muted">Loading…</p> : null}
      {job && job.state !== "running" && job.state !== "completed" ? (
        <p role="alert" className="text-sm text-bad">The workflow ended as “{job.state}”.</p>
      ) : null}
      {outcome?.status === "failed" ? (
        <p role="alert" className="rounded-lg border border-bad/50 bg-bad/10 p-3 text-sm">
          {outcome.failure_reason || "The job failed."}
        </p>
      ) : null}
      {outcome ? (
        <SimulationView
          outcome={outcome}
          playbackUrl={outcome.playback_key ? artifactUrl(id, "playback.json") : null}
          sceneUrl={outcome.scene_key ? artifactUrl(id, "scene.gltf") : null}
          solverUrl={outcome.code_key ? artifactUrl(id, "solver.py") : null}
          assetKey={`${outcome.playback_key}|${outcome.scene_key}|${outcome.code_key}`}
          jobId={id}
          onReviewed={poll}
        />
      ) : job && job.state === "running" ? (
        <p className="text-sm text-muted">The simulation appears here once it is ready for review.</p>
      ) : null}
    </div>
  );
}
