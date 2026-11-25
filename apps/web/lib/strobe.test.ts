import { describe, expect, it } from "vitest";
import { parsePlayback } from "./playback";
import { exposures, exposureTimes, longExposure, project } from "./strobe";

const pb = parsePlayback({
  version: 1, title: "throw", fidelity: "analytic", t: [0, 1, 2], unit_t: "second",
  series: {
    x: { unit: "meter", values: [0, 1, 2] },
    y: { unit: "meter", values: [0, 1, 0] },
    still: { unit: "meter", values: [5, 5, 5] },
  },
  entities: [
    { name: "ball", kind: "sphere", bound_state: ["x", "y"] },
    { name: "post", kind: "box", bound_state: ["still"] },
  ],
  tracks: [
    { entity: "ball", channel: "position_x", state: "x", min: 0, max: 2, scale: 1 },
    { entity: "ball", channel: "position_y", state: "y", min: 0, max: 1, scale: 1 },
    { entity: "post", channel: "position_x", state: "still", min: 5, max: 5, scale: 1 },
  ],
});

describe("exposureTimes", () => {
  it("spans the run, ends included", () => {
    expect(exposureTimes(pb, 5)).toEqual([0, 0.5, 1, 1.5, 2]);
    expect(exposureTimes(pb, 1)).toEqual([0]);
  });
});

describe("exposures", () => {
  it("records moving objects only, oldest first", () => {
    const e = exposures(pb, exposureTimes(pb, 3));
    expect(e.trails.map((t) => t.entity)).toEqual(["ball"]);
    expect(e.trails[0]?.points).toEqual([[0, 0, 0], [1, 1, 0], [2, 0, 0]]);
  });
});

describe("project", () => {
  it("fits the picture into the box with height pointing up", () => {
    const p = project(exposures(pb, exposureTimes(pb, 3)), 100, 60, 10);
    const [first, top, last] = p.trails[0]?.points ?? [];
    expect(first?.[0]).toBeLessThan(last?.[0] ?? 0);
    expect(top?.[1]).toBeLessThan(first?.[1] ?? 0); // higher in the world = smaller y on screen
    for (const [x, y] of p.trails[0]?.points ?? []) {
      expect(x).toBeGreaterThanOrEqual(10);
      expect(x).toBeLessThanOrEqual(90);
      expect(y).toBeGreaterThanOrEqual(10);
      expect(y).toBeLessThanOrEqual(50);
    }
  });
});

describe("project far from the origin", () => {
  it("fits the exposures, not the origin", () => {
    const far = { trails: [{ entity: "b", points: [[1e6, 0, 0], [1e6 + 1, 1, 0]] as [number, number, number][] }], links: [] };
    const [a, b] = project(far, 100, 100, 10).trails[0]?.points ?? [];
    expect(Math.abs((b?.[0] ?? 0) - (a?.[0] ?? 0))).toBeGreaterThan(50);
  });
  it("draws nothing for no exposures", () => expect(project({ trails: [], links: [] }, 100, 60, 10).trails).toEqual([]));
});

describe("longExposure", () => {
  it("spaces samples by elapsed time", () => {
    const heat = parsePlayback({
      version: 1, title: "heat", fidelity: "analytic", t: [0, 1, 3], unit_t: "second",
      series: { T: { unit: "kelvin", values: [0, 10, 30] } }, entities: [], tracks: [],
    });
    const pts = longExposure(heat, 100, 60, 0, 3);
    expect(pts[1]?.[0]).toBeCloseTo(100 / 3);
  });
});
