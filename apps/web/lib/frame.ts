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
export function viewFrom(center: V3, distance: number, dir: V3 = VIEW_DIR): V3 {
  return [center[0] + dir[0] * distance, center[1] + dir[1] * distance, center[2] + dir[2] * distance];
}

/**
 * The direction to look at the action from, so a big object near it (a black hole beside an explosion) is behind the
 * action, not between it and the camera: the usual three-quarter view, turned toward the side of the occluder the
 * action is on. Returns the default when there is no occluder or the action sits at its centre.
 */
export function viewAround(action: V3, occluder: V3 | null): V3 {
  if (!occluder) return VIEW_DIR;
  const away: V3 = [action[0] - occluder[0], 0, action[2] - occluder[2]]; // horizontal: keep the camera's height
  const len = Math.hypot(away[0], away[2]);
  if (len < 1e-9) return VIEW_DIR;
  const h = Math.hypot(VIEW_DIR[0], VIEW_DIR[2]);
  // mostly from the action's side, a little of the default so the occluder still shows as a backdrop
  const x = (away[0] / len) * 0.8 + (VIEW_DIR[0] / h) * 0.2;
  const z = (away[2] / len) * 0.8 + (VIEW_DIR[2] / h) * 0.2;
  const k = Math.hypot(x, z) || 1;
  const out: V3 = [(x / k) * h, VIEW_DIR[1], (z / k) * h];
  const n = Math.hypot(...out);
  return [out[0] / n, out[1] / n, out[2] / n];
}
