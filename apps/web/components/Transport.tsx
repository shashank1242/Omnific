"use client";

import { fmtNum } from "@/lib/format";
import Icon from "./Icon";
import { usePlayerState, type Player } from "./player";

const SPEEDS = [0.25, 0.5, 1, 2, 4, 8];

export default function Transport({ player, unit }: { player: Player; unit: string }) {
  const s = usePlayerState(player);
  const span = player.t1 - player.t0;
  const btn =
    "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-line bg-surface text-fg transition-colors hover:border-fg/40";
  return (
    <div
      role="group"
      aria-label="Playback controls"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl border border-line bg-surface p-2 pr-3"
      onKeyDown={(e) => {
        if (e.target instanceof HTMLSelectElement) return;
        if (e.key === " " && !(e.target instanceof HTMLButtonElement)) {
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
        step={span > 0 ? span / 1000 : 1}
        value={s.time}
        onChange={(e) => player.seek(Number(e.target.value))}
      />
      <span className="tabular min-w-[9rem] font-mono text-[13px]" aria-live="off">
        t = {fmtNum(s.time)} / {fmtNum(player.t1)} {unit}
      </span>
      <label className="flex items-center gap-1.5 text-sm text-muted">
        Speed
        <select
          className="tabular h-9 rounded-lg border border-line bg-surface px-2 font-mono text-[13px] text-fg"
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
