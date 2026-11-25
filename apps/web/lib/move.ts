// Moving objects in the viewer: each moved entity gets a world-space offset layered on top of its simulated pose,
// so it keeps its motion while sitting somewhere else. Reset clears the offsets. View-only: the simulation's
// numbers don't change.
import type { Articulation } from "./articulation";

export type Vec = [number, number, number];
export type Offsets = Map<string, Vec>;

/** New offset while dragging: the start offset plus how far the pointer's hit point moved.
 *  Lifting (vertical drag) keeps only the height change; a normal drag stays on the horizontal plane. */
export function dragOffset(origin: Vec, start: Vec, hit: Vec, lift: boolean): Vec {
  const d: Vec = [hit[0] - start[0], hit[1] - start[1], hit[2] - start[2]];
  return lift ? [origin[0], origin[1] + d[1], origin[2]] : [origin[0] + d[0], origin[1], origin[2] + d[2]];
}

export function addOffset(p: Vec, o: Vec | undefined): Vec {
  return o ? [p[0] + o[0], p[1] + o[1], p[2] + o[2]] : p;
}

/** Links follow the moved objects: the free end with the entity, the pivot with its parent entity. */
export function offsetLinks(links: Articulation["links"], offsets: Offsets, parentOf: (entity: string) => string | null | undefined): Articulation["links"] {
  if (offsets.size === 0) return links;
  return links.map((l) => {
    const parent = parentOf(l.entity);
    return { ...l, from: addOffset(l.from, parent ? offsets.get(parent) : undefined), to: addOffset(l.to, offsets.get(l.entity)) };
  });
}

/** The entity a picked mesh belongs to: the nearest ancestor (or itself) that is a scene entity. */
export function entityOf(node: { name: string; parent: unknown } | null, isEntity: (name: string) => boolean): string | null {
  let n = node as { name: string; parent: unknown } | null;
  while (n) {
    if (isEntity(n.name)) return n.name;
    n = n.parent as { name: string; parent: unknown } | null;
  }
  return null;
}
