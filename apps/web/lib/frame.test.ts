import { describe, expect, it } from "vitest";
import { runPositions, sweep, VIEW_DIR, viewFrom } from "./frame";
import { parsePlayback } from "./playback";

const pb = parsePlayback({
  version: 1, title: "drop", fidelity: "analytic", t: [0, 1, 2], unit_t: "second",
  series: { y: { unit: "meter", values: [20, 15, 0] }, still: { unit: "meter", values: [3, 3, 3] } },
  entities: [{ name: "ball", kind: "sphere", bound_state: ["y"] }, { name: "post", kind: "box", bound_state: ["still"] }],
  tracks: [
    { entity: "ball", channel: "position_y", state: "y", min: 0, max: 20, scale: 1 },
    { entity: "post", channel: "position_x", state: "still", min: 3, max: 3, scale: 1 },
  ],
});

describe("framing the whole run", () => {
  it("sweeps an object's box along its path", () => {
    const ball = { min: [-0.5, -0.5, -0.5] as [number, number, number], max: [0.5, 0.5, 0.5] as [number, number, number] };
    expect(sweep(ball, [[0, 20, 0], [0, 0, 0], [4, -1, 0]])).toEqual({ min: [-0.5, -1.5, -0.5], max: [4.5, 20.5, 0.5] });
    expect(sweep(ball, [])).toEqual(ball);
  });

  it("samples every moving entity over the whole run, ends included", () => {
    const paths = runPositions(pb, () => undefined, 3);
    const ys = (paths.get("ball") ?? []).map((p) => p[1]);
    expect(ys[0]).toBeCloseTo(20);
    expect(ys[ys.length - 1]).toBeCloseTo(0);
    expect(paths.get("ball")?.[0]?.[0]).toBeUndefined(); // x is never driven: the object keeps its own
  });

  it("looks from the default angle at the fitting distance", () => {
    expect(Math.hypot(...VIEW_DIR)).toBeCloseTo(1);
    const [x, y, z] = viewFrom([1, 2, 3], 10);
    expect(Math.hypot(x - 1, y - 2, z - 3)).toBeCloseTo(10);
    expect(y).toBeGreaterThan(2); // from above
  });
});
