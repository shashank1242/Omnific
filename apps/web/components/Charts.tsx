"use client";

import { memo, useEffect, useMemo, useRef } from "react";
import { colormap, colormapForUnit, gradientCss, normalize } from "@/lib/colormap";
import { finiteRange, fmtNum } from "@/lib/format";
import { isFieldSeries, sampleScalar } from "@/lib/playback";
import type { FieldSeries, Playback, ScalarSeries } from "@/lib/types";
import { usePlayerState, type Player } from "./player";

const W = 300;
const H = 110;
const PAD = 4;

const SERIES_COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"];

function timeFromPointer(e: React.PointerEvent<Element>, player: Player) {
  const r = e.currentTarget.getBoundingClientRect();
  const u = r.width > 0 ? (e.clientX - r.left) / r.width : 0;
  player.seek(player.t0 + Math.min(1, Math.max(0, u)) * (player.t1 - player.t0));
}

function useScrub(player: Player) {
  return {
    onPointerDown: (e: React.PointerEvent<Element>) => {
      e.currentTarget.setPointerCapture(e.pointerId);
      player.pause();
      timeFromPointer(e, player);
    },
    onPointerMove: (e: React.PointerEvent<Element>) => {
      if (e.buttons & 1) timeFromPointer(e, player);
    },
  };
}

