import { describe, expect, it } from "vitest";
import { addOffset, dragOffset, entityOf, offsetLinks, type Offsets } from "./move";

describe("dragOffset", () => {
  it("moves on the ground plane by the pointer's travel", () => {
    expect(dragOffset([1, 0, 0], [2, 0.5, 2], [3, 0.5, 4], false)).toEqual([2, 0, 2]);
  });
  it("lifting changes height only", () => {
    expect(dragOffset([1, 0, 1], [0, 0, 0], [5, 2, 5], true)).toEqual([1, 2, 1]);
  });
  it("ignores height drift during a ground drag", () => {
    expect(dragOffset([0, 0.3, 0], [0, 0, 0], [1, 9, 1], false)).toEqual([1, 0.3, 1]);
  });
});

describe("offsetLinks", () => {
  const links = [
    { entity: "upper", from: [0, 0, 0] as [number, number, number], to: [0, -1, 0] as [number, number, number] },
    { entity: "lower", from: [0, -1, 0] as [number, number, number], to: [0, -2, 0] as [number, number, number] },
  ];
  const parent = (e: string) => (e === "lower" ? "upper" : null);

  it("is a no-op with nothing moved", () => {
    expect(offsetLinks(links, new Map(), parent)).toBe(links);
  });
  it("moves a link's free end with its entity and its pivot with the parent", () => {
    const offsets: Offsets = new Map([["upper", [1, 0, 0]]]);
    const [up, low] = offsetLinks(links, offsets, parent);
    expect(up?.from).toEqual([0, 0, 0]); // fixed pivot stays
    expect(up?.to).toEqual([1, -1, 0]);
    expect(low?.from).toEqual([1, -1, 0]); // child pivot follows the moved parent
    expect(low?.to).toEqual([0, -2, 0]);
  });
});

describe("entityOf", () => {
  it("walks up to the nearest entity", () => {
    const ball = { name: "ball", parent: { name: "Scene", parent: null } };
    const mesh = { name: "ball_mesh_0", parent: ball };
    expect(entityOf(mesh, (n) => n === "ball")).toBe("ball");
    expect(entityOf({ name: "ground", parent: null }, (n) => n === "ball")).toBeNull();
  });
});

describe("addOffset", () => {
  it("adds, or passes through without an offset", () => {
    expect(addOffset([1, 2, 3], [1, 1, 1])).toEqual([2, 3, 4]);
    expect(addOffset([1, 2, 3], undefined)).toEqual([1, 2, 3]);
  });
});
