import { describe, expect, it } from "vitest";
import { locate, parsePlayback, PlaybackError, sampleAlong, sampleField, sampleScalar } from "./playback";

const base = {
  version: 1, title: "x", fidelity: "analytic", t: [0, 1, 3], unit_t: "second",
  series: {
    x: { unit: "meter", values: [0, 10, 30] },
    T: { unit: "kelvin", values: [[1, 2], [3, 4], [5, 6]], field_axis: "x" },
  },
  entities: [{ name: "ball", kind: "sphere", bound_state: ["x"] }],
  tracks: [{ entity: "ball", channel: "position_x", state: "x", min: 0, max: 30, scale: 0.5 }],
};

describe("parsePlayback", () => {
  it("accepts a valid document", () => {
    const pb = parsePlayback(base);
    expect(pb.tracks[0]?.scale).toBe(0.5);
    expect(pb.series.T).toMatchObject({ field_axis: "x" });
  });
  it("rejects bad version, descending t, length mismatch, unknown state", () => {
    expect(() => parsePlayback({ ...base, version: 3 })).toThrow(PlaybackError);
    expect(() => parsePlayback({ ...base, t: [0, 2, 1] })).toThrow(/ascending/);
    expect(() => parsePlayback({ ...base, series: { x: { unit: "m", values: [1] } } })).toThrow(/frames/);
    expect(() => parsePlayback({ ...base, tracks: [{ ...base.tracks[0], state: "nope" }] })).toThrow(/unknown state/);
  });
  it("rejects color_field on a scalar series", () => {
    expect(() => parsePlayback({ ...base, tracks: [{ ...base.tracks[0], channel: "color_field" }] })).toThrow(
      /dimensionality/,
    );
  });
  it("maps null to NaN", () => {
    const pb = parsePlayback({ ...base, series: { x: { unit: "m", values: [0, null, 2] } }, tracks: [] });
    expect(Number.isNaN((pb.series.x as { values: number[] }).values[1])).toBe(true);
  });
});

describe("interpolation", () => {
  const t = [0, 1, 3];
  it("locates and clamps", () => {
    expect(locate(t, -1)).toEqual({ i: 0, j: 0, f: 0 });
    expect(locate(t, 5)).toEqual({ i: 2, j: 2, f: 0 });
    expect(locate(t, 2)).toEqual({ i: 1, j: 2, f: 0.5 });
  });
  it("interpolates scalars non-uniformly", () => {
    expect(sampleScalar([0, 10, 30], t, 0.5)).toBe(5);
    expect(sampleScalar([0, 10, 30], t, 2)).toBe(20);
  });
  it("interpolates fields and along an axis", () => {
    expect(sampleField([[1, 2], [3, 4], [5, 6]], t, 0.5)).toEqual([2, 3]);
    expect(sampleAlong([0, 10], 0.25)).toBe(2.5);
  });
  it("handles single frame", () => {
    expect(sampleScalar([7], [0], 3)).toBe(7);
  });
});

describe("stop record", () => {
  it("keeps a valid stop record and ignores a malformed or missing one", () => {
    const pb = parsePlayback({ ...base, stop: { rule: "settle", at: 7.9, window_s: 3, settled: true, reason: "" } });
    expect(pb.stop).toEqual({ rule: "settle", at: 7.9, window_s: 3, settled: true, reason: "" });
    expect(parsePlayback({ ...base, stop: { rule: "maybe", at: 1 } }).stop).toBeUndefined();
    expect(parsePlayback(base).stop).toBeUndefined();
  });
});

describe("environment", () => {
  const env = { name: "The Moon", description: "Low gravity", kind: "planetary", ground: "flat", ground_material: "regolith",
    lighting: "space", sky_color: "#000000", ground_color: "#8c8c8c", fog: 0 };
  it("keeps a valid setting and drops a malformed one", () => {
    expect(parsePlayback({ ...base, environment: env }).environment?.name).toBe("The Moon");
    expect(parsePlayback({ ...base, environment: { ...env, sky_color: "black" } }).environment).toBeUndefined();
    expect(parsePlayback({ ...base, environment: { ...env, fog: 7, kind: "volcano" } }).environment)
      .toMatchObject({ fog: 1, kind: "outdoor" });
  });
});
