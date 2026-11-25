"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { artifactUrl, fetchJson, fetchText, listVariants, variantPlaybackUrl } from "@/lib/api";
import { describeChanges } from "@/lib/compare";
import { fmtNum } from "@/lib/format";
import { parsePlayback } from "@/lib/playback";
import type { JobOutcome, Playback, VariantRecord } from "@/lib/types";
import Charts from "./Charts";
import { CodePanel, DecisionPanel, FidelityBadge, PublishPanel, ScenePanel, TrustPanel } from "./Panels";
import { Player } from "./player";
import Transport from "./Transport";
import BlendButton from "./BlendButton";
import ObjectsPanel from "./ObjectsPanel";
import { EditPanel, WhatChanged } from "./Versions";
import TunePanel, { runLabel, useTuning, type RunId } from "./TunePanel";

const Viewport = dynamic(() => import("./Viewport"), {
  ssr: false,
  loading: () => <div className="grid h-full place-items-center text-sm text-muted">Loading 3D viewport…</div>,
});

type TabId = "charts" | "tune" | "trust" | "scene" | "code";
const noCapture = { current: null };
/** Compare-only run: the version this one was built from. */
const BASE_RUN: RunId = "base-version";

export interface SimulationViewProps {
  outcome: JobOutcome | null;
  playbackUrl: string | null;
  sceneUrl: string | null;
  solverUrl: string | null;
  /** Changes whenever the artifacts behind the URLs change, forcing a reload. */
  assetKey: string;
  jobId: string;
  demo?: boolean;
  /** The job is waiting for the creator's decision (the draft keeps its status while a requested rebuild runs). */
  awaitingDecision?: boolean;
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
  // only the result of THIS request: while a new run loads, show loading rather than the previous run
  return url && state.key === key ? state : { pb: null, error: null, key: "" };
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

/** The simulation's re-runs with changed values; polled while any is still running. */
function useVariants(jobId: string, enabled: boolean) {
  const [variants, setVariants] = useState<VariantRecord[]>([]);
  const [tick, setTick] = useState(0);
  const running = variants.some((v) => v.status === "running");
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    const load = () => listVariants(jobId).then((v) => live && setVariants(v)).catch(() => undefined);
    void load();
    const id = running ? setInterval(load, 1500) : undefined;
    return () => { live = false; if (id) clearInterval(id); };
  }, [jobId, enabled, running, tick]);
  const add = useCallback((v: VariantRecord) => { setVariants((cur) => [...cur, v]); setTick((n) => n + 1); }, []);
  return { variants, add };
}

/** Why the run ends where it does: settle runs stop on their own once everything is at rest. */
function StopNote({ stop, unit }: { stop: NonNullable<Playback["stop"]>; unit: string }) {
  const at = `${fmtNum(stop.at)} ${unit}`;
  const text = stop.rule === "fixed"
    ? `Runs for exactly ${at}${stop.reason ? `: ${stop.reason}` : "."}`
    : stop.settled
      ? `Stopped by itself at ${at}: nothing changed for ${fmtNum(stop.window_s)} s, so the run ended there.`
      : `Ran to its ${at} limit without coming to rest.`;
  const extra = stop.rule === "settle" && stop.window_s !== 3 && stop.reason ? ` (${stop.reason})` : "";
  return <p className="px-1 text-xs text-muted">{text}{extra}</p>;
}

