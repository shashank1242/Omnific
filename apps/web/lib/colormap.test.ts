import { describe, expect, it } from "vitest";
import { colormap, colormapForUnit, normalize } from "./colormap";

describe("colormap", () => {
  it("hits endpoints", () => {
    expect(colormap("viridis", 0)[0]).toBeCloseTo(0x44 / 255);
    expect(colormap("inferno", 1)[0]).toBeCloseTo(0xfc / 255);
  });
  it("clamps and tolerates NaN", () => {
    expect(colormap("viridis", -5)).toEqual(colormap("viridis", 0));
    expect(colormap("viridis", 9)).toEqual(colormap("viridis", 1));
    expect(colormap("viridis", NaN)).toEqual(colormap("viridis", 0));
  });
  it("increases in luminance for inferno", () => {
    const lum = (c: number[]) => 0.2126 * (c[0] ?? 0) + 0.7152 * (c[1] ?? 0) + 0.0722 * (c[2] ?? 0);
    let prev = -1;
    for (let i = 0; i <= 10; i++) {
      const l = lum(colormap("inferno", i / 10));
      expect(l).toBeGreaterThan(prev);
      prev = l;
    }
  });
  it("normalizes with degenerate range", () => {
    expect(normalize(5, 0, 10)).toBe(0.5);
    expect(normalize(5, 3, 3)).toBe(0);
  });
  it("picks maps by unit", () => {
    expect(colormapForUnit("kelvin")).toBe("inferno");
    expect(colormapForUnit("meter")).toBe("viridis");
  });
});
