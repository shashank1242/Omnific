"use client";

import { use, useCallback, useEffect, useState } from "react";
import BuildProgress from "@/components/BuildProgress";
import ClarifyForm from "@/components/ClarifyForm";
import SimulationView from "@/components/SimulationView";
import { artifactUrl, getJob } from "@/lib/api";
import type { JobView, Stage } from "@/lib/types";

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
  const ended = job !== null && job.state !== "running" && job.state !== "completed";
  const failed = ended || outcome?.status === "failed" || stage === "failed";
  const title = outcome?.spec?.title ?? "Your simulation";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        {/* once there is a simulation, the viewer shows its title; keep the heading for screen readers */}
        <h1 className={outcome?.spec ? "sr-only" : "text-xl font-semibold"}>{title}</h1>
      </div>
      {error && !job ? <p role="alert" className="text-sm text-bad">Could not load this simulation: {error}</p> : null}
      {job && stage !== "approved" ? (
        <BuildProgress stage={stage} revision={job.status?.revision ?? 0} failed={failed} />
      ) : !job && !error ? <p className="text-sm text-muted">Loading…</p> : null}
      {stage === "awaiting_clarification" && job?.questions && job.questions.length > 0 ? (
        <ClarifyForm jobId={id} questions={job.questions} onDone={poll} />
      ) : null}
      {failed ? (
        <p role="alert" className="rounded-lg border border-bad/50 bg-bad/10 p-3 text-sm">
          {outcome?.failure_reason || "The build stopped before it finished."} You can start a new simulation with a reworded prompt.
        </p>
      ) : null}
      {outcome && (outcome.status !== "failed" || outcome.spec) ? (
        <SimulationView
          outcome={outcome}
          playbackUrl={outcome.playback_key ? artifactUrl(id, "playback.json") : null}
          sceneUrl={outcome.scene_key ? artifactUrl(id, "scene.gltf") : null}
          solverUrl={outcome.code_key ? artifactUrl(id, "solver.py") : null}
          assetKey={`${outcome.playback_key}|${outcome.scene_key}|${outcome.code_key}`}
          jobId={id}
          awaitingDecision={stage === "awaiting_review"}
          onReviewed={poll}
        />
      ) : null}
    </div>
  );
}
