import { describe, expect, it } from "vitest";
import { STEPS, buildProgress } from "./progress";

const states = (p: ReturnType<typeof buildProgress>) => Object.fromEntries(p.steps.map((s) => [s.id, s.state]));

describe("buildProgress", () => {
  it("starts at the idea before the first status arrives", () => {
    const p = buildProgress(null);
    expect(states(p).idea).toBe("current");
    expect(p.steps.filter((s) => s.state === "todo")).toHaveLength(STEPS.length - 1);
    expect(p.fraction).toBeGreaterThan(0);
  });

  it("marks earlier steps done and grows the fraction monotonically", () => {
    const order = ["interpreting_inputs", "clarifying", "researching", "modeling", "coding", "running", "evaluating", "packaging"] as const;
    let last = -1;
    for (const stage of order) {
      const p = buildProgress(stage);
      expect(p.fraction).toBeGreaterThan(last);
      last = p.fraction;
    }
    const s = states(buildProgress("running"));
    expect([s.idea, s.research, s.model, s.code]).toEqual(["done", "done", "done", "done"]);
    expect(s.run).toBe("current");
    expect(s.check).toBe("todo");
  });

  it("maps waiting for answers to the questions step", () => {
    const p = buildProgress("awaiting_clarification");
    expect(states(p).clarify).toBe("current");
    expect(p.headline).toMatch(/questions/i);
  });

  it("is fully coloured once ready or accepted", () => {
    const review = buildProgress("awaiting_review");
    expect(review.fraction).toBe(1);
    expect(states(review).ready).toBe("current");
    const done = buildProgress("approved");
    expect(done.steps.every((s) => s.state === "done")).toBe(true);
  });

  it("reports the self-correcting loop only inside it", () => {
    expect(buildProgress("coding", { revision: 1 }).looping).toBe(true);
    expect(buildProgress("coding", { revision: 0 }).looping).toBe(false);
    expect(buildProgress("packaging", { revision: 2 }).looping).toBe(false);
  });

  it("marks the step that failed", () => {
    const p = buildProgress("evaluating", { failed: true });
    expect(states(p).check).toBe("failed");
    expect(p.looping).toBe(false);
    expect(states(buildProgress("failed")).ready).toBe("failed");
  });
});
