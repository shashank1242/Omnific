import { describe, expect, it } from "vitest";
import { advancePhase, animationName, clipRate, driveReference, parseClipExtras } from "./clips";
import type { Playback } from "./types";

const pb = {
  version: 1, title: "t", fidelity: "heuristic", t: [0, 1, 2],
  series: { v: { unit: "m/s", values: [0, -2, 4] }, f: { unit: "K", values: [[1, 2], [3, 4], [5, 6]] } },
  entities: [], tracks: [],
} as unknown as Playback;

describe("scene clips", () => {
  it("parses node extras", () => {
    const clips = parseClipExtras("dog", { omnific: { clips: [{ name: "walk", kind: "locomotion", drive_state: "v" }, { kind: "x" }, null] } });
    expect(clips).toEqual([{ entity: "dog", name: "walk", kind: "locomotion", driveState: "v" }]);
    expect(animationName(clips[0]!)).toBe("dog:walk");
    expect(parseClipExtras("x", undefined)).toEqual([]);
    expect(parseClipExtras("x", { omnific: { clips: "no" } })).toEqual([]);
  });
  it("scales rate by |drive| over its max", () => {
    const walk = { entity: "dog", name: "walk", kind: "locomotion", driveState: "v" };
    const ref = driveReference(pb, "v");
    expect(ref).toBe(4);
    expect(clipRate(pb, walk, 1, ref)).toBeCloseTo(0.5);
    expect(clipRate(pb, walk, 2, ref)).toBeCloseTo(1);
    expect(clipRate(pb, { ...walk, driveState: "" }, 1, ref)).toBe(1);
    expect(clipRate(pb, { ...walk, driveState: "missing" }, 1, 0)).toBe(1);
    expect(driveReference(pb, "f")).toBe(0); // field series can't drive a clip
  });
  it("wraps phase both ways", () => {
    expect(advancePhase(0.9, 0.2, 1, 1)).toBeCloseTo(0.1);
    expect(advancePhase(0.1, -0.2, 1, 1)).toBeCloseTo(0.9);
    expect(advancePhase(0.5, 1, 0, 1)).toBeCloseTo(0.5);
    expect(advancePhase(0.5, 1, 1, 0)).toBe(0);
  });
});
