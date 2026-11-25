"use client";

import { useMemo, useRef, useState } from "react";
import { fmtNum, steadyFormatter } from "@/lib/format";
import { phaseAt } from "@/lib/effects";
import { PRESET_SPEEDS, parseSpeed, realTimeSpeed, speedLabel } from "@/lib/speed";
import type { PlaybackPhase } from "@/lib/types";
import Icon from "./Icon";
import { usePlayerFrame, usePlayerState, type Player } from "./player";

const CUSTOM = "custom";
const REAL = "real";

export default function Transport({ player, unit, phases }: { player: Player; unit: string; phases?: PlaybackPhase[] }) {
  const s = usePlayerState(player);
  const span = player.t1 - player.t0;
  // One format and width for the whole run, so the clock never changes size while it plays.
  const clock = useMemo(() => steadyFormatter(player.t0, player.t1), [player.t0, player.t1]);
  const readout = useRef<HTMLSpanElement>(null);
  usePlayerFrame(player, ({ time }) => {
    if (readout.current) readout.current.textContent = clock.format(time);
  });
  const btn =
    "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-line bg-surface text-fg transition-colors hover:border-fg/40";
  return (
    <div
      role="group"
      aria-label="Playback controls"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl border border-line bg-surface p-2 pr-3"
      onKeyDown={(e) => {
        const t = e.target;
        if (t instanceof HTMLSelectElement || (t instanceof HTMLInputElement && t.type !== "range")) return;
        if (e.key === " " && !(t instanceof HTMLButtonElement)) {
          e.preventDefault();
          player.toggle();
        }
      }}
    >
      <button
        type="button"
        className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-flash text-flash-fg transition-[filter,transform] hover:brightness-105 active:scale-95"
        onClick={() => player.toggle()}
        aria-label={s.playing ? "Pause" : s.time >= player.t1 ? "Replay" : "Play"}
        title={s.playing ? "Pause (space)" : "Play (space)"}
      >
        <Icon name={s.playing ? "pause" : s.time >= player.t1 ? "reset" : "play"} size={18} />
      </button>
      <button type="button" className={btn} onClick={() => { player.pause(); player.seek(player.t0); }} aria-label="Restart" title="Back to the start">
        <Icon name="reset" size={16} />
      </button>
      <input
        type="range"
        aria-label="Simulation time"
        aria-valuetext={`${fmtNum(s.time)} ${unit}`}
        className="h-9 min-w-[8rem] flex-1 accent-[rgb(var(--accent))]"
        min={player.t0}
        max={player.t1}
        step="any"
        value={s.time}
        onChange={(e) => player.seek(Number(e.target.value))}
      />
      <span className="tabular whitespace-nowrap font-mono text-[13px]" aria-live="off">
        t = <span ref={readout} style={{ minWidth: `${clock.width}ch`, display: "inline-block", textAlign: "right" }} />
        {" "}/ {clock.format(player.t1)} {unit}
      </span>
      <SpeedControl player={player} speed={s.speed} span={span} unit={unit} />
      {phases?.length ? <PhaseBar player={player} phases={phases} unit={unit} /> : null}
    </div>
  );
}

/** The run's stages as a strip under the controls: the current one is lit; click one to jump to its start. */
function PhaseBar({ player, phases, unit }: { player: Player; phases: PlaybackPhase[]; unit: string }) {
  const span = player.t1 - player.t0 || 1;
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  usePlayerFrame(player, ({ time }) => {
    const now = phaseAt(phases, time);
    phases.forEach((ph, k) => {
      const el = refs.current[k];
      if (el) el.dataset.on = String(ph === now);
    });
  });
  return (
    // contain: the strip takes the row's width and shrinks its labels (truncated), instead of the labels' combined
    // length widening the whole page on a phone
    <div role="group" aria-label="Stages of the simulation" className="flex w-full basis-full gap-1" style={{ contain: "inline-size" }}>
      {phases.map((ph, k) => (
        <button
          key={`${ph.name}-${ph.start}`}
          ref={(el) => { refs.current[k] = el; }}
          type="button"
          onClick={() => { player.pause(); player.seek(ph.start); }}
          title={`${ph.name}: from ${fmtNum(ph.start)} ${unit}${ph.description ? `. ${ph.description}` : ""}`}
          style={{ flexGrow: Math.max(ph.end - ph.start, span / 7) / (span / 7), flexBasis: 0 }} // short stages still get room for their name
          className="min-w-0 truncate rounded-md border border-line bg-bg px-2 py-1 text-left text-xs text-muted transition-colors hover:border-fg/40 data-[on=true]:border-flash data-[on=true]:bg-flash data-[on=true]:font-semibold data-[on=true]:text-flash-fg"
        >
          {ph.name}
        </button>
      ))}
    </div>
  );
}

/** Preset speeds, real time (when the run's time unit allows it) and any custom multiple of the normal pace. */
function SpeedControl({ player, speed, span, unit }: { player: Player; speed: number; span: number; unit: string }) {
  const real = realTimeSpeed(span, unit);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const same = (a: number, b: number) => Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(b));
  const preset = PRESET_SPEEDS.find((v) => same(v, speed));
  const isReal = real !== null && same(real, speed);
  const value = isReal ? REAL : preset !== undefined ? String(preset) : CUSTOM;
  const bad = draft.trim() !== "" && parseSpeed(draft) === null;

  const apply = () => {
    const v = parseSpeed(draft);
    if (v !== null) player.setSpeed(v);
    setEditing(false);
  };

  if (editing) {
    return (
      <label className="flex items-center gap-1.5 text-sm text-muted">
        Speed
        <span className="relative">
          <input
            autoFocus
            inputMode="decimal"
            aria-label="Custom speed (times the normal pace)"
            aria-invalid={bad}
            title="0.01 to 1000 times the normal pace. Enter to apply, Escape to cancel."
            className={`tabular h-9 w-20 rounded-lg border bg-surface px-2 pr-5 font-mono text-[13px] text-fg ${bad ? "border-[rgb(var(--bad))]" : "border-line"}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); apply(); }
              if (e.key === "Escape") { e.preventDefault(); setDraft(""); setEditing(false); }
            }}
            onBlur={apply}
          />
          <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 font-mono text-[13px] text-muted">×</span>
        </span>
      </label>
    );
  }

  return (
    <label className="flex items-center gap-1.5 text-sm text-muted">
      Speed
      <select
        className="tabular h-9 rounded-lg border border-line bg-surface px-2 font-mono text-[13px] text-fg"
        value={value}
        onChange={(e) => {
          const v = e.target.value;
          if (v === CUSTOM) {
            setDraft(String(Number(speed.toPrecision(3))));
            setEditing(true);
          } else if (v === REAL && real !== null) {
            player.setSpeed(real);
          } else {
            player.setSpeed(Number(v));
          }
        }}
      >
        {PRESET_SPEEDS.map((v) => (
          <option key={v} value={String(v)}>{speedLabel(v)}</option>
        ))}
        {real !== null ? <option value={REAL}>Real time ({speedLabel(real)})</option> : null}
        <option value={CUSTOM}>{value === CUSTOM ? `Custom (${speedLabel(speed)})` : "Custom…"}</option>
      </select>
    </label>
  );
}
