import { locate } from "./playback";
import type { Playback, PlaybackEffect, PlaybackEntity, PlaybackPhase, RGB3 } from "./types";

/** An effect's per-frame values with nulls as NaN, ready to sample every frame. */
export interface PreparedEffect {
  fx: PlaybackEffect;
  radius: number[];
  intensity: number[];
  /** Per-frame colour channels, or null for one fixed colour. */
  channels: [number[], number[], number[]] | null;
  fixed: RGB3;
}

export interface EffectState { radius: number; intensity: number; color: RGB3 }

const num = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) ? v : NaN);

function isPerFrame(c: PlaybackEffect["color"]): c is RGB3[] {
  return Array.isArray(c[0]);
}

export function prepareEffect(fx: PlaybackEffect): PreparedEffect {
  const perFrame = isPerFrame(fx.color);
  const rows = perFrame ? (fx.color as RGB3[]) : [];
  return {
    fx,
    radius: fx.radius.map(num),
    intensity: fx.intensity.map(num),
    channels: perFrame ? [rows.map((c) => c[0]), rows.map((c) => c[1]), rows.map((c) => c[2])] : null,
    fixed: perFrame ? [1, 1, 1] : (fx.color as RGB3),
  };
}

function sample(values: readonly number[], t: readonly number[], time: number): number {
  const { i, j, f } = locate(t, time);
  const a = values[i] ?? NaN;
  const b = values[j] ?? NaN;
  if (!Number.isFinite(a)) return Number.isFinite(b) ? b : 0;
  if (!Number.isFinite(b)) return a;
  return a + (b - a) * f;
}

/** Radius (scene units), brightness 0..1 and colour of an effect at `time`. */
export function sampleEffect(p: PreparedEffect, t: readonly number[], time: number): EffectState {
  const radius = Math.max(0, sample(p.radius, t, time));
  const intensity = Math.min(1, Math.max(0, sample(p.intensity, t, time)));
  const color: RGB3 = p.channels
    ? [sample(p.channels[0], t, time), sample(p.channels[1], t, time), sample(p.channels[2], t, time)]
    : p.fixed;
  return { radius, intensity, color };
}

/** The phase `time` falls in (the last one includes the end of the run), or null. */
export function phaseAt(phases: readonly PlaybackPhase[] | undefined, time: number): PlaybackPhase | null {
  if (!phases?.length) return null;
  for (let k = phases.length - 1; k >= 0; k--) {
    const ph = phases[k] as PlaybackPhase;
    if (time >= ph.start) return time <= ph.end || k === phases.length - 1 ? ph : null;
  }
  return null;
}

/** Whether an entity exists at `time` (appears_at <= time < disappears_at). */
export function visibleAt(e: Pick<PlaybackEntity, "appears_at" | "disappears_at">, time: number): boolean {
  if (e.appears_at != null && time < e.appears_at) return false;
  if (e.disappears_at != null && time >= e.disappears_at) return false;
  return true;
}

/** Whether the playback has anything for the effect layer to draw or show. */
export function hasStory(pb: Playback): boolean {
  return !!(pb.effects?.length || pb.phases?.length || pb.entities.some((e) => e.appears_at != null || e.disappears_at != null));
}
