"use client";

import { Component, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import * as THREE from "three";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Bounds, Grid, OrbitControls, useBounds, useGLTF } from "@react-three/drei";
import { clone as cloneSkinned } from "three/examples/jsm/utils/SkeletonUtils.js";
import { advancePhase, animationName, clipRate, driveReference, parseClipExtras } from "@/lib/clips";
import { colormap, normalize } from "@/lib/colormap";
import Icon from "./Icon";
import { dragOffset, entityOf, offsetLinks, type Offsets, type Vec } from "@/lib/move";
import { sampleAlong } from "@/lib/playback";
import { evaluateScene, legends, type EntityPose } from "@/lib/tracks";
import { runPositions, sweep, viewFrom, type Box, type V3 } from "@/lib/frame";
import type { Playback, PlaybackEnvironment } from "@/lib/types";
import { RIG_FILL, SCENERY_ROOT, calibrateSceneLights, dressInfo, type DressInfo } from "@/lib/dressing";
import { gradientCss } from "@/lib/colormap";
import type { Player } from "./player";

interface Base { position: THREE.Vector3; rotation: THREE.Euler; scale: THREE.Vector3 }

function useDark(): boolean {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setDark(mq.matches);
    const on = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return dark;
}

function meshesOf(node: THREE.Object3D): THREE.Mesh[] {
  const out: THREE.Mesh[] = [];
  node.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) out.push(o as THREE.Mesh);
  });
  return out;
}

function applyColor(node: THREE.Object3D, rgb: [number, number, number]) {
  for (const m of meshesOf(node)) {
    const mat = m.material as THREE.Material & { color?: THREE.Color };
    if (mat.color) mat.color.setRGB(rgb[0], rgb[1], rgb[2], THREE.SRGBColorSpace);
  }
}

function applyColorField(node: THREE.Object3D, cf: NonNullable<EntityPose["colorField"]>) {
  for (const m of meshesOf(node)) {
    const geo = m.geometry;
    const pos = geo.getAttribute("position");
    if (!pos) continue;
    if (!geo.boundingBox) geo.computeBoundingBox();
    const bb = geo.boundingBox as THREE.Box3;
    const size = bb.getSize(new THREE.Vector3());
    const axis: "x" | "y" | "z" = size.x >= size.y && size.x >= size.z ? "x" : size.y >= size.z ? "y" : "z";
    const span = size[axis] || 1;
    let attr = geo.getAttribute("color") as THREE.BufferAttribute | undefined;
    if (!attr || attr.count !== pos.count) {
      attr = new THREE.BufferAttribute(new Float32Array(pos.count * 3), 3);
      geo.setAttribute("color", attr);
    }
    const tmp = new THREE.Color();
    for (let i = 0; i < pos.count; i++) {
      const u = ((pos.getX(i) * (axis === "x" ? 1 : 0) + pos.getY(i) * (axis === "y" ? 1 : 0) + pos.getZ(i) * (axis === "z" ? 1 : 0)) - bb.min[axis]) / span;
      const v = sampleAlong(cf.values, u);
      const [r, g, b] = colormap(cf.map, normalize(v, cf.min, cf.max));
      tmp.setRGB(r, g, b, THREE.SRGBColorSpace);
      attr.setXYZ(i, tmp.r, tmp.g, tmp.b);
    }
    attr.needsUpdate = true;
    const mat = m.material as THREE.MeshStandardMaterial;
    if (!mat.vertexColors) {
      mat.vertexColors = true;
      mat.color?.set(0xffffff);
      mat.needsUpdate = true;
    }
  }
}

interface Drag { name: string; plane: THREE.Plane; start: Vec; origin: Vec; lift: boolean; x: number; y: number }
const CLICK_PX = 5; // a press that moves less than this is a click (select), not a drag
const SELECT_GLOW = new THREE.Color("#ff4d8a"); // the "current" gel

