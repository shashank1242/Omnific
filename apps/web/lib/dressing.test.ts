import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { LIGHT_SCALE, SCENERY_ROOT, calibrateSceneLights, dressInfo, isScenery } from "./dressing";

describe("dressed scenes", () => {
  it("reads the scene extras and ignores anything else", () => {
    expect(dressInfo(undefined).dressed).toBe(false);
    expect(dressInfo({ omnific_scene: { dressed: false } }).dressed).toBe(false);
    expect(dressInfo({ omnific_scene: { dressed: true, lighting: "scene", lights: 2 } }))
      .toEqual({ dressed: true, lighting: "scene", lights: 2 });
    // "scene" lighting without any light falls back to the viewer's rig
    expect(dressInfo({ omnific_scene: { dressed: true, lighting: "scene", lights: 0 } }).lighting).toBe("stage");
    expect(dressInfo({ omnific_scene: { dressed: true, lights: "3" } }).lights).toBe(0);
  });

  it("calibrates only the scenery's lights, once", () => {
    const root = new THREE.Group();
    const scenery = new THREE.Group();
    scenery.name = SCENERY_ROOT;
    const lamp = new THREE.PointLight(0xffffff, 683 * 500);
    const holder = new THREE.Group();
    holder.add(lamp);
    scenery.add(holder);
    const entityGlow = new THREE.PointLight(0xffffff, 3);
    const ball = new THREE.Mesh();
    ball.name = "ball";
    ball.add(entityGlow);
    root.add(scenery, ball);

    expect(calibrateSceneLights(root)).toBe(1);
    expect(lamp.intensity).toBeCloseTo(500, 6);
    expect(entityGlow.intensity).toBe(3);
    expect(calibrateSceneLights(root)).toBe(0); // a second pass (re-render, clone) never scales twice
    expect(lamp.intensity).toBeCloseTo(500, 6);
    expect(LIGHT_SCALE).toBeCloseTo(1 / 683, 12);
  });

  it("knows what is scenery", () => {
    const scenery = new THREE.Group();
    scenery.name = SCENERY_ROOT;
    const crate = new THREE.Mesh();
    scenery.add(crate);
    expect(isScenery(crate)).toBe(true);
    expect(isScenery(new THREE.Mesh())).toBe(false);
  });
});
