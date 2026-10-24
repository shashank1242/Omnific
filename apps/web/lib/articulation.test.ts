import { describe, expect, it } from "vitest";
import { evaluateArticulation } from "./articulation";
import { parsePlayback, PlaybackError } from "./playback";
import { evaluateTracks } from "./tracks";

const N = 401;
const t = Array.from({ length: N }, (_, i) => i / 100);
const th = t.map((x) => 0.5 * Math.cos(2 * Math.PI * x));
const th2 = t.map((x) => 0.8 * Math.sin(3 * x));
const L = 4;

function doc(extra: Record<string, unknown> = {}) {
  return {
    version: 2, length_scale: 4, title: "p", fidelity: "analytic", t, unit_t: "second",
    series: {
      theta: { unit: "radian", values: th },
      theta2: { unit: "radian", values: th2 },
      x: { unit: "meter", values: t.map((v) => v) },
    },
    entities: [
      { name: "support", kind: "box", bound_state: [] },
      { name: "bob", kind: "sphere", bound_state: ["theta"], parent: null, pivot: [0, 4, 0], arm: [0, -L, 0] },
    ],
    tracks: [{ entity: "bob", channel: "rotation_z", state: "theta", min: -1, max: 1, scale: 1 }],
    ...extra,
  };
}

describe("articulation", () => {
  it("simple pendulum traces an arc at distance L", () => {
    const pb = parsePlayback(doc());
    let xs = 0;
    for (let k = 0; k < 200; k++) {
      const time = k * 0.0173;
      const a = evaluateArticulation(pb, time).poses.get("bob")!;
      const angle = a.angle;
      expect(Math.hypot(a.position[0] - 0, a.position[1] - 4)).toBeCloseTo(L, 9);
      expect(a.position[0]).toBeCloseTo(L * Math.sin(angle), 9);
      expect(a.position[1]).toBeCloseTo(4 - L * Math.cos(angle), 9);
      xs = Math.max(xs, Math.abs(a.position[0]));
    }
    expect(xs).toBeGreaterThan(1);
  });

  it("double pendulum uses absolute angles", () => {
    const pb = parsePlayback(doc({
      entities: [
        { name: "bob1", kind: "sphere", bound_state: ["theta"], parent: null, pivot: [0, 4, 0], arm: [0, -2, 0] },
        { name: "bob2", kind: "sphere", bound_state: ["theta2"], parent: "bob1", pivot: null, arm: [0, -3, 0] },
      ],
      tracks: [
        { entity: "bob2", channel: "rotation_z", state: "theta2", min: -1, max: 1, scale: 1 },
        { entity: "bob1", channel: "rotation_z", state: "theta", min: -1, max: 1, scale: 1 },
      ],
    }));
    const time = 1.234;
    const { poses, links } = evaluateArticulation(pb, time);
    const b1 = poses.get("bob1")!;
    const b2 = poses.get("bob2")!;
    const a2 = b2.angle;
    expect(b2.position[0]).toBeCloseTo(b1.position[0] + 3 * Math.sin(a2), 9);
    expect(b2.position[1]).toBeCloseTo(b1.position[1] - 3 * Math.cos(a2), 9);
    expect(b2.pivotWorld).toEqual(b1.position);
    expect(links.map((l) => l.entity)).toEqual(["bob1", "bob2"]);
  });

  it("v1 files give the same poses as plain tracks", () => {
    const pb = parsePlayback({
      version: 1, t: [0, 2], series: { x: { unit: "m", values: [0, 10] }, a: { unit: "rad", values: [0, 1] } },
      entities: [{ name: "b", kind: "sphere", bound_state: ["x"] }],
      tracks: [
        { entity: "b", channel: "position_x", state: "x", min: 0, max: 10, scale: 0.5 },
        { entity: "b", channel: "rotation_z", state: "a", min: 0, max: 1, scale: 2 },
      ],
    });
    expect(pb.version).toBe(1);
    expect(evaluateArticulation(pb, 1).links).toEqual([]);
    const p = evaluateTracks(pb, 1).get("b")!;
    expect(p.position).toEqual({ x: 2.5 });
    expect(p.rotationZ).toBeCloseTo(1);
    expect(parsePlayback({ t: [0], series: {} }).version).toBe(1);
  });

  it("ignores position tracks on articulated entities", () => {
    const pb = parsePlayback(doc({
      tracks: [
        { entity: "bob", channel: "rotation_z", state: "theta", min: -1, max: 1, scale: 1 },
        { entity: "bob", channel: "position_x", state: "x", min: 0, max: 4, scale: 100 },
      ],
    }));
    const p = evaluateTracks(pb, 3).get("bob")!;
    const a = evaluateArticulation(pb, 3).poses.get("bob")!;
    expect(p.position.x).toBeCloseTo(a.position[0], 12);
    expect(p.position.y).toBeCloseTo(a.position[1], 12);
    expect(p.rotationZ).toBeCloseTo(a.angle, 12);
  });

  it("validates v2 fields", () => {
    const ents = (over: object[]) => doc({ entities: over });
    const e = (name: string, extra: object) => ({ name, kind: "sphere", bound_state: [], ...extra });
    expect(() => parsePlayback(ents([e("a", { parent: "ghost", arm: [0, -1, 0] })]))).toThrow(/unknown parent/);
    expect(() =>
      parsePlayback(ents([e("a", { parent: "b", arm: [0, -1, 0] }), e("b", { parent: "a", arm: [0, -1, 0] })])),
    ).toThrow(/cycle/);
    expect(() => parsePlayback(ents([e("a", { arm: [0, -1] })]))).toThrow(PlaybackError);
    expect(() => parsePlayback(ents([e("a", { pivot: [0, 1, 2, 3], arm: [0, -1, 0] })]))).toThrow(/pivot/);
    expect(() => parsePlayback(doc({ length_scale: -1 }))).toThrow(/length_scale/);
  });
});

describe("non-articulated parent", () => {
  it("keeps the parent's base translation on untracked axes", () => {
    const pb = parsePlayback({
      version: 2, length_scale: 1, t: [0, 1], unit_t: "second",
      series: { x: { unit: "meter", values: [0, 2] }, th: { unit: "radian", values: [0, 0] } },
      entities: [
        { name: "cart", kind: "box", bound_state: ["x"] },
        { name: "bob", kind: "sphere", bound_state: ["th"], parent: "cart", pivot: [0, 0, 0], arm: [0, -1, 0] },
      ],
      tracks: [
        { entity: "cart", channel: "position_x", state: "x", min: 0, max: 2, scale: 1 },
        { entity: "bob", channel: "rotation_z", state: "th", min: -1, max: 1, scale: 1 },
      ],
    });
    const base = (n: string) => (n === "cart" ? ([5, 3, 0] as [number, number, number]) : undefined);
    const bob = evaluateArticulation(pb, 1, base).poses.get("bob")!;
    // x comes from the track (2); y keeps the cart's base translation (3)
    expect(bob.pivotWorld).toEqual([2, 3, 0]);
    expect(bob.position[1]).toBeCloseTo(2, 12);
  });
});