const GHOSTS = 8; // exposures drawn per moving object
const GEL_VARS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6"];

function SceneModel({ url, playback, player, dark, offsets, onMoved, strobe, selected, onSelect, runBox, onDress }: {
  url: string; playback: Playback; player: Player; dark: boolean; offsets: RefObject<Offsets>; onMoved: () => void;
  runBox: RefObject<(() => THREE.Box3) | null>; onDress?: (d: DressInfo) => void;
  selected: string | null; onSelect: (name: string | null) => void;
  strobe: boolean;
}) {
  const gltf = useGLTF(url);
  // Own copy so cloned materials never leak into the drei cache. SkeletonUtils keeps skinned meshes bound to the
  // cloned bones (Object3D.clone would leave them pointing at the cached skeleton).
  const root = useMemo(() => {
    const clone = cloneSkinned(gltf.scene);
    clone.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) {
        m.material = Array.isArray(m.material) ? m.material.map((x) => x.clone()) : m.material.clone();
        m.geometry = m.geometry.clone();
      }
    });
    // a Blender-dressed scene brings its own lights, exported in photometric units: bring them to the rig's scale
    if (dressInfo(gltf.scene.userData).dressed) calibrateSceneLights(clone);
    return clone;
  }, [gltf.scene]);
  useEffect(() => { onDress?.(dressInfo(gltf.scene.userData)); }, [gltf.scene, onDress]);

  // Secondary-motion clips (scene.gltf v3 extras): phase follows simulation time, rate follows the drive state.
  const clips = useMemo(() => {
    const mixer = new THREE.AnimationMixer(root);
    const list: { action: THREE.AnimationAction; rate: (t: number) => number; duration: number; phase: number }[] = [];
    root.traverse((node) => {
      for (const spec of parseClipExtras(node.name, node.userData)) {
        const clip = gltf.animations.find((a) => a.name === animationName(spec));
        if (!clip) continue;
        const action = mixer.clipAction(clip);
        action.play();
        action.paused = true; // driven from simulation time in apply()
        const ref = driveReference(playback, spec.driveState);
        list.push({ action, rate: (t) => clipRate(playback, spec, t, ref), duration: clip.duration, phase: 0 });
      }
    });
    return { mixer, list, last: null as number | null };
  }, [root, gltf.animations, playback]);
  useEffect(() => () => { clips.mixer.stopAllAction(); }, [clips]);

  const bases = useMemo(() => {
    const map = new Map<string, { node: THREE.Object3D; base: Base }>();
    for (const e of playback.entities.length ? playback.entities.map((x) => x.name) : [...new Set(playback.tracks.map((t) => t.entity))]) {
      const node = root.getObjectByName(e);
      if (node) map.set(e, { node, base: { position: node.position.clone(), rotation: node.rotation.clone(), scale: node.scale.clone() } });
    }
    return map;
  }, [root, playback]);

  // One link cylinder + pivot marker per articulated entity, created once per playback.
  const linkGroup = useMemo(() => {
    const g = new THREE.Group();
    for (const e of playback.entities) {
      if (!e.arm) continue;
      const link = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 12), new THREE.MeshBasicMaterial());
      link.name = `link:${e.name}`;
      const pivot = new THREE.Mesh(new THREE.SphereGeometry(0.09, 12, 12), new THREE.MeshBasicMaterial());
      pivot.name = `pivot:${e.name}`;
      g.add(link, pivot);
    }
    return g;
  }, [playback]);
  useEffect(() => {
    const color = new THREE.Color(dark ? "#aab6c6" : "#4a5565");
    for (const o of linkGroup.children) ((o as THREE.Mesh).material as THREE.MeshBasicMaterial).color.copy(color);
  }, [linkGroup, dark]);
  useEffect(() => () => {
    for (const o of linkGroup.children) {
      const m = o as THREE.Mesh;
      m.geometry.dispose();
      (m.material as THREE.Material).dispose();
    }
  }, [linkGroup]);

  const baseOf = useMemo(() => (name: string): [number, number, number] | undefined => {
    const p = bases.get(name)?.base.position;
    return p ? [p.x, p.y, p.z] : undefined;
  }, [bases]);
  const up = useMemo(() => new THREE.Vector3(0, 1, 0), []);
  const dir = useMemo(() => new THREE.Vector3(), []);
  // Strobe: translucent gel-coloured copies of each moving object at earlier instants (a multiple exposure).
  const ghosts = useMemo(() => {
    const group = new THREE.Group();
    const list: { name: string; k: number; node: THREE.Object3D; mats: THREE.MeshBasicMaterial[] }[] = [];
    if (!strobe) return { group, list };
    const css = getComputedStyle(document.documentElement);
    const moving = [...new Set(playback.tracks.filter((t) => t.channel.startsWith("position") || t.channel === "rotation_z").map((t) => t.entity)),
      ...playback.entities.filter((e) => e.arm).map((e) => e.name)];
    [...new Set(moving)].forEach((name, i) => {
      const src = bases.get(name)?.node;
      if (!src) return;
      const color = new THREE.Color(css.getPropertyValue(GEL_VARS[i % GEL_VARS.length] ?? "--c1").trim() || "#ff4d8a");
      for (let k = 1; k <= GHOSTS; k++) {
        const node = src.clone(true);
        const mats: THREE.MeshBasicMaterial[] = [];
        node.traverse((o) => {
          const m = o as THREE.Mesh;
          if (!m.isMesh) return;
          // full gel colour, fading with age; normal blending keeps the hue over light scene geometry too
          const mat = new THREE.MeshBasicMaterial({
            color, transparent: true, depthWrite: false,
            opacity: 0.9 - (0.55 * (k - 1)) / (GHOSTS - 1),
          });
          m.material = mat;
          mats.push(mat);
        });
        node.visible = false;
        group.add(node);
        list.push({ name, k, node, mats });
      }
    });
    return { group, list };
  }, [strobe, bases, playback, dark]); // eslint-disable-line react-hooks/exhaustive-deps -- dark: gel colours are read from CSS and differ per theme
  useEffect(() => () => { for (const g of ghosts.list) g.mats.forEach((m) => m.dispose()); }, [ghosts]);

  const parentOf = useMemo(() => {
    const m = new Map(playback.entities.map((e) => [e.name, e.parent ?? null]));
    return (name: string) => m.get(name);
  }, [playback]);
  const local = useMemo(() => ({ m: new THREE.Matrix3(), v: new THREE.Vector3() }), []);
  /** A world-space offset as a delta in the node's parent space (identity unless the scene root is transformed). */
  const offsetIn = (node: THREE.Object3D, o: Vec): THREE.Vector3 => {
    local.v.set(o[0], o[1], o[2]);
    if (node.parent) local.v.applyMatrix3(local.m.setFromMatrix4(node.parent.matrixWorld).invert());
    return local.v;
  };

  const placeGhosts = (time: number) => {
    if (ghosts.list.length === 0) return;
    const t0 = playback.t[0] ?? 0;
    const span = (playback.t[playback.t.length - 1] ?? 0) - t0;
    // playing: a trail of exposures behind the object over half the run; paused: the whole run as one photograph
    const playing = player.state.playing;
    const at = (k: number) => (playing ? time - (k * span) / (2 * GHOSTS) : t0 + span - ((k - 1) * span) / (GHOSTS - 1));
    const moved = offsets.current;
    const frames = new Map<number, ReturnType<typeof evaluateScene>["poses"]>();
    for (const g of ghosts.list) {
      const t = at(g.k);
      if (t < t0 || (!playing && Math.abs(t - time) < span / (4 * GHOSTS))) { g.node.visible = false; continue; }
      let poses = frames.get(g.k);
      if (!poses) { poses = evaluateScene(playback, t, baseOf).poses; frames.set(g.k, poses); }
      const pose = poses.get(g.name);
      const base = bases.get(g.name)?.base;
      if (!pose || !base) { g.node.visible = false; continue; }
      g.node.visible = true;
      g.node.position.set(pose.position.x ?? base.position.x, pose.position.y ?? base.position.y, pose.position.z ?? base.position.z);
      const o = moved.get(g.name);
      if (o) g.node.position.add(offsetIn(g.node, o));
      g.node.rotation.set(base.rotation.x, base.rotation.y, pose.rotationZ ?? base.rotation.z);
      if (pose.scale !== undefined) g.node.scale.setScalar(pose.scale);
    }
  };

  const apply = (time: number) => {
    placeGhosts(time);
    if (clips.list.length) {
      const dt = clips.last === null ? 0 : time - clips.last;
      clips.last = time;
      for (const c of clips.list) {
        c.phase = advancePhase(c.phase, dt, c.rate(time), c.duration);
        c.action.time = c.phase;
      }
      clips.mixer.update(0);
    }
    const { poses, links: rawLinks } = evaluateScene(playback, time, baseOf);
    const moved = offsets.current;
    const links = offsetLinks(rawLinks, moved, parentOf);
    for (const l of links) {
      const link = linkGroup.getObjectByName(`link:${l.entity}`);
      const pivot = linkGroup.getObjectByName(`pivot:${l.entity}`);
      if (!link || !pivot) continue;
      dir.set(l.to[0] - l.from[0], l.to[1] - l.from[1], l.to[2] - l.from[2]);
      const len = dir.length();
      link.position.set((l.from[0] + l.to[0]) / 2, (l.from[1] + l.to[1]) / 2, (l.from[2] + l.to[2]) / 2);
      if (len > 1e-9) link.quaternion.setFromUnitVectors(up, dir.divideScalar(len));
      link.scale.set(0.03, len, 0.03);
      pivot.position.set(l.from[0], l.from[1], l.from[2]);
    }
    for (const [name, { node, base }] of bases) {
      const pose = poses.get(name);
      const o = moved.get(name);
      if (!pose && !o) {
        if (!node.position.equals(base.position)) node.position.copy(base.position); // static object after a reset
        continue;
      }
      node.position.set(pose?.position.x ?? base.position.x, pose?.position.y ?? base.position.y, pose?.position.z ?? base.position.z);
      if (o) node.position.add(offsetIn(node, o));
      if (!pose) continue;
      node.rotation.set(base.rotation.x, base.rotation.y, pose.rotationZ ?? base.rotation.z);
      if (pose.scale !== undefined) node.scale.setScalar(pose.scale);
      if (pose.color) applyColor(node, pose.color);
      if (pose.colorField) applyColorField(node, pose.colorField);
    }
  };

  // Dragging: grab an object and move it on the ground plane; hold Shift to lift it instead.
  const { camera, gl, controls } = useThree() as unknown as {
    camera: THREE.Camera; gl: THREE.WebGLRenderer; controls: { enabled: boolean } | null;
  };
  const drag = useRef<Drag | null>(null);
  const ray = useMemo(() => ({ caster: new THREE.Raycaster(), ndc: new THREE.Vector2(), hit: new THREE.Vector3() }), []);
  const isEntity = useCallback((n: string) => bases.has(n), [bases]);

  const onMove = useCallback((ev: PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const r = gl.domElement.getBoundingClientRect();
    ray.ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    ray.caster.setFromCamera(ray.ndc, camera);
    if (!ray.caster.ray.intersectPlane(d.plane, ray.hit)) return;
    offsets.current.set(d.name, dragOffset(d.origin, d.start, [ray.hit.x, ray.hit.y, ray.hit.z], d.lift));
  }, [camera, gl, offsets, ray]);

  const onUp = useCallback((ev?: PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    if (ev && Math.hypot(ev.clientX - d.x, ev.clientY - d.y) < CLICK_PX) {
      offsets.current.set(d.name, d.origin); // a click: undo any sub-pixel move and select the object
      if (d.origin.every((v) => v === 0)) offsets.current.delete(d.name);
      onSelect(d.name);
    }
    if (controls) controls.enabled = true;
    gl.domElement.style.cursor = "";
    window.removeEventListener("pointermove", onMove);
    onMoved();
  }, [controls, gl, onMove, onMoved, onSelect, offsets]);
  useEffect(() => () => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  }, [onMove, onUp]);

  const onDown = (e: ThreeEvent<PointerEvent>) => {
    const name = entityOf(e.object, isEntity);
    if (!name || e.button !== 0) return;
    e.stopPropagation();
    const lift = e.shiftKey;
    const normal = lift ? camera.getWorldDirection(new THREE.Vector3()).setY(0) : new THREE.Vector3(0, 1, 0);
    if (normal.lengthSq() < 1e-9) normal.set(0, 0, 1); // looking straight down while lifting
    drag.current = {
      name, lift,
      plane: new THREE.Plane().setFromNormalAndCoplanarPoint(normal.normalize(), e.point),
      start: [e.point.x, e.point.y, e.point.z],
      origin: offsets.current.get(name) ?? [0, 0, 0],
      x: e.nativeEvent.clientX, y: e.nativeEvent.clientY,
    };
    if (controls) controls.enabled = false;
    gl.domElement.style.cursor = "grabbing";
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  };
  const onOver = (e: ThreeEvent<PointerEvent>) => {
    if (!drag.current && entityOf(e.object, isEntity)) gl.domElement.style.cursor = "grab";
  };
  const onOut = () => { if (!drag.current) gl.domElement.style.cursor = ""; };

  // the selected object glows in the "current" gel; everything else keeps its own emissive
  useEffect(() => {
    for (const [name, { node }] of bases) {
      node.traverse((o) => {
        const m = o as THREE.Mesh;
        if (!m.isMesh) return;
        for (const mat of (Array.isArray(m.material) ? m.material : [m.material]) as (THREE.Material & { emissive?: THREE.Color; emissiveIntensity?: number })[]) {
          if (!mat.emissive) continue;
          const ud = mat.userData as { baseEmissive?: THREE.Color; baseIntensity?: number };
          if (!ud.baseEmissive) { ud.baseEmissive = mat.emissive.clone(); ud.baseIntensity = mat.emissiveIntensity ?? 1; }
          if (name === selected) { mat.emissive.copy(SELECT_GLOW); mat.emissiveIntensity = dark ? 0.55 : 0.4; }
          else { mat.emissive.copy(ud.baseEmissive); mat.emissiveIntensity = ud.baseIntensity ?? 1; }
        }
      });
    }
  }, [bases, selected, dark]);

  useLayoutEffect(() => apply(player.state.time));
  useFrame(() => apply(player.state.time));
  // The whole run's box: everything where it is now, plus each moving object swept along its path (the strobe ghosts
  // and drag offsets are left out), so a reset frames the entire simulation rather than one instant of it.
  useEffect(() => {
    runBox.current = () => {
      root.updateWorldMatrix(true, true);
      const all = new THREE.Box3();
      for (const child of root.children) if (child.name !== SCENERY_ROOT) all.expandByObject(child); // frame the run, not the set
      const paths = runPositions(playback, baseOf);
      const local = new THREE.Vector3();
      const world = new THREE.Vector3();
      for (const [name, { node, base }] of bases) {
        const path = paths.get(name);
        if (!path?.length) continue;
        const nb = new THREE.Box3().setFromObject(node);
        if (nb.isEmpty()) continue;
        const parent = node.parent ?? root;
        const now = parent.localToWorld(node.position.clone());
        const deltas: V3[] = path.map(([x, y, z]) => {
          local.set(x ?? base.position.x, y ?? base.position.y, z ?? base.position.z);
          world.copy(parent.localToWorld(local.clone())).sub(now);
          return [world.x, world.y, world.z];
        });
        const swept: Box = sweep({ min: nb.min.toArray() as V3, max: nb.max.toArray() as V3 }, deltas);
        all.union(new THREE.Box3(new THREE.Vector3(...swept.min), new THREE.Vector3(...swept.max)));
      }
      return all;
    };
    return () => { runBox.current = null; };
  }, [root, bases, playback, baseOf, runBox]);

  return (
    <>
      <primitive object={root} onPointerDown={onDown} onPointerOver={onOver} onPointerOut={onOut} />
      <primitive object={linkGroup} />
      <primitive object={ghosts.group} />
    </>
  );
}

