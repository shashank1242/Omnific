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
      <Link href="/" className="text-sm text-accent underline underline-offset-2">← All demos</Link>
      <p className="rounded-md border border-line bg-surface px-3 py-2 text-sm text-muted">
        Demo bundle: pre-computed results shipped with the app, no backend involved.
      </p>
      {error ? (
        <p role="alert" className="text-sm text-bad">This demo could not be loaded: {error}</p>
      ) : (
        <SimulationView
          outcome={outcome}
          playbackUrl={`${base}/playback.json`}
          sceneUrl={`${base}/scene.gltf`}
          solverUrl={`${base}/solver.py`}
          assetKey={slug}
          jobId={outcome?.job_id ?? slug}
          demo
        />
      )}
    </div>
  );
}
