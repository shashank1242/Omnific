import { describe, expect, it } from "vitest";
import { objectsOf } from "./objects";
import type { SimSpec } from "./types";

const P = (name: string) => ({ name, value: 1, unit: "", description: "", source: null });
const S = (name: string) => ({ name, unit: "m", initial_value: 0, description: "" });

const base = {
  parameters: [P("m_ball"), P("r_ball"), P("ramp_length"), P("g"), P("C_rr")],
  state: [S("x"), S("v")],
} as unknown as SimSpec;

describe("objectsOf", () => {
  it("uses the properties each entity declares, and leaves the rest shared", () => {
    const spec = { ...base, entities: [
      { name: "ball", kind: "sphere", bound_state: ["x", "v"], properties: ["m_ball", "r_ball", "C_rr"] },
      { name: "ramp", kind: "wedge", bound_state: [], properties: ["ramp_length"] },
    ] } as unknown as SimSpec;
    const { objects, shared } = objectsOf(spec);
    expect(objects[0]?.properties.map((p) => p.name)).toEqual(["m_ball", "r_ball", "C_rr"]);
    expect(objects[0]?.states.map((s) => s.name)).toEqual(["x", "v"]);
    expect(objects[1]?.properties.map((p) => p.name)).toEqual(["ramp_length"]);
    expect(shared.map((p) => p.name)).toEqual(["g"]);
  });

  it("falls back to matching names for specs without declared properties", () => {
    const spec = { ...base, entities: [
      { name: "ball", kind: "sphere", bound_state: ["x"] },
      { name: "ramp", kind: "wedge", bound_state: [] },
    ] } as unknown as SimSpec;
    const { objects, shared } = objectsOf(spec);
    expect(objects[0]?.properties.map((p) => p.name)).toEqual(["m_ball", "r_ball"]);
    expect(objects[1]?.properties.map((p) => p.name)).toEqual(["ramp_length"]);
    expect(shared.map((p) => p.name)).toEqual(["g", "C_rr"]);
  });

  it("never lists one parameter under two objects and ignores unknown names", () => {
    const spec = { ...base, entities: [
      { name: "a", kind: "x", bound_state: ["nope"], properties: ["g", "missing"] },
      { name: "b", kind: "x", bound_state: [], properties: ["g"] },
    ] } as unknown as SimSpec;
    const { objects } = objectsOf(spec);
    expect(objects[0]?.properties.map((p) => p.name)).toEqual(["g"]);
    expect(objects[0]?.states).toEqual([]);
    expect(objects[1]?.properties).toEqual([]);
  });
});
