import { describe, expect, it } from "vitest";
import { REPLAY_SECONDS, REPLAY_STAGES, replayStage } from "./replay";

describe("demo replay timeline", () => {
  it("walks the build stages in order, then ends", () => {
    expect(replayStage(0)).toBe("clarifying");
    expect(replayStage(2.6)).toBe("researching");
    let t = 0;
    for (const s of REPLAY_STAGES) {
      expect(replayStage(t + s.seconds / 2)).toBe(s.stage);
      t += s.seconds;
    }
    expect(replayStage(REPLAY_SECONDS)).toBeNull();
    expect(replayStage(Number.NaN)).toBe("clarifying");
    expect(REPLAY_SECONDS).toBeGreaterThan(20);
    expect(REPLAY_SECONDS).toBeLessThan(60);
  });
});
