import { colormap, colormapForUnit, normalize, type ColormapName, type RGB } from "./colormap";
import { isFieldSeries, sampleField, sampleScalar } from "./playback";
import { evaluateArticulation, type Articulation, type BaseLookup } from "./articulation";
import type { Playback } from "./types";

export interface EntityPose {
  /** Axis translations replacing the node base translation on those axes only. */
  position: { x?: number; y?: number; z?: number };
  rotationZ?: number;
  /** Uniform scale. */
  scale?: number;
  color?: RGB;
  colorField?: { values: number[]; min: number; max: number; map: ColormapName };
}

export function scaleFromRange(value: number, min: number, max: number): number {
  return 0.5 + 1.0 * normalize(value, min, max);
}

/** Visual pose of every driven entity at `time`. Non-finite samples leave the channel untouched. */
export function evaluateTracks(pb: Playback, time: number): Map<string, EntityPose> {
  return evaluateScene(pb, time).poses;
}

/** Entity poses plus articulation link segments (empty for v1 files). */
export function evaluateScene(
  pb: Playback,
  time: number,
  base?: BaseLookup,
): { poses: Map<string, EntityPose>; links: Articulation["links"] } {
  const art = evaluateArticulation(pb, time, base);
  const poses = new Map<string, EntityPose>();
  for (const tr of pb.tracks) {
    const s = pb.series[tr.state];
    if (!s) continue;
    let pose = poses.get(tr.entity);
    if (!pose) {
      pose = { position: {} };
      poses.set(tr.entity, pose);
    }
    if (tr.channel === "color_field") {
      if (!isFieldSeries(s)) continue;
      pose.colorField = {
        values: sampleField(s.values, pb.t, time),
        min: tr.min,
        max: tr.max,
        map: colormapForUnit(s.unit),
      };
      continue;
    }
    if (isFieldSeries(s)) continue;
    const v = sampleScalar(s.values, pb.t, time);
    if (!Number.isFinite(v)) continue;
    switch (tr.channel) {
      case "position_x": if (!art.poses.has(tr.entity)) pose.position.x = v * tr.scale; break;
      case "position_y": if (!art.poses.has(tr.entity)) pose.position.y = v * tr.scale; break;
      case "position_z": if (!art.poses.has(tr.entity)) pose.position.z = v * tr.scale; break;
      case "rotation_z": if (!art.poses.has(tr.entity)) pose.rotationZ = v * tr.scale; break;
      case "scale": pose.scale = scaleFromRange(v, tr.min, tr.max); break;
      case "color": pose.color = colormap(colormapForUnit(s.unit), normalize(v, tr.min, tr.max)); break;
    }
  }
  for (const [name, a] of art.poses) {
    let pose = poses.get(name);
    if (!pose) {
      pose = { position: {} };
      poses.set(name, pose);
    }
    pose.position = { x: a.position[0], y: a.position[1], z: a.position[2] };
    pose.rotationZ = a.angle;
  }
  return { poses, links: art.links };
}

export interface LegendEntry {
  entity: string; state: string; unit: string; min: number; max: number; map: ColormapName;
}

/** One legend per distinct (state, range) colour mapping. */
export function legends(pb: Playback): LegendEntry[] {
  const out: LegendEntry[] = [];
  for (const tr of pb.tracks) {
    if (tr.channel !== "color" && tr.channel !== "color_field") continue;
    const s = pb.series[tr.state];
    if (!s) continue;
    if (out.some((l) => l.state === tr.state && l.min === tr.min && l.max === tr.max)) continue;
    out.push({ entity: tr.entity, state: tr.state, unit: s.unit, min: tr.min, max: tr.max, map: colormapForUnit(s.unit) });
  }
  return out;
}
