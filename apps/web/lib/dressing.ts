import * as THREE from "three";

/** Blender scene dressing (docs/playback-format.md, "scene.gltf v4: dressed scenes"). */

/** Root node holding everything Blender added; it is set dressing, never an entity. */
export const SCENERY_ROOT = "scenery";

/**
 * Blender exports lights photometrically (glTF KHR_lights_punctual: W x 683 lm/W, so lux / candela), while the viewer's
 * own rig uses small, Blender-like radiometric values. Scaling the scene's lights by 1/683 brings them back to the
 * strengths the dressing agent chose in Blender, on the same footing as the rig.
 */
export const LIGHT_SCALE = 1 / 683;

/** How strongly the viewer's own rig still shines when the dressed scene brings its own lights. */
export const RIG_FILL = 0.35;

export interface DressInfo { dressed: boolean; lighting: "scene" | "stage"; lights: number }

const NONE: DressInfo = { dressed: false, lighting: "stage", lights: 0 };

/** Reads `scenes[0].extras.omnific_scene` (three.js puts glTF extras in `userData`). */
export function dressInfo(userData: unknown): DressInfo {
  const raw = (userData as { omnific_scene?: unknown } | null | undefined)?.omnific_scene;
  if (!raw || typeof raw !== "object") return NONE;
  const o = raw as Record<string, unknown>;
  if (o.dressed !== true) return NONE;
  const lights = typeof o.lights === "number" && Number.isFinite(o.lights) ? Math.max(0, o.lights) : 0;
  return { dressed: true, lighting: o.lighting === "scene" && lights > 0 ? "scene" : "stage", lights };
}

/** True for anything inside the scenery root. */
export function isScenery(o: THREE.Object3D | null): boolean {
  for (let n: THREE.Object3D | null = o; n; n = n.parent) if (n.name === SCENERY_ROOT) return true;
  return false;
}

/** Scale the scenery's lights once (idempotent per light). Returns how many were calibrated. */
export function calibrateSceneLights(root: THREE.Object3D): number {
  let n = 0;
  root.traverse((o) => {
    const light = o as THREE.Light;
    if (!light.isLight || !isScenery(light) || light.userData.omnificCalibrated) return;
    light.intensity *= LIGHT_SCALE;
    light.userData.omnificCalibrated = true;
    n += 1;
  });
  return n;
}
