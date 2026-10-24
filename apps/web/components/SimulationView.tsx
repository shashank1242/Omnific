"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import { fetchJson, fetchText } from "@/lib/api";
import { parsePlayback } from "@/lib/playback";
import type { JobOutcome, Playback } from "@/lib/types";
import Charts from "./Charts";
import { CodePanel, EvalPanel, FidelityBadge, ReviewPanel, TrustPanel } from "./Panels";
import { Player } from "./player";
import Transport from "./Transport";

const Viewport = dynamic(() => import("./Viewport"), {
  ssr: false,
  loading: () => <div className="grid h-full place-items-center text-sm text-muted">Loading 3D viewport…</div>,
});

type TabId = "charts" | "trust" | "eval" | "code" | "review";

export interface SimulationViewProps {
  outcome: JobOutcome | null;
  playbackUrl: string | null;
  sceneUrl: string | null;
  solverUrl: string | null;
  /** Changes whenever the artifacts behind the URLs change, forcing a reload. */
  assetKey: string;
  jobId: string;
  demo?: boolean;
  onReviewed?: () => void;
}

function usePlayback(url: string | null, key: string) {
  const [state, setState] = useState<{ pb: Playback | null; error: string | null; key: string }>({ pb: null, error: null, key: "" });
  useEffect(() => {
    if (!url) return;
    let live = true;
    fetchJson(url)
      .then((raw) => parsePlayback(raw))
      .then((pb) => live && setState({ pb, error: null, key }))
      .catch((e: unknown) => live && setState({ pb: null, error: e instanceof Error ? e.message : String(e), key }));
    return () => { live = false; };
  }, [url, key]);
  return url ? state : { pb: null, error: null, key: "" };
}

function useSolver(url: string | null, key: string) {
  const [state, setState] = useState<{ code: string | null; missing: boolean }>({ code: null, missing: false });
  useEffect(() => {
    if (!url) return;
    let live = true;
    fetchText(url)
      .then((code) => live && setState({ code, missing: code === null }))
      .catch(() => live && setState({ code: null, missing: true }));
    return () => { live = false; };
  }, [url, key]);
  return url ? state : { code: null, missing: true };
}

export default function SimulationView(props: SimulationViewProps) {
  const { outcome, playbackUrl, sceneUrl, solverUrl, assetKey, jobId, demo = false } = props;
  const { pb, error } = usePlayback(playbackUrl, assetKey);
  const solver = useSolver(solverUrl, assetKey);
  const [tab, setTab] = useState<TabId>("charts");

  const player = useMemo(() => (pb ? new Player(pb.t[0] ?? 0, pb.t[pb.t.length - 1] ?? 0) : null), [pb]);
  useEffect(() => () => player?.dispose(), [player]);

  const spec = outcome?.spec ?? null;
  const showReview = demo || outcome?.status === "awaiting_review";
  const tabs: { id: TabId; label: string; show: boolean }[] = [
    { id: "charts", label: "Charts", show: pb !== null },
    { id: "trust", label: "Trust", show: true },
    { id: "eval", label: "Evaluation", show: true },
    { id: "code", label: "Code", show: !(demo && solver.missing) && solverUrl !== null },
    { id: "review", label: "Review", show: showReview && outcome !== null },
  ];
  const visible = tabs.filter((t) => t.show);
  const active = visible.some((t) => t.id === tab) ? tab : (visible[0]?.id ?? "trust");

  const title = spec?.title ?? pb?.title ?? "Simulation";
  const fidelity = spec?.fidelity ?? pb?.fidelity ?? "heuristic";

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
      <div className="space-y-3 lg:sticky lg:top-4 lg:self-start">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold">{title}</h2>
          <FidelityBadge fidelity={fidelity} />
        </div>
        {pb && player ? (
          <>
            <div className="h-[48vh] min-h-[16rem] lg:h-[60vh]">
              <Viewport sceneUrl={sceneUrl} playback={pb} player={player} />
            </div>
            <Transport player={player} unit={pb.unit_t} />
          </>
        ) : (
          <div className="grid h-64 place-items-center rounded-lg border border-line bg-surface p-4 text-center text-sm text-muted">
            {error ? `Could not load playback data: ${error}` : playbackUrl ? "Loading playback…" : "No playback data was produced for this run."}
          </div>
        )}
      </div>

      <div className="min-w-0">
        <div role="tablist" aria-label="Simulation details" className="mb-3 flex gap-1 overflow-x-auto border-b border-line">
          {visible.map((t) => (
            <button
              key={t.id}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={active === t.id}
              aria-controls={`panel-${t.id}`}
              tabIndex={active === t.id ? 0 : -1}
              onClick={() => setTab(t.id)}
              onKeyDown={(e) => {
                const i = visible.findIndex((x) => x.id === t.id);
                const next = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : null;
                if (next === null) return;
                const target = visible[(next + visible.length) % visible.length];
                if (target) { setTab(target.id); document.getElementById(`tab-${target.id}`)?.focus(); }
              }}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${
                active === t.id ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={`panel-${active}`} aria-labelledby={`tab-${active}`}>
          {active === "charts" && pb && player ? <Charts playback={pb} player={player} /> : null}
          {active === "trust" ? <TrustPanel spec={spec} research={outcome?.research ?? null} fidelity={fidelity} /> : null}
          {active === "eval" ? <EvalPanel report={outcome?.eval_report ?? null} failure={outcome?.failure_reason ?? ""} /> : null}
          {active === "code" ? <CodePanel code={solver.code} /> : null}
          {active === "review" && outcome ? (
            <ReviewPanel jobId={jobId} localOnly={demo} outcome={outcome} onSubmitted={props.onReviewed} />
          ) : null}
        </div>
      </div>
    </div>
  );
}
