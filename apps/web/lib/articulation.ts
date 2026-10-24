import { sampleScalar } from "./playback";
import type { Playback, PlaybackEntity, Vec3 } from "./types";

export interface ArticulatedPose {
  position: Vec3;
  pivotWorld: Vec3;
  /** Absolute rotation about z (rad). */
  angle: number;
}
export interface LinkSegment { entity: string; from: Vec3; to: Vec3 }
export interface Articulation {
  poses: Map<string, ArticulatedPose>;
  links: LinkSegment[];
}

/** Entities with an `arm` (v2), parents-first. Cached per playback object. */
const orderCache = new WeakMap<Playback, PlaybackEntity[]>();

function articulatedOrder(pb: Playback): PlaybackEntity[] {
  const hit = orderCache.get(pb);
  if (hit) return hit;
  const byName = new Map(pb.entities.map((e) => [e.name, e]));
  const out: PlaybackEntity[] = [];
  const done = new Set<string>();
  const visit = (e: PlaybackEntity) => {
    if (done.has(e.name)) return;
    done.add(e.name);
    const parent = typeof e.parent === "string" ? byName.get(e.parent) : undefined;
    if (parent) visit(parent);
    if (e.arm) out.push(e);
  };
  for (const e of pb.entities) visit(e);
  orderCache.set(pb, out);
  return out;
}

/** Base (rest) translation of an entity's scene node, e.g. from the glTF. */
export type BaseLookup = (entity: string) => Vec3 | undefined;

/** Position of a non-articulated parent: its position tracks; untracked axes keep the base translation (v1 rule). */
function trackPosition(pb: Playback, name: string, time: number, base?: BaseLookup): Vec3 {
  const b = base?.(name);
  const p: Vec3 = b ? [b[0], b[1], b[2]] : [0, 0, 0];
  for (const tr of pb.tracks) {
    if (tr.entity !== name) continue;
    const idx: number = tr.channel === "position_x" ? 0 : tr.channel === "position_y" ? 1 : tr.channel === "position_z" ? 2 : -1;
    const s = pb.series[tr.state];
    if (idx < 0 || !s || Array.isArray(s.values[0])) continue;
    const v = sampleScalar(s.values as number[], pb.t, time);
    if (Number.isFinite(v)) p[idx] = v * tr.scale;
  }
  return p;
}

/** World poses and link segments of articulated entities at `time`. Angles are absolute. */
export function evaluateArticulation(pb: Playback, time: number, base?: BaseLookup): Articulation {
  const poses = new Map<string, ArticulatedPose>();
  const links: LinkSegment[] = [];
  for (const e of articulatedOrder(pb)) {
    const arm = e.arm as Vec3;
    const pv = e.pivot ?? [0, 0, 0];
    let pivotWorld: Vec3 = [pv[0], pv[1], pv[2]];
    if (typeof e.parent === "string") {
      const pp = poses.get(e.parent)?.position ?? trackPosition(pb, e.parent, time, base);
      pivotWorld = [pp[0] + pv[0], pp[1] + pv[1], pp[2] + pv[2]];
    }
    let angle = 0;
    for (const tr of pb.tracks) {
      if (tr.entity !== e.name || tr.channel !== "rotation_z") continue;
      const s = pb.series[tr.state];
      if (!s || Array.isArray(s.values[0])) continue;
      const v = sampleScalar(s.values as number[], pb.t, time);
      if (Number.isFinite(v)) angle = v * tr.scale;
    }
    const c = Math.cos(angle);
    const sn = Math.sin(angle);
    const position: Vec3 = [
      pivotWorld[0] + arm[0] * c - arm[1] * sn,
      pivotWorld[1] + arm[0] * sn + arm[1] * c,
      pivotWorld[2] + arm[2],
    ];
    poses.set(e.name, { position, pivotWorld, angle });
    links.push({ entity: e.name, from: pivotWorld, to: position });
  }
  return { poses, links };
}
