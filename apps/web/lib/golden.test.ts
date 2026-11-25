import { describe, expect, it } from "vitest";
import golden from "./fixtures/playback-golden.json";
import { parsePlayback } from "./playback";
import { evaluateScene } from "./tracks";
import type { Vec3 } from "./types";

// The same fixture is checked by sandbox/tests/test_playback_math.py against the Python port the Blender export
// uses (sandbox/src/omnific_sandbox/playback_math.py): the browser and a downloaded .blend pose entities identically.

type Expected = { position: Partial<Record<"x" | "y" | "z", number>>; rotation_z?: number; scale?: number; color?: number[] };

describe("golden playback fixture (shared with the Blender export)", () => {
  const pb = parsePlayback(golden.playback);
  const base = golden.base as unknown as Record<string, Vec3>;

  for (const c of golden.cases) {
    it(`poses every entity like the Python port at t=${c.time}`, () => {
      const { poses } = evaluateScene(pb, c.time, (name) => base[name]);
      const expected = c.poses as Record<string, Expected>;
      expect([...poses.keys()].sort()).toEqual(Object.keys(expected).sort());
      for (const [name, want] of Object.entries(expected)) {
        const got = poses.get(name);
        expect(got, name).toBeDefined();
        if (!got) continue;
        for (const axis of ["x", "y", "z"] as const) {
          if (want.position[axis] === undefined) expect(got.position[axis], `${name}.${axis}`).toBeUndefined();
          else expect(got.position[axis], `${name}.${axis}`).toBeCloseTo(want.position[axis] as number, 12);
        }
        if (want.rotation_z === undefined) expect(got.rotationZ, `${name}.rotationZ`).toBeUndefined();
        else expect(got.rotationZ, `${name}.rotationZ`).toBeCloseTo(want.rotation_z, 12);
        if (want.scale === undefined) expect(got.scale, `${name}.scale`).toBeUndefined();
        else expect(got.scale, `${name}.scale`).toBeCloseTo(want.scale, 12);
        if (want.color === undefined) expect(got.color, `${name}.color`).toBeUndefined();
        else want.color.forEach((v, i) => expect(got.color?.[i], `${name}.color[${i}]`).toBeCloseTo(v, 12));
      }
    });
  }
});
