import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { phaseAt, prepareEffect, sampleEffect, visibleAt } from "./effects";
import { parsePlayback } from "./playback";

// fixtures/blast is made by the real Data agent (apps/web/e2e/fixtures/make_blast.py)
const pb = parsePlayback(JSON.parse(readFileSync(path.join(__dirname, "..", "e2e", "fixtures", "blast", "playback.json"), "utf-8")));

describe("effects, phases and visibility from a real playback", () => {
  it("keeps the story through parsing", () => {
    expect(pb.effects?.map((e) => e.name)).toEqual(["detonation flash", "fireball", "shock front", "accretion disk"]);
    expect(pb.phases?.map((p) => p.name)).toEqual(["Falling in", "Detonation", "Fireball grows", "Redshifted fade"]);
    expect(pb.outputs?.[0]).toMatchObject({ label: "Peak fireball radius", unit: "meter" });
    expect(pb.outputs?.[0]?.value).toBeCloseTo(12, 1);
    expect(pb.entities.find((e) => e.name === "bomb")?.disappears_at).toBe(2);
  });

  it("the fireball is dark before detonation, then grows white-hot and cools", () => {
    const fire = prepareEffect(pb.effects!.find((e) => e.name === "fireball")!);
    expect(sampleEffect(fire, pb.t, 1.9).intensity).toBe(0);
    const early = sampleEffect(fire, pb.t, 2.3);
    const late = sampleEffect(fire, pb.t, 7.5);
    expect(early.intensity).toBeGreaterThan(0.5);
    expect(late.radius).toBeGreaterThan(early.radius);
    expect(early.color[2]).toBeGreaterThan(late.color[2]); // white-hot (blue channel up) -> orange
  });

  it("phases and appearances follow the clock", () => {
    expect(phaseAt(pb.phases, 0)?.name).toBe("Falling in");
    expect(phaseAt(pb.phases, 2)?.name).toBe("Detonation");
    expect(phaseAt(pb.phases, 8)?.name).toBe("Redshifted fade");
    const bomb = pb.entities.find((e) => e.name === "bomb")!;
    expect(visibleAt(bomb, 1.99)).toBe(true);
    expect(visibleAt(bomb, 2)).toBe(false);
  });

  it("drops a malformed effect instead of failing the playback", () => {
    const raw = JSON.parse(readFileSync(path.join(__dirname, "..", "e2e", "fixtures", "blast", "playback.json"), "utf-8"));
    raw.effects[0].radius = [1, 2];
    raw.effects[1].kind = "laser";
    expect(parsePlayback(raw).effects?.map((e) => e.name)).toEqual(["shock front", "accretion disk"]);
  });
});

describe("a three-frame playback", () => {
  it("keeps per-frame colours (not mistaken for one RGB triple)", () => {
    const raw = {
      version: 2, t: [0, 1, 2], unit_t: "second", series: {}, entities: [], tracks: [],
      effects: [{ name: "fx", kind: "glow", anchor: null, position: [0, 0, 0], description: "",
        radius: [1, 1, 1], intensity: [1, 1, 1], color: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] }],
    };
    const fx = parsePlayback(raw).effects?.[0];
    expect(fx?.color).toEqual([[1, 0, 0], [0, 1, 0], [0, 0, 1]]);
    expect(sampleEffect(prepareEffect(fx!), [0, 1, 2], 1).color).toEqual([0, 1, 0]);
  });
});
