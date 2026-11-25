// Strobe exposures: where each moving object was at evenly spaced instants, like Edgerton's multiple-exposure
// photographs. Computed from the simulation's own playback data (no 3D scene needed), for thumbnails and for the
// viewer's strobe trails.
import { evaluateScene } from "./tracks";
import type { Playback } from "./types";

export type P3 = [number, number, number];

export interface Trail {
  entity: string;
  /** Positions oldest first; entities that never move are left out. */
  points: P3[];
}

export interface Exposures {
  trails: Trail[];
  /** Link segments (pendulum arms) at the last exposure. */
  links: { from: P3; to: P3 }[];
}

/** Sample times spread over the whole run, first and last included. */
export function exposureTimes(pb: Playback, count: number): number[] {
  const t0 = pb.t[0] ?? 0;
  const t1 = pb.t[pb.t.length - 1] ?? t0;
  if (count <= 1 || t1 <= t0) return [t0];
  return Array.from({ length: count }, (_, i) => t0 + ((t1 - t0) * i) / (count - 1));
}

/** Exposures at `times`. Axes a track never drives stay 0, so a 1D motion still draws as a line of exposures. */
export function exposures(pb: Playback, times: number[]): Exposures {
  const byEntity = new Map<string, P3[]>();
  let links: Exposures["links"] = [];
  for (const time of times) {
    const { poses, links: segs } = evaluateScene(pb, time);
    for (const [name, pose] of poses) {
      const { x, y, z } = pose.position;
      if (x === undefined && y === undefined && z === undefined) continue;
      const list = byEntity.get(name) ?? [];
      list.push([x ?? 0, y ?? 0, z ?? 0]);
      byEntity.set(name, list);
    }
    links = segs.map((s) => ({ from: s.from, to: s.to }));
  }
  const trails: Trail[] = [];
  for (const [entity, points] of byEntity) {
    if (moves(points)) trails.push({ entity, points });
  }
  return { trails, links };
}

function moves(points: P3[]): boolean {
  const first = points[0];
  if (!first) return false;
  return points.some((p) => Math.abs(p[0] - first[0]) + Math.abs(p[1] - first[1]) + Math.abs(p[2] - first[2]) > 1e-9);
}

export interface Projected {
  trails: { entity: string; points: [number, number][] }[];
  links: { from: [number, number]; to: [number, number] }[];
}

/** Flatten to a 2D picture (x right, height up) fitted into a w × h box with padding, keeping the aspect ratio.
 *  A motion without height (all y equal) is drawn on the ground plane instead (x right, z down). */
export function project(e: Exposures, w: number, h: number, pad: number): Projected {
  const all = [...e.trails.flatMap((t) => t.points), ...e.links.flatMap((l) => [l.from, l.to])];
  const flat = all.length > 0 && all.every((p) => Math.abs(p[1] - (all[0]?.[1] ?? 0)) < 1e-9);
  const to2 = (p: P3): [number, number] => (flat ? [p[0], p[2]] : [p[0], -p[1]]);
  const pts = all.map(to2);
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  // bounds of the exposures themselves: forcing the origin in would shrink motion far from it to a dot
  if (pts.length === 0) return { trails: [], links: [] };
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 1e-9), spanY = Math.max(maxY - minY, 1e-9);
  const s = Math.min((w - 2 * pad) / spanX, (h - 2 * pad) / spanY);
  const ox = (w - spanX * s) / 2, oy = (h - spanY * s) / 2;
  const map = (p: P3): [number, number] => {
    const [x, y] = to2(p);
    return [ox + (x - minX) * s, oy + (y - minY) * s];
  };
  return {
    trails: e.trails.map((t) => ({ entity: t.entity, points: t.points.map(map) })),
    links: e.links.map((l) => ({ from: map(l.from), to: map(l.to) })),
  };
}

/** For a simulation where nothing moves (heating, decay): a long exposure of its first changing quantity over
 *  time, as screen points in a w × h box. Empty when no scalar series changes. */
export function longExposure(pb: Playback, w: number, h: number, pad: number, count = 48): [number, number][] {
  for (const s of Object.values(pb.series)) {
    if (!("values" in s) || s.values.length === 0 || Array.isArray(s.values[0])) continue;
    const values = s.values as number[];
    const finite = values.filter(Number.isFinite);
    const lo = Math.min(...finite), hi = Math.max(...finite);
    if (!(hi > lo)) continue;
    const n = Math.min(count, values.length);
    const t0 = pb.t[0] ?? 0, t1 = pb.t[values.length - 1] ?? t0;
    return Array.from({ length: n }, (_, i) => {
      const j = Math.round((i * (values.length - 1)) / Math.max(n - 1, 1));
      const v = values[j] ?? lo;
      // across by elapsed time, so uneven time steps don't fake a change of rate
      const tj = pb.t[j];
      const fx = t1 > t0 && tj !== undefined ? (tj - t0) / (t1 - t0) : i / Math.max(n - 1, 1);
      return [pad + (w - 2 * pad) * fx, h - pad - ((h - 2 * pad) * ((Number.isFinite(v) ? v : lo) - lo)) / (hi - lo)];
    });
  }
  return [];
}
