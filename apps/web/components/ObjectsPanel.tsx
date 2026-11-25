"use client";

import { useMemo, useRef } from "react";
import { finiteBounds, fmtNum, steadyFormatter } from "@/lib/format";
import { objectsOf } from "@/lib/objects";
import { isFieldSeries, sampleScalar } from "@/lib/playback";
import type { Playback, ScalarSeries, SimSpec } from "@/lib/types";
import { usePlayerFrame, type Player } from "./player";
import { Field, RunChanges, type Tuning } from "./TunePanel";

/** Every object in the simulation with its properties; selecting one highlights it in the 3D view. */
export default function ObjectsPanel({ spec, tuning, selected, onSelect, pb, player, editable }: {
  spec: SimSpec; tuning: Tuning | null; selected: string | null; onSelect: (name: string | null) => void;
  pb: Playback | null; player: Player | null; editable: boolean;
}) {
  const { objects, shared } = useMemo(() => objectsOf(spec), [spec]);
  const live = (state: string) => {
    const series = pb?.series[state];
    return series && !isFieldSeries(series) && pb && player ? <LiveValue series={series} pb={pb} player={player} /> : "n/a";
  };
  const field = (name: string, unit: string, original: number, hint: string) =>
    editable && tuning ? (
      <Field key={name} label={name} hint={hint} unit={unit} value={tuning.values.parameters[name] ?? ""} original={original}
        onChange={(v) => tuning.set("parameters", name, v)} />
    ) : (
      <div key={name} className="flex items-baseline justify-between gap-3 py-1.5">
        <span className="min-w-0"><span className="block truncate font-mono text-[13px]">{name}</span>
          {hint ? <span className="block truncate text-xs text-muted" title={hint}>{hint}</span> : null}</span>
        <span className="tabular shrink-0 font-mono text-[13px]">{fmtNum(original)} <span className="text-muted">{unit}</span></span>
      </div>
    );

  return (
    <section aria-labelledby="objects-h" className="rounded-xl border border-line bg-surface">
      <div className="flex items-baseline justify-between gap-2 border-b border-line px-4 py-3">
        <h3 id="objects-h" className="text-sm font-bold tracking-tight">Objects</h3>
        <span className="text-xs text-muted">Click one here or in the 3D view</span>
      </div>
      <ul className="divide-y divide-line">
        {objects.map((o) => {
          const on = selected === o.name;
          return (
            <li key={o.name}>
              <button type="button" aria-expanded={on} onClick={() => onSelect(on ? null : o.name)}
                className={`flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left transition-colors ${on ? "bg-accent/10" : "hover:bg-surface-2"}`}>
                <span className="min-w-0">
                  <span className={`block truncate text-sm font-semibold ${on ? "text-accent" : ""}`}>{o.name}</span>
                  <span className="block truncate text-xs text-muted">{o.kind}</span>
                </span>
                <span className="shrink-0 text-xs text-muted">{o.properties.length} {o.properties.length === 1 ? "property" : "properties"}</span>
              </button>
              {on ? (
                <div className="space-y-3 px-4 pb-4 pt-1">
                  {o.states.length > 0 ? (
                    <div>
                      <p className="mb-1 text-xs font-semibold text-muted">Right now</p>
                      <dl className="divide-y divide-line">
                        {o.states.map((st) => (
                          <div key={st.name} className="flex items-baseline justify-between gap-3 py-1.5">
                            <dt className="truncate font-mono text-[13px]" title={st.description}>{st.name}</dt>
                            <dd className="tabular shrink-0 font-mono text-[13px]">{live(st.name)} <span className="text-muted">{st.unit}</span></dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  ) : null}
                  {o.properties.length > 0 ? (
                    <div>
                      <p className="mb-1 text-xs font-semibold text-muted">Properties</p>
                      <div className="divide-y divide-line">{o.properties.map((p) => field(p.name, p.unit, p.value, p.description))}</div>
                    </div>
                  ) : <p className="text-xs text-muted">No properties of its own: its behaviour comes from the shared values below.</p>}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      {shared.length > 0 ? (
        <details className="border-t border-line">
          <summary className="cursor-pointer px-4 py-2.5 text-sm font-semibold hover:bg-surface-2">Shared values <span className="font-normal text-muted">({shared.length})</span></summary>
          <div className="divide-y divide-line px-4 pb-3">{shared.map((p) => field(p.name, p.unit, p.value, p.description))}</div>
        </details>
      ) : null}
      {editable && tuning && tuning.n > 0 ? (
        <div className="border-t border-line px-4 py-3">
          <RunChanges tuning={tuning} compact />
          <p className="mt-2 text-xs text-muted">Every run is kept with this simulation, so you can come back to any set of changes.</p>
        </div>
      ) : null}
    </section>
  );
}

/** A state's value right now, updated every frame without re-rendering the panel, at one width for the whole run. */
function LiveValue({ series, pb, player }: { series: ScalarSeries; pb: Playback; player: Player }) {
  const steady = useMemo(() => steadyFormatter(...finiteBounds(series.values)), [series]);
  const el = useRef<HTMLSpanElement>(null);
  usePlayerFrame(player, ({ time }) => {
    if (el.current) el.current.textContent = steady.format(sampleScalar(series.values, pb.t, time));
  });
  return <span ref={el} style={{ minWidth: `${steady.width}ch`, display: "inline-block", textAlign: "right" }} />;
}
