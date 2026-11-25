// The objects in a simulation and what belongs to each: the parameters that describe it (mass, radius, friction…)
// and the states that move it. Specs from before `Entity.properties` existed fall back to matching names.
import type { Parameter, SimSpec, StateVariable } from "./types";

export interface SceneObject {
  name: string;
  kind: string;
  properties: Parameter[];
  states: StateVariable[];
}

export interface ObjectGroups {
  objects: SceneObject[];
  /** Parameters that belong to no single object (g, air density, time constants…). */
  shared: Parameter[];
}

const tokens = (s: string) => s.toLowerCase().split(/[^a-z0-9]+/).filter((t) => t.length > 1);

/** A parameter mentions an object when one of its name tokens is one of the object's name or kind tokens. */
function mentions(p: Parameter, name: string, kind: string): boolean {
  const own = new Set([...tokens(name), ...tokens(kind)]);
  return tokens(p.name).some((t) => own.has(t));
}

export function objectsOf(spec: SimSpec): ObjectGroups {
  const byName = new Map(spec.parameters.map((p) => [p.name, p]));
  const stateByName = new Map(spec.state.map((s) => [s.name, s]));
  const claimed = new Set<string>();
  const declared = spec.entities.some((e) => (e.properties ?? []).length > 0);
  const objects = spec.entities.map((e) => {
    const names = declared
      ? (e.properties ?? []).filter((n) => byName.has(n))
      : spec.parameters.filter((p) => mentions(p, e.name, e.kind)).map((p) => p.name);
    const properties = names.filter((n) => !claimed.has(n)).map((n) => byName.get(n) as Parameter);
    properties.forEach((p) => claimed.add(p.name));
    const states = e.bound_state.map((n) => stateByName.get(n)).filter((s): s is StateVariable => !!s);
    return { name: e.name, kind: e.kind, properties, states };
  });
  return { objects, shared: spec.parameters.filter((p) => !claimed.has(p.name)) };
}
