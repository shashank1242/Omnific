"use client";

import { Component, Suspense, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import * as THREE from "three";
import { Canvas, useFrame } from "@react-three/fiber";
import { Bounds, Grid, OrbitControls, useGLTF } from "@react-three/drei";
import { colormap, normalize } from "@/lib/colormap";
import { sampleAlong } from "@/lib/playback";
import { evaluateScene, legends, type EntityPose } from "@/lib/tracks";
import type { Playback } from "@/lib/types";
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

function SceneModel({ url, playback, player, dark }: { url: string; playback: Playback; player: Player; dark: boolean }) {
  const gltf = useGLTF(url);
  // Own copy so cloned materials never leak into the drei cache.
  const root = useMemo(() => {
    const clone = gltf.scene.clone(true);
    clone.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) {
        m.material = Array.isArray(m.material) ? m.material.map((x) => x.clone()) : m.material.clone();
        m.geometry = m.geometry.clone();
      }
    });
    return clone;
  }, [gltf.scene]);

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
  const apply = (time: number) => {
    const { poses, links } = evaluateScene(playback, time, baseOf);
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
    for (const [name, pose] of poses) {
      const entry = bases.get(name);
      if (!entry) continue;
      const { node, base } = entry;
      node.position.set(pose.position.x ?? base.position.x, pose.position.y ?? base.position.y, pose.position.z ?? base.position.z);
      node.rotation.set(base.rotation.x, base.rotation.y, pose.rotationZ ?? base.rotation.z);
      if (pose.scale !== undefined) node.scale.setScalar(pose.scale);
      if (pose.color) applyColor(node, pose.color);
      if (pose.colorField) applyColorField(node, pose.colorField);
    }
  };

  useLayoutEffect(() => apply(player.state.time));
  useFrame(() => apply(player.state.time));
  return (
    <>
      <primitive object={root} />
      <primitive object={linkGroup} />
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

export default function Viewport({ sceneUrl, playback, player }: { sceneUrl: string | null; playback: Playback; player: Player }) {
  const dark = useDark();
  const [error, setError] = useState<string | null>(null);
  const bg = dark ? "#0e1217" : "#eef1f5";
  const legendList = useMemo(() => legends(playback), [playback]);

  return (
    <div className="relative h-full w-full overflow-hidden rounded-lg border border-line" style={{ background: bg }}>
      <Canvas camera={{ position: [9, 7, 12], fov: 45 }} dpr={[1, 2]} aria-label="3D simulation viewport">
        <color attach="background" args={[bg]} />
        <hemisphereLight args={[0xffffff, dark ? 0x222a35 : 0x8892a0, dark ? 0.9 : 1.1]} />
        <directionalLight position={[6, 10, 8]} intensity={dark ? 1.6 : 2.2} />
        <directionalLight position={[-6, 4, -6]} intensity={0.5} />
        <Grid
          infiniteGrid
          fadeDistance={45}
          cellSize={1}
          sectionSize={5}
          cellColor={dark ? "#2a3340" : "#c3cad4"}
          sectionColor={dark ? "#46556a" : "#8f9bab"}
          cellThickness={0.6}
          sectionThickness={1}
        />
        <Boundary onError={setError}>
          <Suspense fallback={null}>
            {sceneUrl ? (
              <Bounds fit margin={1.5}>
                <SceneModel url={sceneUrl} playback={playback} player={player} dark={dark} />
              </Bounds>
            ) : null}
          </Suspense>
        </Boundary>
        <OrbitControls makeDefault enableDamping target={[0, 0, 0]} />
      </Canvas>
      {error || !sceneUrl ? (
        <p role="alert" className="absolute left-3 top-3 max-w-[80%] rounded bg-surface/90 px-3 py-2 text-xs text-muted">
          {error ? `Scene failed to load: ${error}` : "No 3D scene is available yet; charts below show the data."}
        </p>
      ) : null}
      {legendList.length > 0 ? (
        <div className="pointer-events-none absolute bottom-3 left-3 flex max-w-[calc(100%-1.5rem)] flex-col gap-2 rounded-md bg-surface/90 p-2 text-xs shadow-sm">
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
