import { describe, expect, it } from "vitest";
import { changeCount, changesFrom, describeChanges, impacts, valuesOf } from "./compare";
import { parsePlayback } from "./playback";
import type { SimSpec } from "./types";

const spec = {
  title: "Drop", prompt: "p", domains: ["mechanics"], fidelity: "analytic", assumptions: [],
  parameters: [
    { name: "g", value: 9.81, unit: "m/s**2", description: "", source: null },
    { name: "m", value: 1, unit: "kg", description: "", source: null },
  ],
  state: [{ name: "y", unit: "m", initial_value: 10, description: "" }],
  equations: [], entities: [], time: { start: 0, end: 2, unit: "second", output_samples: 3 },
  invariants: [], bounds: [], analytic_checks: [], citations: [],
} as unknown as SimSpec;

describe("changesFrom", () => {
  it("reports nothing when nothing changed", () => {
    const { changes, error } = changesFrom(spec, valuesOf(spec));
    expect(error).toBeNull();
    expect(changeCount(changes)).toBe(0);
  });

  it("collects edited parameters, starting values and duration", () => {
    const v = valuesOf(spec);
    v.parameters.g = "1.62";
    v.initial.y = " 5 ";
    v.end = "4";
    const { changes, error } = changesFrom(spec, v);
    expect(error).toBeNull();
    expect(changes).toEqual({ parameters: [{ name: "g", value: 1.62 }], initial: [{ name: "y", value: 5 }], end: 4 });
    expect(describeChanges(spec, changes)).toEqual(["g 9.81 → 1.62 m/s**2", "starting y 10 → 5 m", "duration 2 → 4 second"]);
  });

  it("names the field that is not a number, and refuses an end before the start", () => {
    const v = valuesOf(spec);
    v.parameters.m = "heavy";
    expect(changesFrom(spec, v).error).toBe("m needs a number");
    const w = valuesOf(spec);
    w.end = "0";
    expect(changesFrom(spec, w).error).toMatch(/end after 0/);
  });
});

describe("impacts", () => {
  const mk = (x: number[], v: number[]) => parsePlayback({
    version: 1, title: "t", fidelity: "analytic", t: [0, 1, 2], unit_t: "second",
    series: { x: { unit: "meter", values: x }, v: { unit: "m/s", values: v } },
    entities: [], tracks: [],
  });

  it("compares end values and peaks, biggest change first", () => {
    const out = impacts(mk([0, 5, 10], [1, 3, 2]), mk([0, 6, 12], [1, 3, 2]));
    expect(out[0]).toMatchObject({ name: "x", from: 10, to: 12, peakFrom: 10, peakTo: 12 });
    expect(out[0]?.pct).toBeCloseTo(20);
    expect(out[1]).toMatchObject({ name: "v", pct: 0 });
  });

  it("has no percentage when the base ends at zero", () => {
    const out = impacts(mk([0, 5, 0], [0, 0, 0]), mk([0, 5, 3], [0, 0, 0]));
    expect(out.find((i) => i.name === "x")?.pct).toBeNull();
  });
});
