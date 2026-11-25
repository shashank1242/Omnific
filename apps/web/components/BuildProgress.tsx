"use client";

import { buildProgress, type Progress } from "@/lib/progress";
import type { Stage } from "@/lib/types";
import Icon from "./Icon";

type Shown = Progress["steps"][number];

// The three gels keep their jobs: magenta marks the current stage; finished stages fire cyan and yellow (and
// magenta) in turn, so the diagram fills with colour as the build nears ready.
const CURRENT = "var(--c1)";
const FIRED = ["var(--c2)", "var(--c3)", "var(--c1)"];
const firedGel = (i: number): string => FIRED[i % FIRED.length] ?? "var(--c2)";

function StepNode({ step, fired }: { step: Shown; fired: string }) {
  const gel = step.state === "current" ? CURRENT : fired;
  const style =
    step.state === "done"
      ? { background: gel, borderColor: gel, color: "var(--gel-ink)" }
      : step.state === "current"
        ? { borderColor: gel, background: `color-mix(in srgb, ${gel} 14%, transparent)` }
        : undefined;
  return (
    <li
      aria-current={step.state === "current" ? "step" : undefined}
      title={step.detail}
      className={`relative flex min-w-[6.5rem] flex-1 flex-col self-stretch rounded-lg border px-3 py-2.5 transition-[background,border-color,color] duration-700 lg:min-w-0 lg:px-2.5 ${
        step.state === "todo" ? "border-line bg-surface-2/60 text-muted" : ""
      } ${step.state === "failed" ? "border-bad bg-bad/10 text-bad" : ""} ${step.state === "done" ? "flash-fire" : ""}`}
      style={style}
    >
      {step.state === "current" ? (
        <span aria-hidden className="build-glow pointer-events-none absolute -inset-px rounded-lg" style={{ ["--glow" as string]: `color-mix(in srgb, ${gel} 45%, transparent)` }} />
      ) : null}
      <span className="flex items-center gap-1 text-sm font-bold leading-tight">
        {step.state === "done" ? <Icon name="check" size={14} /> : step.state === "failed" ? <Icon name="x" size={14} /> : null}
        {step.label}
      </span>
      <span className={`mt-1 text-[11px] leading-snug ${step.state === "done" ? "opacity-80" : "text-muted"}`}>{step.detail}</span>
    </li>
  );
}

function Arrow({ lit }: { lit: boolean }) {
  return (
    <li aria-hidden className={`hidden self-center sm:block ${lit ? "text-fg" : "text-line"}`}>
      <Icon name="arrowRight" size={14} />
    </li>
  );
}

/** The build pipeline as an architecture diagram; each finished stage fires its gel. */
export default function BuildProgress({ stage, revision = 0, failed = false }: { stage: Stage | null; revision?: number; failed?: boolean }) {
  const p = buildProgress(stage, { revision, failed });
  const firstLoop = p.steps.findIndex((x) => x.loop);
  const index = new Map(p.steps.map((s, i) => [s.id, i]));
  const before = p.steps.filter((s, i) => !s.loop && i < firstLoop);
  const loop = p.steps.filter((s) => s.loop);
  const after = p.steps.filter((s, i) => !s.loop && i > firstLoop);
  const reached = (s: Shown) => s.state !== "todo";
  const pct = Math.round(p.fraction * 100);
  const node = (s: Shown, arrow: boolean, leading = false) => (
    <StepFragment key={s.id} step={s} fired={firedGel(index.get(s.id) ?? 0)} arrow={arrow} lit={reached(s)} leading={leading} />
  );

  return (
    <section aria-label="Build progress" className="rounded-2xl border border-line bg-surface p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[15px] font-semibold" aria-live="polite">{p.headline}</p>
        <span className="tabular font-mono text-sm text-muted">{pct}%</span>
      </div>
      <div className="mb-5 flex h-1.5 gap-1" role="progressbar" aria-label="Build progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        {p.steps.map((s, i) => (
          <span
            key={s.id}
            className={`flex-1 rounded-full transition-colors duration-700 ${s.state === "todo" ? "bg-line" : ""} ${s.state === "failed" ? "bg-bad" : ""}`}
            style={s.state === "done" ? { background: firedGel(i) } : s.state === "current" ? { background: CURRENT } : undefined}
          />
        ))}
      </div>
      <ol className="flex flex-wrap items-stretch gap-2 lg:flex-nowrap">
        {before.map((s, i) => node(s, i < before.length - 1 || loop.length > 0))}
        <li className="flex min-w-full flex-1 flex-col rounded-xl border border-dashed border-line p-2 lg:min-w-0 lg:flex-[5_1_0%]">
          <span className="mb-1.5 flex items-center gap-1.5 px-1 text-xs font-medium text-muted">
            <Icon name="loop" size={13} className={p.looping ? "motion-safe:animate-spin" : undefined} />
            Build and self-check loop
          </span>
          <ol className="flex flex-1 flex-wrap items-stretch gap-2 lg:flex-nowrap">
            {loop.map((s, i) => node(s, i < loop.length - 1))}
          </ol>
        </li>
        {after.map((s, i) => node(s, i < after.length - 1, i === 0))}
      </ol>
    </section>
  );
}

function StepFragment({ step, fired, arrow, lit, leading }: { step: Shown; fired: string; arrow: boolean; lit: boolean; leading: boolean }) {
  return (
    <>
      {leading ? <Arrow lit={lit} /> : null}
      <StepNode step={step} fired={fired} />
      {arrow ? <Arrow lit={lit} /> : null}
    </>
  );
}
