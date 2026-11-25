// Framing the whole simulation: the box every object occupies at some moment of the run, and the default view of it.
import { exposureTimes } from "./strobe";
import { evaluateScene } from "./tracks";
import type { Playback } from "./types";

export type V3 = [number, number, number];
export interface Box { min: V3; max: V3 }

/** The default viewing direction (camera minus target, unit length): above, to the right and in front, the angle the
 *  viewer opens with. */
export const VIEW_DIR: V3 = (() => {
  const v: V3 = [9, 7, 12];
  const n = Math.hypot(...v);
  return [v[0] / n, v[1] / n, v[2] / n];
})();

/** `box` (an object where it is now) swept along every displacement it makes during the run. */
export function sweep(box: Box, deltas: V3[]): Box {
  const min: V3 = [...box.min];
  const max: V3 = [...box.max];
  for (const d of deltas) {
    for (let i = 0; i < 3; i++) {
      min[i] = Math.min(min[i]!, box.min[i]! + d[i]!);
      max[i] = Math.max(max[i]!, box.max[i]! + d[i]!);
    }
  }
  return { min, max };
}

/** Where each moving entity is (its playback position) at `count` instants spread over the whole run. Axes a track
 *  never drives are left undefined, so the caller keeps the object's own coordinate there. */
export function runPositions(
  pb: Playback,
  baseOf: (name: string) => [number, number, number] | undefined,
  count = 48,
): Map<string, (number | undefined)[][]> {
  const out = new Map<string, (number | undefined)[][]>();
  for (const t of exposureTimes(pb, count)) {
    for (const [name, pose] of evaluateScene(pb, t, baseOf).poses) {
      const { x, y, z } = pose.position;
      if (x === undefined && y === undefined && z === undefined) continue;
      const list = out.get(name) ?? [];
      list.push([x, y, z]);
      out.set(name, list);
    }
  }
  return out;
}

/** The camera position for looking at `center` from the default direction at `distance`. */
export function viewFrom(center: V3, distance: number): V3 {
  return [center[0] + VIEW_DIR[0] * distance, center[1] + VIEW_DIR[1] * distance, center[2] + VIEW_DIR[2] * distance];
}
