import { describe, expect, it } from "vitest";
import { badge, buildPicks, hasChoices, initialSelection, reconcile, routesFor } from "./picker";
import type { AssetChoices, AssetOption, EntityChoices } from "./types";

const opt = (id: string, status: AssetOption["status"] = "shortlisted"): AssetOption => ({
  candidate: { source: "fake", id, name: `Car ${id}`, license: "CC0 1.0", author: "ann", url: "u", tris: 1000, animated: false },
  thumb_key: `jobs/j/choices/car/${id}.png`, score: 8, reason: "", status, detail: status === "failed" ? "not a car" : "",
});

const car: EntityChoices = {
  entity: "car", kind: "car", default_route: "library", routes_available: ["library", "cad", "procedural"],
  query: "red car", options: [opt("1", "failed"), opt("2", "passed"), opt("3")], note: "",
};
const ball: EntityChoices = {
  entity: "ball", kind: "sphere", default_route: "procedural", routes_available: ["library", "procedural"],
  query: "ball", options: [], note: "nothing found",
};
const choices: AssetChoices = { entities: [car, ball], ready: false };

describe("asset picker state", () => {
  it("offers only the types that exist", () => {
    expect(routesFor(car)).toEqual(["auto", "library", "cad", "procedural"]);
    expect(routesFor(ball)).toEqual(["auto", "procedural"]); // no model found: no library type
    expect(hasChoices(choices)).toBe(true);
    expect(hasChoices({ entities: [{ ...ball, routes_available: [] }], ready: true })).toBe(false);
    expect(hasChoices(null)).toBe(false);
  });

  it("starts on auto and sends only real choices", () => {
    const sel = initialSelection(choices);
    expect(buildPicks(choices, sel)).toEqual({ picks: [] });
    sel.car = { route: "library", candidateId: "fake/3" };
    sel.ball = { route: "procedural", candidateId: "ignored" };
    expect(buildPicks(choices, sel).picks).toEqual([
      { entity: "car", route: "library", candidate_id: "fake/3" },
      { entity: "ball", route: "procedural", candidate_id: "" },
    ]);
  });

  it("drops a chosen model that later fails its evaluation", () => {
    const sel = { car: { route: "library" as const, candidateId: "fake/3" }, ball: { route: "auto" as const, candidateId: "" } };
    const later: AssetChoices = { entities: [{ ...car, options: [opt("1", "failed"), opt("2", "passed"), opt("3", "failed")] }, ball], ready: true };
    expect(reconcile(later, sel).car).toEqual({ route: "auto", candidateId: "" });
    expect(reconcile(choices, { ...sel, ball: { route: "cad", candidateId: "" } }).ball?.route).toBe("auto");
    // the library route itself went away: a library pick can't stand either
    const noLib: AssetChoices = { entities: [{ ...car, routes_available: ["procedural"] }, ball], ready: true };
    expect(reconcile(noLib, { ...sel, car: { route: "library", candidateId: "fake/2" } }).car?.route).toBe("auto");
  });

  it("says what the evaluator found", () => {
    expect(badge(opt("2", "passed"), "library")).toEqual({ text: "Passed every check", tone: "ok" });
    expect(badge(opt("1", "failed"), "library").tone).toBe("bad");
    expect(badge(opt("3"), "library").text).toBe("Checking…");
    expect(badge(opt("3"), "procedural").text).toBe("Checked when you pick it");
  });
});