export default function SimulationView(props: SimulationViewProps) {
  const { outcome, playbackUrl, sceneUrl, solverUrl, assetKey, jobId, demo = false } = props;
  const tunable = !demo && !!outcome?.spec && !!outcome.code_key && outcome.status !== "failed";
  const { variants, add } = useVariants(jobId, tunable);
  const [shown, setShown] = useState<RunId>("original");
  const [selected, setSelected] = useState<string | null>(null);
  const [compareTo, setCompareTo] = useState<RunId | null>(null);
  const [autoShow, setAutoShow] = useState<string | null>(null);
  // a rebuilt draft (new artifacts) starts on its own original run: tuned runs belong to the build they came from
  const [base, setBase] = useState(assetKey);
  if (base !== assetKey) {
    setBase(assetKey);
    setShown("original");
    setCompareTo(null);
    setAutoShow(null);
  }
  const edit = demo ? null : (outcome?.edit ?? null);
  const baseJobId = edit?.base_job_id ?? null;
  const baseLabel = edit ? `v${edit.base_version ?? edit.version - 1} · before this change` : null;
  const baseRun = baseLabel ? { id: BASE_RUN, label: baseLabel } : undefined;
  const urlOf = useCallback((id: RunId) => {
    if (id === "original") return playbackUrl;
    if (id === BASE_RUN) return baseJobId ? artifactUrl(baseJobId, "playback.json") : null;
    return variantPlaybackUrl(jobId, id);
  }, [playbackUrl, jobId, baseJobId]);
  const labelOf = (id: RunId) => (id === BASE_RUN && baseLabel ? baseLabel : runLabel(id, variants));
  const { pb, error } = usePlayback(urlOf(shown), `${assetKey}|${shown}`);
  const compareRun = compareTo !== null && compareTo !== shown ? compareTo : null;
  const { pb: comparePb } = usePlayback(compareRun ? urlOf(compareRun) : null, `${assetKey}|${compareRun}`);

  // a run the user just started shows itself when it is ready, compared with what was on screen
  useEffect(() => {
    if (!autoShow) return;
    const v = variants.find((x) => x.variant_id === autoShow);
    if (v?.status === "ready") { setCompareTo(shown); setShown(autoShow); setAutoShow(null); }
    if (v?.status === "failed") setAutoShow(null);
  }, [variants, autoShow, shown]);
  const solver = useSolver(solverUrl, assetKey);
  const [tab, setTab] = useState<TabId>("charts");
  const capture = useRef<(() => string | null) | null>(null);

  const player = useMemo(() => (pb ? new Player(pb.t[0] ?? 0, pb.t[pb.t.length - 1] ?? 0) : null), [pb]);
  useEffect(() => () => player?.dispose(), [player]);

  const spec = outcome?.spec ?? null;
  const onCreated = useCallback((v: VariantRecord) => { add(v); setAutoShow(v.variant_id); }, [add]);
  const tuning = useTuning(spec, jobId, onCreated);
  const deciding = outcome !== null && (demo || (props.awaitingDecision ?? outcome.status === "awaiting_review"));
  const publishable = !demo && outcome?.status === "approved";
  const tabs: { id: TabId; label: string; show: boolean }[] = [
    { id: "charts", label: "Charts", show: pb !== null },
    { id: "tune", label: "Tune", show: tunable },
    { id: "trust", label: "Trust", show: true },
    { id: "scene", label: "3D models", show: !demo && (!!outcome?.scene_report || !!outcome?.dress_report) },
    { id: "code", label: "Code", show: !(demo && solver.missing) && solverUrl !== null },
  ];
  const visible = tabs.filter((t) => t.show);
  const active = visible.some((t) => t.id === tab) ? tab : (visible[0]?.id ?? "trust");

  const title = spec?.title ?? pb?.title ?? "Simulation";
  const fidelity = spec?.fidelity ?? pb?.fidelity ?? "heuristic";

  return (
    <div className={`grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] ${
      // the narrow first column is for the objects panel: without a spec there is none, so the viewer keeps its width
      spec ? "xl:grid-cols-[17rem_minmax(0,1.2fr)_minmax(0,1fr)]" : "xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]"}`}>
      {spec ? (
        <div className="min-w-0 lg:col-span-2 xl:sticky xl:top-20 xl:order-first xl:col-span-1 xl:max-h-[calc(100vh-6rem)] xl:self-start xl:overflow-y-auto">
          <ObjectsPanel spec={spec} tuning={tuning} selected={selected} onSelect={setSelected} pb={pb} player={player} editable={tunable} />
        </div>
      ) : null}
      <div className="space-y-3 lg:sticky lg:top-20 lg:self-start">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold">{title}</h2>
          <FidelityBadge fidelity={fidelity} />
        </div>
        {shown !== "original" && spec ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-accent/50 bg-accent/10 px-3 py-2 text-sm">
            <span className="min-w-0">
              <strong>{runLabel(shown, variants)}</strong>{" "}
              <span className="font-mono text-xs text-muted">
                {describeChanges(spec, variants.find((v) => v.variant_id === shown)?.changes ?? { parameters: [], initial: [] }).join("; ")}
              </span>
            </span>
            <button type="button" onClick={() => { setShown("original"); setCompareTo(null); }} className="text-xs font-semibold text-accent underline">
              Back to the original
            </button>
          </div>
        ) : null}
        {pb && player ? (
          <>
            <div className="h-[48vh] min-h-[16rem] lg:h-[60vh]">
              <Viewport sceneUrl={sceneUrl} playback={pb} player={player} captureRef={capture} selected={selected} onSelect={setSelected} />
            </div>
            <Transport player={player} unit={pb.unit_t} />
            {!demo && shown !== BASE_RUN ? <BlendButton jobId={jobId} run={shown} hasMainBlend={!!outcome?.blend_key} /> : null}
            {pb.stop ? <StopNote stop={pb.stop} unit={pb.unit_t} /> : null}
          </>
        ) : (
          <div className="grid h-64 place-items-center rounded-lg border border-line bg-surface p-4 text-center text-sm text-muted">
            {error ? `Could not load playback data: ${error}` : playbackUrl ? "Loading playback…" : "No playback data was produced for this run."}
          </div>
        )}
        {deciding && outcome && shown !== "original" ? (
          <p className="text-sm text-muted">
            Accept and Ask for changes apply to the original build, not the tuned run on screen.{" "}
            <button type="button" onClick={() => setShown("original")} className="text-accent underline">Show the original</button>
          </p>
        ) : null}
        {deciding && outcome ? <DecisionPanel key={`${outcome.review_round}-${outcome.reviews.length}`} jobId={jobId} localOnly={demo} outcome={outcome} onSubmitted={props.onReviewed} /> : null}
        {publishable ? <EditPanel key={`edit-${jobId}`} jobId={jobId} /> : null}
        {publishable && outcome ? <PublishPanel key={jobId} jobId={jobId} outcome={outcome} capture={shown === "original" ? capture : noCapture} /> : null}
      </div>

      <div className="min-w-0">
        {edit ? <div className="mb-3"><WhatChanged edit={edit} /></div> : null}
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
          {active === "charts" && pb && player ? (
            <>
              {comparePb && compareRun ? (
                <p className="mb-3 text-xs text-muted">
                  Solid: {labelOf(shown)}. Dashed: {labelOf(compareRun)}.
                </p>
              ) : null}
              <Charts playback={pb} player={player} compare={comparePb && compareRun ? { pb: comparePb, label: labelOf(compareRun) } : null} />
            </>
          ) : null}
          {active === "tune" && spec ? (
            <TunePanel
              spec={spec} tuning={tuning} variants={variants}
              shown={shown} compare={compareRun} onShow={setShown} onCompare={setCompareTo}
              shownPb={pb} comparePb={comparePb} baseRun={baseRun}
            />
          ) : null}
          {active === "trust" ? <TrustPanel spec={spec} research={outcome?.research ?? null} fidelity={fidelity} clarifications={demo ? [] : (outcome?.clarifications ?? [])} intake={demo ? [] : (outcome?.intake ?? [])} /> : null}
          {active === "scene" && (outcome?.scene_report || outcome?.dress_report) ? (
            <ScenePanel report={outcome.scene_report ?? null} dress={outcome.dress_report ?? null} jobId={demo ? null : jobId} />
          ) : null}
          {active === "code" ? <CodePanel code={solver.code} /> : null}
        </div>
      </div>
    </div>
  );
}
