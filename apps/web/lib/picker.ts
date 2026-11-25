import type { AssetChoices, AssetOption, AssetPicks, EntityChoices, MeshRoute, PickRoute } from "./types";

/** What the user has chosen for one object: a type, and for the library a specific model. */
export interface Selection { route: PickRoute; candidateId: string }

export const ROUTE_LABEL: Record<PickRoute, string> = {
  auto: "Let Omnific choose",
  library: "Model library",
  cad: "CAD model",
  generated: "AI-generated",
  procedural: "Simple shape",
};

/** The options in the order the type control lists them. */
const ORDER: PickRoute[] = ["auto", "library", "cad", "generated", "procedural"];

export const optionId = (o: AssetOption) => `${o.candidate.source}/${o.candidate.id}`;

/** Types the user can pick for an object: always "auto"; the library only when there is a model to pick. */
export function routesFor(e: EntityChoices): PickRoute[] {
  const have = new Set<PickRoute>(["auto", ...e.routes_available]);
  if (!e.options.some((o) => o.status !== "failed")) have.delete("library");
  return ORDER.filter((r) => have.has(r));
}

/** Everything starts as "let Omnific choose". */
export function initialSelection(choices: AssetChoices): Record<string, Selection> {
  return Object.fromEntries(choices.entities.map((e) => [e.entity, { route: "auto", candidateId: "" }]));
}

/** Keep a selection valid as the options update (an option can fail its evaluation after it was chosen). */
export function reconcile(choices: AssetChoices, sel: Record<string, Selection>): Record<string, Selection> {
  const out: Record<string, Selection> = {};
  for (const e of choices.entities) {
    const s = sel[e.entity] ?? { route: "auto", candidateId: "" };
    if (s.route === "library") {
      const opt = e.options.find((o) => optionId(o) === s.candidateId);
      const ok = opt && opt.status !== "failed" && routesFor(e).includes("library");
      out[e.entity] = ok ? s : { route: "auto", candidateId: "" };
    } else {
      out[e.entity] = routesFor(e).includes(s.route) ? s : { route: "auto", candidateId: "" };
    }
  }
  return out;
}

/** The POST body: only real choices ("auto" is the default and is left out). */
export function buildPicks(choices: AssetChoices, sel: Record<string, Selection>): AssetPicks {
  const valid = reconcile(choices, sel);
  return {
    picks: choices.entities.flatMap((e) => {
      const s = valid[e.entity];
      if (!s || s.route === "auto") return [];
      return [{ entity: e.entity, route: s.route, candidate_id: s.route === "library" ? s.candidateId : "" }];
    }),
  };
}

export interface Badge { text: string; tone: "ok" | "bad" | "muted" }

/** The evaluator's verdict on an option, in plain words. */
export function badge(o: AssetOption, defaultRoute: MeshRoute): Badge {
  if (o.status === "passed") return { text: "Passed every check", tone: "ok" };
  if (o.status === "failed") return { text: `Failed a check: ${o.detail}`, tone: "bad" };
  return defaultRoute === "library"
    ? { text: "Checking…", tone: "muted" }
    : { text: "Checked when you pick it", tone: "muted" };
}

/** True when there is something to choose at all (otherwise the picker stays hidden). */
export function hasChoices(choices: AssetChoices | null | undefined): boolean {
  return !!choices && choices.entities.some((e) => routesFor(e).length > 1);
}