class Boundary extends Component<{ children: ReactNode; onError: (m: string) => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(err: Error) {
    this.props.onError(err.message);
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** Grid lines tinted from the ground so they read on any ground colour. */
function gridOn(ground: string, amount: number): string {
  const c = new THREE.Color(ground);
  const hsl = { h: 0, s: 0, l: 0 };
  c.getHSL(hsl);
  return `#${c.offsetHSL(0, 0, hsl.l > 0.5 ? -amount : amount).getHexString()}`;
}

const LIGHT_RIGS: Record<PlaybackEnvironment["lighting"], { sun: [number, number, number]; color: string; sunI: number; skyI: number }> = {
  day: { sun: [8, 14, 6], color: "#fff6e8", sunI: 2.4, skyI: 1.1 },
  dusk: { sun: [12, 3, -4], color: "#ffb070", sunI: 1.8, skyI: 0.6 },
  night: { sun: [-6, 10, 4], color: "#9fb4ff", sunI: 0.6, skyI: 0.35 },
  studio: { sun: [6, 10, 8], color: "#ffffff", sunI: 2.0, skyI: 1.0 },
  space: { sun: [14, 6, 10], color: "#ffffff", sunI: 3.0, skyI: 0.08 },
};

/** The setting: sky, ground, fog and a light rig, all under the entities. */
function Stage({ env, fill = 1 }: { env: PlaybackEnvironment; fill?: number }) {
  const rig = LIGHT_RIGS[env.lighting];
  return (
    <>
      <color attach="background" args={[env.sky_color]} />
      {env.fog > 0 ? <fog attach="fog" args={[env.sky_color, 4 + 30 * (1 - env.fog), 20 + 120 * (1 - env.fog)]} /> : null}
      <hemisphereLight args={[env.sky_color, env.ground_color, rig.skyI * fill]} />
      <directionalLight position={rig.sun} color={rig.color} intensity={rig.sunI * fill} />
      <directionalLight position={[-6, 4, -6]} intensity={(env.lighting === "space" ? 0.05 : 0.35) * fill} />
      {env.ground === "flat" ? (
        <mesh rotation-x={-Math.PI / 2} position={[0, -0.001, 0]} receiveShadow>
          <circleGeometry args={[120, 64]} />
          <meshStandardMaterial color={env.ground_color} roughness={1} metalness={0} />
        </mesh>
      ) : null}
    </>
  );
}

const THUMB_WIDTH = 640;
const noop = () => undefined;

/** Registers a function that frames the whole run from the default angle (the view it opened with), and frames it
 *  once when the scene first appears. */
function Refit({ target, runBox }: { target: RefObject<(() => void) | null>; runBox: RefObject<(() => THREE.Box3) | null> }) {
  const bounds = useBounds();
  useEffect(() => {
    const fit = () => {
      const box = runBox.current?.();
      if (box && !box.isEmpty()) bounds.refresh(box);
      else bounds.refresh();
      const { center, distance } = bounds.getSize();
      bounds.moveTo(new THREE.Vector3(...viewFrom(center.toArray() as V3, distance))).lookAt({ target: center });
      bounds.clip();
    };
    target.current = fit;
    // once the first frame has posed the objects
    const id = requestAnimationFrame(() => requestAnimationFrame(fit));
    return () => { cancelAnimationFrame(id); target.current = null; };
  }, [bounds, target, runBox]);
  return null;
}

/** Registers a function that renders the current view and returns it as a small JPEG data URL. */
function Capture({ target }: { target: RefObject<(() => string | null) | null> }) {
  const { gl, scene, camera } = useThree();
  useEffect(() => {
    target.current = () => {
      gl.render(scene, camera); // draw now: without preserveDrawingBuffer the buffer is only valid right after a render
      const src = gl.domElement;
      if (!src.width || !src.height) return null;
      const out = document.createElement("canvas");
      out.width = Math.min(THUMB_WIDTH, src.width);
      out.height = Math.round((out.width * src.height) / src.width);
      const ctx = out.getContext("2d");
      if (!ctx) return null;
      ctx.drawImage(src, 0, 0, out.width, out.height);
      return out.toDataURL("image/jpeg", 0.85);
    };
    return () => { target.current = null; };
  }, [gl, scene, camera, target]);
  return null;
}

export default function Viewport({ sceneUrl, playback, player, captureRef, selected = null, onSelect }: {
  sceneUrl: string | null; playback: Playback; player: Player; captureRef?: RefObject<(() => string | null) | null>;
  selected?: string | null; onSelect?: (name: string | null) => void;
}) {
  const dark = useDark();
  const [error, setError] = useState<string | null>(null);
  const env = playback.environment;
  const bg = env ? env.sky_color : dark ? "#0e0f14" : "#e9ebef"; // the setting's sky, else --studio
  const [strobe, setStrobe] = useState(false);
  // turning the strobe on re-frames the camera around the whole exposure, not just the scene at this instant
  useEffect(() => {
    if (!strobe) return;
    const id = setTimeout(() => refit.current?.(), 80);
    return () => clearTimeout(id);
  }, [strobe]);
  const legendList = useMemo(() => legends(playback), [playback]);
  const offsets = useRef<Offsets>(new Map());
  const refit = useRef<(() => void) | null>(null);
  const runBox = useRef<(() => THREE.Box3) | null>(null);
  const [moved, setMoved] = useState(0);
  const onMoved = useCallback(() => setMoved(offsets.current.size), []);
  // a Blender-dressed scene with its own lights turns the viewer's rig down to a fill
  const [dress, setDress] = useState<DressInfo | null>(null);
  useEffect(() => setDress(null), [sceneUrl]);
  const fill = dress?.lighting === "scene" ? RIG_FILL : 1;
  useEffect(() => { offsets.current.clear(); setMoved(0); }, [playback]); // a new build starts from its defaults
  const reset = () => {
    offsets.current.clear();
    setMoved(0);
    setTimeout(() => refit.current?.(), 50); // after a frame has put the dragged objects back
  };

  return (
    <div className="relative h-full w-full overflow-hidden rounded-lg border border-line" style={{ background: bg }}>
      <Canvas camera={{ position: [9, 7, 12], fov: 45 }} dpr={[1, 2]} aria-label="3D simulation viewport"
        onPointerMissed={(e) => { if (e.button === 0) onSelect?.(null); }}>
        {env ? <Stage env={env} fill={fill} /> : (
          <>
            <color attach="background" args={[bg]} />
            <hemisphereLight args={[0xffffff, dark ? 0x222a35 : 0x8892a0, (dark ? 0.9 : 1.1) * fill]} />
            <directionalLight position={[6, 10, 8]} intensity={(dark ? 1.6 : 2.2) * fill} />
            <directionalLight position={[-6, 4, -6]} intensity={0.5 * fill} />
          </>
        )}
        <Grid
          infiniteGrid
          fadeDistance={45}
          cellSize={1}
          sectionSize={5}
          cellColor={env ? gridOn(env.ground_color, 0.18) : dark ? "#1d1f27" : "#d4d8df"}
          sectionColor={env ? gridOn(env.ground_color, 0.32) : dark ? "#30333d" : "#aeb4bf"}
          cellThickness={0.6}
          sectionThickness={1}
          position={[0, 0.002, 0]}
        />
        <Boundary onError={setError}>
          <Suspense fallback={null}>
            {sceneUrl ? (
              <Bounds margin={1.3}>
                <SceneModel url={sceneUrl} playback={playback} player={player} dark={dark} offsets={offsets} onMoved={onMoved} strobe={strobe}
                  selected={selected} onSelect={onSelect ?? noop} runBox={runBox} onDress={setDress} />
                <Refit target={refit} runBox={runBox} />
              </Bounds>
            ) : null}
          </Suspense>
        </Boundary>
        <OrbitControls makeDefault enableDamping target={[0, 0, 0]} />
        {captureRef ? <Capture target={captureRef} /> : null}
      </Canvas>
      {error || !sceneUrl ? (
        <p role="alert" className="absolute left-3 top-3 max-w-[80%] rounded bg-surface px-3 py-2 text-xs text-muted">
          {error ? `Scene failed to load: ${error}` : "No 3D scene is available yet; charts below show the data."}
        </p>
      ) : null}
      {(env && !error) || (sceneUrl && !error) ? (
        // one row: the setting's name gives way (truncates) to the controls, never slides under them
        <div className="pointer-events-none absolute inset-x-3 top-3 flex items-start justify-between gap-2">
          {env && !error ? (
            <p className="min-w-0 truncate rounded-md bg-surface px-2 py-1 text-xs font-semibold" title={`${env.name}: ${env.description}`}>
              {env.name}
            </p>
          ) : <span />}
          {sceneUrl && !error ? (
            <div className="pointer-events-auto flex shrink-0 items-center gap-2">
              <button
                type="button"
                aria-pressed={strobe}
                onClick={() => setStrobe((v) => !v)}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold shadow-sm ${strobe ? "border-accent bg-accent text-accent-fg" : "border-line bg-surface hover:border-fg/40"}`}
                title="Show each moving object's earlier positions as fading exposures"
              >
                <Icon name="strobe" size={14} />Strobe
              </button>
              <button type="button" onClick={reset} className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold shadow-sm hover:border-fg/40" title="Put every object back and frame the whole simulation">
                <Icon name="reset" size={14} />Reset view
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
      {sceneUrl && !error ? (
        <span className="pointer-events-none absolute bottom-3 right-3 hidden rounded-md bg-surface px-2 py-1 text-xs text-muted sm:inline">
          {moved > 0 ? `${moved} moved` : "Drag objects to move them · Shift to lift"}
        </span>
      ) : null}
      {legendList.length > 0 ? (
        <div className="pointer-events-none absolute bottom-3 left-3 flex max-w-[calc(100%-1.5rem)] flex-col gap-2 rounded-md bg-surface p-2 text-xs shadow-sm">
          {legendList.map((l) => (
            <div key={`${l.state}-${l.min}-${l.max}`} className="w-44 sm:w-56">
              <div className="mb-1 flex justify-between gap-2 text-fg">
                <span className="truncate font-medium">{l.state}</span>
                <span className="text-muted">{l.unit}</span>
              </div>
              <div className="h-2.5 rounded-sm" style={{ background: gradientCss(l.map) }} aria-hidden />
              <div className="mt-0.5 flex justify-between tabular-nums text-muted">
                <span>{fmt(l.min)}</span>
                <span>{fmt(l.max)}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function fmt(v: number): string {
  return Math.abs(v) >= 1e4 || (Math.abs(v) > 0 && Math.abs(v) < 1e-2) ? v.toExponential(1) : String(Number(v.toPrecision(4)));
}
