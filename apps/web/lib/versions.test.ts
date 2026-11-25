import { describe, expect, it } from "vitest";
import type { VersionInfo } from "./types";
import { hasPending, isSettled, stageWords, versionChips } from "./versions";

const v = (n: number, over: Partial<VersionInfo> = {}): VersionInfo => ({
  job_id: `job${n}`, version: n, request: n === 1 ? "" : `change ${n}`, summary: "", kind: n === 1 ? null : "visual",
  stage: "approved", live: false, created_at: "2026-11-25T00:00:00Z", ...over,
});

describe("versionChips", () => {
  it("shows nothing for a job without edits", () => {
    expect(versionChips([v(1, { live: true })], "job1")).toEqual([]);
  });

  it("orders by version and marks the current and live ones", () => {
    const chips = versionChips([v(3, { live: true }), v(1), v(2)], "job2");
    expect(chips.map((c) => c.label)).toEqual(["v1", "v2", "v3"]);
    expect(chips.map((c) => c.current)).toEqual([false, true, false]);
    expect(chips.map((c) => c.live)).toEqual([false, false, true]);
  });

  it("describes a running version in words and flags a failed one", () => {
    const chips = versionChips([v(1), v(2, { stage: "modeling" }), v(3, { stage: "failed" })], "job1");
    expect(chips[1]?.progress).toBe("modelling");
    expect(chips[0]?.progress).toBeNull();
    expect(chips[2]).toMatchObject({ failed: true, progress: null });
  });

  it("titles a chip with the request", () => {
    const chips = versionChips([v(1), v(2, { summary: "red car" })], "job1");
    expect(chips[0]?.title).toBe("The first version");
    expect(chips[1]?.title).toBe("change 2: red car");
  });
});

describe("settling", () => {
  it("polls until every version is accepted or failed", () => {
    expect(hasPending([v(1), v(2)])).toBe(false);
    expect(hasPending([v(1), v(2, { stage: "failed" })])).toBe(false);
    expect(hasPending([v(1), v(2, { stage: "awaiting_review" })])).toBe(true);
    expect(isSettled("running")).toBe(false);
  });

  it("has words for the new planning stage", () => {
    expect(stageWords("planning_edit")).toMatch(/plan/);
  });
});
