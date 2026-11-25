"use client";

import { fmtNum } from "@/lib/format";
import type { PlaybackOutput } from "@/lib/types";
import type { Player } from "./player";

const WHEN: Partial<Record<PlaybackOutput["measure"], string>> = { max: "at", min: "at", initial: "at", final: "at" };

/** The headline numbers chosen for this kind of simulation; clicking one jumps the playback to that moment. */
export default function KeyResults({ outputs, unitT, player }: { outputs: PlaybackOutput[]; unitT: string; player: Player | null }) {
  return (
    <section aria-labelledby="key-results-h" className="rounded-xl border border-line bg-surface p-3">
      <h3 id="key-results-h" className="mb-2 text-sm font-bold tracking-tight">Key results</h3>
      <ul className="grid gap-2 sm:grid-cols-2">
        {outputs.map((o) => {
          const at = o.at;
          const jump = at !== null && player ? () => { player.pause(); player.seek(at); } : undefined;
          const body = (
            <>
              <span className="block text-xs text-muted">{o.label}</span>
              <span className="tabular block font-mono text-lg font-semibold leading-tight">
                {fmtNum(o.value)} <span className="text-sm font-normal text-muted">{o.unit || ""}</span>
              </span>
              {WHEN[o.measure] && at !== null ? (
                <span className="tabular block font-mono text-[11px] text-muted">{WHEN[o.measure]} t = {fmtNum(at)} {unitT}</span>
              ) : null}
              {o.why ? <span className="mt-1 block text-xs text-muted">{o.why}</span> : null}
            </>
          );
          return (
            <li key={`${o.label}-${o.state}`}>
              {jump ? (
                <button type="button" onClick={jump} title="Show this moment"
                  className="h-full w-full rounded-lg border border-line px-3 py-2 text-left transition-colors hover:border-fg/40">
                  {body}
                </button>
              ) : <div className="h-full rounded-lg border border-line px-3 py-2">{body}</div>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
