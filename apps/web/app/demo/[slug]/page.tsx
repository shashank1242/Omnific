"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import SimulationView from "@/components/SimulationView";
import { fetchJson } from "@/lib/api";
import type { JobOutcome } from "@/lib/types";

export default function DemoPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const base = `/demo/${encodeURIComponent(slug)}`;
  const [outcome, setOutcome] = useState<JobOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchJson(`${base}/outcome.json`)
      .then((o) => live && setOutcome(o as JobOutcome))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => { live = false; };
  }, [base]);

  return (
    <div className="space-y-4">
      <p className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted">
        <span>A bundled demo: pre-computed and shipped with the app, so nothing you do here is sent anywhere.</span>
        <Link href="/" className="text-accent underline">All demos</Link>
      </p>
      {error ? (
        <p role="alert" className="text-sm text-bad">This demo could not be loaded: {error}</p>
      ) : (
        <SimulationView
          outcome={outcome}
          playbackUrl={`${base}/playback.json`}
          sceneUrl={`${base}/scene.gltf`}
          // demos are pre-computed: only ask for a solver file when the demo ships one (no 404 per visit)
          solverUrl={outcome?.code_key ? `${base}/solver.py` : null}
          assetKey={slug}
          jobId={outcome?.job_id ?? slug}
          demo
        />
      )}
    </div>
  );
}
