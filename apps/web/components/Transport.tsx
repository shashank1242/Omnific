"use client";

import { fmtNum } from "@/lib/format";
import { usePlayerState, type Player } from "./player";

const SPEEDS = [0.25, 0.5, 1, 2, 4, 8];

export default function Transport({ player, unit }: { player: Player; unit: string }) {
  const s = usePlayerState(player);
  const span = player.t1 - player.t0;
  const btn =
    "inline-flex h-9 min-w-9 items-center justify-center rounded-md border border-line bg-surface px-2.5 text-sm hover:bg-line/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";
  return (
    <div
      role="group"
      aria-label="Playback controls"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-line bg-surface p-2"
      onKeyDown={(e) => {
        if (e.target instanceof HTMLSelectElement) return;
        if (e.key === " " && !(e.target instanceof HTMLButtonElement)) {
          e.preventDefault();
          player.toggle();
        }
      }}
    >
      <button type="button" className={btn} onClick={() => player.toggle()} aria-label={s.playing ? "Pause" : "Play"}>
        {s.playing ? "Pause" : s.time >= player.t1 ? "Replay" : "Play"}
      </button>
      <button type="button" className={btn} onClick={() => { player.pause(); player.seek(player.t0); }} aria-label="Restart">
        Restart
      </button>
      <input
        type="range"
        aria-label="Simulation time"
        aria-valuetext={`${fmtNum(s.time)} ${unit}`}
        className="h-9 min-w-[8rem] flex-1 accent-accent"
        min={player.t0}
        max={player.t1}
        step={span > 0 ? span / 1000 : 1}
        value={s.time}
        onChange={(e) => player.seek(Number(e.target.value))}
      />
      <span className="min-w-[9rem] text-sm tabular-nums" aria-live="off">
        t = {fmtNum(s.time)} / {fmtNum(player.t1)} {unit}
      </span>
      <label className="flex items-center gap-1.5 text-sm text-muted">
        Speed
        <select
          className="h-9 rounded-md border border-line bg-surface px-2 text-fg"
          value={s.speed}
          onChange={(e) => player.setSpeed(Number(e.target.value))}
        >
          {SPEEDS.map((v) => (
            <option key={v} value={v}>{v}×</option>
          ))}
        </select>
      </label>
    </div>
  );
}
