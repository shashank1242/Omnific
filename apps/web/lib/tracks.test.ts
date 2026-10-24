import { describe, expect, it } from "vitest";
import { evaluateTracks, legends, scaleFromRange } from "./tracks";
import { parsePlayback } from "./playback";

const pb = parsePlayback({
  version: 1, title: "t", fidelity: "heuristic", t: [0, 2], unit_t: "second",
  series: {
    x: { unit: "meter", values: [0, 100] },
    th: { unit: "radian", values: [-0.5, 0.5] },
    T: { unit: "kelvin", values: [300, 500] },
    P: { unit: "kelvin", values: [[300, 400], [400, 500]] },
    m: { unit: "kilogram", values: [1, 3] },
  },
  entities: [],
  tracks: [
    { entity: "ball", channel: "position_x", state: "x", min: 0, max: 100, scale: 0.1 },
    { entity: "ball", channel: "scale", state: "m", min: 1, max: 3, scale: 1 },
    { entity: "rod", channel: "rotation_z", state: "th", min: -0.5, max: 0.5, scale: 2 },
    { entity: "pizza", channel: "color", state: "T", min: 300, max: 500, scale: 1 },
    { entity: "pizza", channel: "color_field", state: "P", min: 300, max: 500, scale: 1 },
  ],
});

describe("evaluateTracks", () => {
  it("applies position with scale and interpolation", () => {
    expect(evaluateTracks(pb, 1).get("ball")?.position.x).toBeCloseTo(5);
  });
  it("applies rotation and scale", () => {
    expect(evaluateTracks(pb, 2).get("rod")?.rotationZ).toBeCloseTo(1);
    expect(evaluateTracks(pb, 0).get("ball")?.scale).toBeCloseTo(0.5);
    expect(evaluateTracks(pb, 2).get("ball")?.scale).toBeCloseTo(1.5);
  });
  it("applies color and color_field", () => {
    const p = evaluateTracks(pb, 1).get("pizza");
    expect(p?.color).toBeDefined();
    expect(p?.colorField?.values).toEqual([350, 450]);
  });
  it("scaleFromRange handles degenerate range", () => {
    expect(scaleFromRange(1, 1, 1)).toBe(0.5);
  });
  it("lists legends with units", () => {
    expect(legends(pb)).toHaveLength(2);
    expect(legends(pb)[0]).toMatchObject({ unit: "kelvin", map: "inferno" });
  });
});
