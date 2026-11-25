"use client";

import { useCallback, useMemo, useState } from "react";
import { ApiError, createVariant } from "@/lib/api";
import { changeCount, changesFrom, describeChanges, impacts, valuesOf, type TuneValues } from "@/lib/compare";
import { fmtNum } from "@/lib/format";
import type { Playback, SimSpec, VariantRecord } from "@/lib/types";
import Icon from "./Icon";

export type RunId = "original" | string;

export function runLabel(id: RunId, variants: VariantRecord[]): string {
  if (id === "original") return "Original";
  const i = variants.findIndex((v) => v.variant_id === id);
  return i < 0 ? "Run" : `Run ${i + 1}`;
}

const card = "rounded-xl border border-line bg-surface p-4";
const input = "tabular w-full min-w-0 rounded-lg border bg-bg px-2.5 py-1.5 text-right font-mono text-[13px] outline-none focus:border-accent";

export function Field({ label, hint, unit, value, original, onChange }: {
  label: string; hint?: string; unit: string; value: string; original: number; onChange: (v: string) => void;
}) {
  const changed = value.trim() !== "" && Number(value) !== original;
  return (
    <label className="grid grid-cols-[minmax(0,1fr)_7.5rem_minmax(0,4.5rem)] items-center gap-x-3 gap-y-0.5 py-1.5">
      <span className="min-w-0">
        <span className="block truncate font-mono text-[13px]">{label}</span>
        {hint ? <span className="block truncate text-xs text-muted" title={hint}>{hint}</span> : null}
      </span>
      <input
        inputMode="decimal"
        className={`${input} ${changed ? "border-accent" : "border-line"}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <span className="truncate text-xs text-muted" title={unit}>{unit || "–"}</span>
      {changed ? <span className="col-start-2 text-right text-[11px] text-accent">was <span className="tabular font-mono">{fmtNum(original)}</span></span> : null}
    </label>
  );
}

/** Change any value, re-run the simulation's own solver with it, and compare the runs. */
/** The pending edits shared by the object inspector and the Tune tab, and the one way to run them. */
const NO_VALUES: TuneValues = { parameters: {}, initial: {}, end: "" };
const NO_CHANGES = { changes: { parameters: [], initial: [], end: null }, error: null };

export function useTuning(spec: SimSpec | null, jobId: string, onCreated: (v: VariantRecord) => void) {
  const [values, setValues] = useState<TuneValues>(() => (spec ? valuesOf(spec) : NO_VALUES));
  // a new spec (another review round, or the spec arriving) starts from its own values
  const specKey = spec ? JSON.stringify([spec.parameters.map((p) => [p.name, p.value]), spec.state.map((x) => [x.name, x.initial_value]), spec.time.end]) : "";
  const [seededFor, setSeededFor] = useState(specKey);
  if (seededFor !== specKey) {
    setSeededFor(specKey);
    setValues(spec ? valuesOf(spec) : NO_VALUES);
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { changes, error: inputError } = useMemo(() => (spec ? changesFrom(spec, values) : NO_CHANGES), [spec, values]);
  const n = changeCount(changes);
  const set = useCallback((part: "parameters" | "initial", name: string, v: string) =>
    setValues((cur) => ({ ...cur, [part]: { ...cur[part], [name]: v } })), []);
  const setEnd = useCallback((v: string) => setValues((c) => ({ ...c, end: v })), []);
  const reset = useCallback(() => { setValues(spec ? valuesOf(spec) : NO_VALUES); setError(null); }, [spec]);
  const run = useCallback(async () => {
    if (inputError || n === 0) { setError(inputError ?? "Change at least one value first."); return; }
    setBusy(true);
    setError(null);
    try {
      onCreated(await createVariant(jobId, changes));
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Could not start the run");
    } finally {
      setBusy(false);
    }
  }, [inputError, n, onCreated, jobId, changes]);
  return { values, set, setEnd, reset, run, busy, error: error ?? inputError, n };
}
export type Tuning = ReturnType<typeof useTuning>;

/** The run button and change count, shared by both places edits happen. */
export function RunChanges({ tuning, compact = false }: { tuning: Tuning; compact?: boolean }) {
  return (
    <div className={compact ? "space-y-2" : "mt-4 space-y-2"}>
      {tuning.error ? <p role="alert" className="text-sm text-bad">{tuning.error}</p> : null}
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" disabled={tuning.busy} onClick={() => void tuning.run()}
          className="inline-flex h-10 items-center gap-2 rounded-lg bg-flash px-4 text-sm font-bold text-flash-fg transition-[filter,transform] hover:brightness-105 active:scale-[0.98] disabled:opacity-60">
          <Icon name="play" size={15} />{tuning.busy ? "Starting…" : "Run with these values"}
        </button>
        <span className="text-xs text-muted">{tuning.n === 0 ? "Nothing changed yet." : `${tuning.n} value${tuning.n === 1 ? "" : "s"} changed`}</span>
      </div>
    </div>
  );
}

export default function TunePanel({
  spec, tuning, variants, shown, compare, onShow, onCompare, shownPb, comparePb, baseRun,
}: {
  spec: SimSpec; tuning: Tuning; variants: VariantRecord[];
  shown: RunId; compare: RunId | null; onShow: (id: RunId) => void; onCompare: (id: RunId | null) => void;
  shownPb: Playback | null; comparePb: Playback | null;
  /** The version before this one (follow-up edits): can be compared against, never shown or tuned. */
  baseRun?: { id: RunId; label: string };
}) {
  const { values, set, setEnd, reset, n } = tuning;

  const label = (id: RunId) => (baseRun && id === baseRun.id ? baseRun.label : runLabel(id, variants));
  const runs: { id: RunId; record: VariantRecord | null }[] = [
    { id: "original", record: null },
    ...(baseRun ? [{ id: baseRun.id, record: null }] : []),
    ...variants.map((v) => ({ id: v.variant_id, record: v })),
  ];
  const changesOf = (id: RunId) => variants.find((v) => v.variant_id === id)?.changes;
  const effects = shownPb && comparePb && compare !== null && compare !== shown ? impacts(comparePb, shownPb) : [];

  return (
    <div className="space-y-4">
      <section className={card} aria-labelledby="tune-h">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <h3 id="tune-h" className="text-sm font-bold tracking-tight">Change values</h3>
          {n > 0 ? (
            <button type="button" className="text-xs text-muted underline hover:text-fg" onClick={reset}>
              Back to the original values
            </button>
          ) : null}
        </div>
        <p className="mb-3 text-xs text-muted">Edits re-run this simulation&apos;s own solver in seconds. The result isn&apos;t re-checked, so treat it as exploration.</p>

        {spec.parameters.length > 0 ? (
          <fieldset>
            <legend className="mb-1 text-xs font-semibold text-muted">Parameters</legend>
            <div className="divide-y divide-line">
              {spec.parameters.map((p) => (
                <Field key={p.name} label={p.name} hint={p.description} unit={p.unit} value={values.parameters[p.name] ?? ""} original={p.value}
                  onChange={(v) => set("parameters", p.name, v)} />
              ))}
            </div>
          </fieldset>
        ) : null}
        {spec.state.length > 0 ? (
          <fieldset className="mt-3">
            <legend className="mb-1 text-xs font-semibold text-muted">Starting values</legend>
            <div className="divide-y divide-line">
              {spec.state.map((s) => (
                <Field key={s.name} label={s.name} hint={s.description} unit={s.unit} value={values.initial[s.name] ?? ""} original={s.initial_value}
                  onChange={(v) => set("initial", s.name, v)} />
              ))}
            </div>
          </fieldset>
        ) : null}
        <fieldset className="mt-3">
          <legend className="mb-1 text-xs font-semibold text-muted">Duration</legend>
          <Field
            label={spec.time.stop === "settle" ? "longest run time" : "end time"}
            hint={spec.time.stop === "settle" ? "it stops earlier by itself once everything is at rest" : spec.time.stop_reason || undefined}
            unit={spec.time.unit} value={values.end} original={spec.time.end}
            onChange={setEnd}
          />
        </fieldset>

        <RunChanges tuning={tuning} />
      </section>

      <section className={card} aria-labelledby="runs-h">
        <h3 id="runs-h" className="mb-1 text-sm font-bold tracking-tight">Runs</h3>
        <p className="mb-3 text-xs text-muted">Show a run in the viewer, and compare it with another: its charts draw dashed behind the one shown.</p>
        <ul className="divide-y divide-line">
          {runs.map(({ id, record }) => {
            const isBase = baseRun?.id === id;
            const lines = record ? describeChanges(spec, record.changes) : [isBase ? "the simulation as it was before your change" : "the values the model chose"];
            const ready = !record || record.status === "ready";
            return (
              <li key={id} className="flex flex-wrap items-start justify-between gap-2 py-2.5">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm font-semibold">
                    {label(id)}
                    {shown === id ? <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold text-accent-fg">shown</span> : null}
                    {compare === id && shown !== id ? <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">compared</span> : null}
                    {record?.status === "running" ? <span className="text-xs font-normal text-muted motion-safe:animate-pulse">running…</span> : null}
                    {record?.status === "failed" ? <span className="text-xs font-normal text-bad">failed</span> : null}
                  </div>
                  <ul className="mt-0.5 font-mono text-xs text-muted">{lines.map((l) => <li key={l}>{l}</li>)}</ul>
                  {record?.status === "failed" && record.error ? (
                    <details className="mt-1 text-xs text-bad"><summary className="cursor-pointer">Why it failed</summary>
                      <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{record.error}</pre></details>
                  ) : null}
                </div>
                <div className="flex shrink-0 gap-1.5">
                  <button type="button" disabled={!ready || shown === id} hidden={isBase} onClick={() => onShow(id)}
                    className="h-8 rounded-lg border border-line px-3 text-xs font-semibold hover:border-fg/40 disabled:opacity-40">Show</button>
                  <button type="button" disabled={!ready || shown === id} aria-pressed={compare === id}
                    onClick={() => onCompare(compare === id ? null : id)}
                    className={`h-8 rounded-lg border px-3 text-xs font-semibold disabled:opacity-40 ${compare === id ? "border-accent text-accent" : "border-line hover:border-fg/40"}`}>
                    Compare
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      {effects.length > 0 && compare !== null ? (
        <section className={card} aria-labelledby="impact-h">
          <h3 id="impact-h" className="text-sm font-bold tracking-tight">What changed in the result</h3>
          <p className="mb-2 text-xs text-muted">
            {label(shown)} against {label(compare)}
            {changesOf(shown) ?? changesOf(compare) ? ` (${describeChanges(spec, (changesOf(shown) ?? changesOf(compare))!).join("; ")})` : ""}
          </p>
          {effects.every((e) => e.pct !== null ? Math.abs(e.pct) < 0.05 && Math.abs(e.peakTo - e.peakFrom) <= 1e-9 * Math.max(1, Math.abs(e.peakFrom)) : e.from === e.to) ? (
            <p role="status" className="mb-2 rounded-lg border border-warn/50 bg-warn/10 px-3 py-2 text-xs">
              This change made no difference to the result. The model probably works this out from other values it
              stores, so changing it alone has no effect; try the values it depends on.
            </p>
          ) : null}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[26rem] text-left text-sm">
              <thead className="text-xs text-muted">
                <tr><th className="py-1 pr-3 font-medium">Quantity</th><th className="pr-3 text-right font-medium">At the end</th><th className="pr-3 text-right font-medium">Change</th><th className="text-right font-medium">Peak</th></tr>
              </thead>
              <tbody className="tabular font-mono text-[13px]">
                {effects.map((e) => (
                  <tr key={e.name} className="border-t border-line">
                    <td className="py-1.5 pr-3">{e.name} <span className="text-muted">[{e.unit || "–"}]</span></td>
                    <td className="whitespace-nowrap pr-3 text-right">{fmtNum(e.from)} → {fmtNum(e.to)}</td>
                    <td className={`whitespace-nowrap pr-3 text-right ${e.pct === null || Math.abs(e.pct) < 0.05 ? "text-muted" : "text-data"}`}>
                      {e.pct === null ? "–" : `${e.pct > 0 ? "+" : ""}${e.pct.toFixed(Math.abs(e.pct) < 10 ? 1 : 0)}%`}
                    </td>
                    <td className="whitespace-nowrap text-right text-muted">{fmtNum(e.peakFrom)} → {fmtNum(e.peakTo)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}
