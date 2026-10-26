import { isFieldSeries, sampleScalar } from "./playback";
import type { Playback } from "./types";

/** One animation clip declared in scene.gltf node extras (docs/playback-format.md, "scene.gltf v3 extras"). */
export interface ClipSpec { entity: string; name: string; kind: string; driveState: string }

/** Reads `extras.omnific.clips` (three.js puts glTF extras in `userData`). Malformed entries are skipped. */
export function parseClipExtras(entity: string, userData: unknown): ClipSpec[] {
  const om = (userData as { omnific?: { clips?: unknown } } | null | undefined)?.omnific;
  if (!om || !Array.isArray(om.clips)) return [];
  return om.clips.flatMap((c: unknown) => {
    const o = c as Record<string, unknown> | null;
    if (!o || typeof o.name !== "string" || !o.name) return [];
    return [{
      entity,
      name: o.name,
      kind: typeof o.kind === "string" ? o.kind : "custom",
      driveState: typeof o.drive_state === "string" ? o.drive_state : "",
    }];
  });
}

/** glTF animation name for a clip: `<entity>:<clip>`. */
export const animationName = (c: ClipSpec) => `${c.entity}:${c.name}`;

/** Largest |value| of a scalar state series: the drive at which the clip plays at 1x. 0 if unusable. */
export function driveReference(pb: Playback, state: string): number {
  const s = pb.series[state];
  if (!s || isFieldSeries(s)) return 0;
  let m = 0;
  for (const v of s.values as number[]) if (Number.isFinite(v)) m = Math.max(m, Math.abs(v));
  return m;
}

/** Playback-rate multiplier at `time`: |drive(t)| / reference, or 1 for an undriven clip. */
export function clipRate(pb: Playback, clip: ClipSpec, time: number, reference: number): number {
  if (!clip.driveState) return 1;
  const s = pb.series[clip.driveState];
  if (!s || isFieldSeries(s) || reference <= 0) return 1;
  const v = sampleScalar(s.values as number[], pb.t, time);
  return Number.isFinite(v) ? Math.abs(v) / reference : 0;
}

/**
 * Advance a clip's phase (seconds into the clip) by the simulation-time step `dt` at `rate`, wrapping at
 * `duration`. A negative dt (seeking backwards) rewinds. The result is always in [0, duration).
 */
export function advancePhase(phase: number, dt: number, rate: number, duration: number): number {
  if (!(duration > 0)) return 0;
  const next = phase + dt * rate;
  return ((next % duration) + duration) % duration;
}