const Line = memo(function Line({ t, values, color }: { t: number[]; values: number[]; color: string }) {
  const [lo, hi] = finiteRange(values);
  const t0 = t[0] ?? 0;
  const span = (t[t.length - 1] ?? 1) - t0 || 1;
  const d = useMemo(() => {
    let path = "";
    let pen = false;
    values.forEach((v, i) => {
      if (!Number.isFinite(v)) { pen = false; return; }
      const x = PAD + (((t[i] ?? 0) - t0) / span) * (W - 2 * PAD);
      const y = H - PAD - ((v - lo) / (hi - lo)) * (H - 2 * PAD);
      path += `${pen ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
      pen = true;
    });
    return path;
  }, [t, values, lo, hi, t0, span]);
  return <path d={d} fill="none" stroke={color} strokeWidth={1.75} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />;
});

function ScalarChart({ name, series, pb, player, color }: { name: string; series: ScalarSeries; pb: Playback; player: Player; color: string }) {
  const s = usePlayerState(player);
  const scrub = useScrub(player);
  const [lo, hi] = finiteRange(series.values);
  const t0 = pb.t[0] ?? 0;
  const span = (pb.t[pb.t.length - 1] ?? 1) - t0 || 1;
  const cx = PAD + ((s.time - t0) / span) * (W - 2 * PAD);
  const cur = sampleScalar(series.values, pb.t, s.time);
  const cy = H - PAD - ((cur - lo) / (hi - lo)) * (H - 2 * PAD);
  return (
    <figure className="rounded-lg border border-line bg-surface p-3">
      <figcaption className="mb-1 flex items-baseline justify-between gap-2 text-sm">
        <span className="font-medium">{name} <span className="font-normal text-muted">[{series.unit || "dimensionless"}]</span></span>
        <span className="tabular-nums text-muted">{fmtNum(cur)}</span>
      </figcaption>
      <div className="flex gap-2">
        <div className="flex flex-col justify-between text-[11px] tabular-nums text-muted" aria-hidden>
          <span>{fmtNum(hi)}</span>
          <span>{fmtNum(lo)}</span>
        </div>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className="h-28 w-full cursor-col-resize touch-none"
          role="img"
          aria-label={`${name} over time, from ${fmtNum(lo)} to ${fmtNum(hi)} ${series.unit}. Drag to scrub.`}
          {...scrub}
        >
          <line x1={PAD} x2={W - PAD} y1={H - PAD} y2={H - PAD} stroke="rgb(var(--line))" />
          <Line t={pb.t} values={series.values} color={color} />
          <line x1={cx} x2={cx} y1={0} y2={H} stroke="rgb(var(--fg))" strokeWidth={1} strokeOpacity={0.6} vectorEffect="non-scaling-stroke" />
          {Number.isFinite(cy) ? <circle cx={cx} cy={cy} r={3} fill={color} stroke="rgb(var(--surface))" strokeWidth={1.5} /> : null}
        </svg>
      </div>
      <div className="mt-1 flex justify-between pl-9 text-[11px] tabular-nums text-muted" aria-hidden>
        <span>{fmtNum(t0)}</span>
        <span>time [{pb.unit_t}]</span>
        <span>{fmtNum(t0 + span)}</span>
      </div>
    </figure>
  );
}

function FieldHeatmap({ name, series, pb, player }: { name: string; series: FieldSeries; pb: Playback; player: Player }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const s = usePlayerState(player);
  const scrub = useScrub(player);
  const flat = useMemo(() => series.values.flat(), [series]);
  const [lo, hi] = useMemo(() => finiteRange(flat), [flat]);
  const map = colormapForUnit(series.unit);
  const nx = series.values.length;
  const ny = series.values[0]?.length ?? 0;

  useEffect(() => {
    const c = canvas.current;
    const ctx = c?.getContext("2d");
    if (!c || !ctx || nx === 0 || ny === 0) return;
    const img = ctx.createImageData(nx, ny);
    for (let i = 0; i < nx; i++) {
      for (let k = 0; k < ny; k++) {
        const [r, g, b] = colormap(map, normalize(series.values[i]?.[k] ?? NaN, lo, hi));
        const o = ((ny - 1 - k) * nx + i) * 4; // position 0 at the bottom
        img.data[o] = r * 255; img.data[o + 1] = g * 255; img.data[o + 2] = b * 255; img.data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }, [series, nx, ny, lo, hi, map]);

  const t0 = pb.t[0] ?? 0;
  const span = (pb.t[pb.t.length - 1] ?? 1) - t0 || 1;
  const pct = ((s.time - t0) / span) * 100;
  const axis = series.field_axis ?? "position index";
  return (
    <figure className="rounded-lg border border-line bg-surface p-3">
      <figcaption className="mb-1 text-sm font-medium">
        {name} <span className="font-normal text-muted">[{series.unit || "dimensionless"}] — time × {axis}</span>
      </figcaption>
      <div className="relative cursor-col-resize touch-none" {...scrub} role="img" aria-label={`Heatmap of ${name}: time on the horizontal axis, ${axis} on the vertical axis, ${fmtNum(lo)} to ${fmtNum(hi)} ${series.unit}. Drag to scrub.`}>
        <canvas ref={canvas} width={nx} height={ny} className="block h-32 w-full rounded-sm" />
        <div className="pointer-events-none absolute inset-y-0 w-px bg-white mix-blend-difference" style={{ left: `${pct}%` }} />
      </div>
      <div className="mt-1 flex justify-between text-[11px] tabular-nums text-muted" aria-hidden>
        <span>{fmtNum(t0)}</span>
        <span>time [{pb.unit_t}]</span>
        <span>{fmtNum(t0 + span)}</span>
      </div>
      <div className="mt-2 flex items-center gap-2 text-[11px] tabular-nums text-muted">
        <span>{fmtNum(lo)}</span>
        <div className="h-2 flex-1 rounded-sm" style={{ background: gradientCss(map) }} aria-hidden />
        <span>{fmtNum(hi)}</span>
      </div>
    </figure>
  );
}

export default function Charts({ playback, player }: { playback: Playback; player: Player }) {
  const entries = Object.entries(playback.series);
  if (entries.length === 0) return <p className="text-sm text-muted">This run has no result series.</p>;
  let n = 0;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
      {entries.map(([name, s]) =>
        isFieldSeries(s) ? (
          <div key={name} className="sm:col-span-2 lg:col-span-1 xl:col-span-2"><FieldHeatmap name={name} series={s} pb={playback} player={player} /></div>
        ) : (
          <ScalarChart key={name} name={name} series={s} pb={playback} player={player} color={SERIES_COLORS[n++ % SERIES_COLORS.length] as string} />
        ),
      )}
    </div>
  );
}

