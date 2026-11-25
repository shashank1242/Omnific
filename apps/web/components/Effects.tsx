"use client";

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { prepareEffect, sampleEffect, type PreparedEffect } from "@/lib/effects";
import { findEntityNode } from "@/lib/move";
import { RUN_SECONDS } from "@/lib/speed";
import type { EffectKind, Playback } from "@/lib/types";
import type { Player } from "./player";

/*
 * Explosions, flashes, shock fronts, fire, smoke, glows, accretion disks and jets, drawn from the playback's
 * per-frame radius, brightness and colour (computed from the solved states by the Data agent). Their turbulence is
 * animated on simulation time, so scrubbing, pausing and playback speed behave exactly like the motion does.
 */

const NOISE = /* glsl */ `
float hash3(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
float noise3(vec3 x) {
  vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash3(i), hash3(i + vec3(1, 0, 0)), f.x), mix(hash3(i + vec3(0, 1, 0)), hash3(i + vec3(1, 1, 0)), f.x), f.y),
             mix(mix(hash3(i + vec3(0, 0, 1)), hash3(i + vec3(1, 0, 1)), f.x), mix(hash3(i + vec3(0, 1, 1)), hash3(i + vec3(1, 1, 1)), f.x), f.y), f.z);
}
float fbm(vec3 p) { float v = 0.0; float a = 0.5; for (int k = 0; k < 4; k++) { v += a * noise3(p); p *= 2.03; a *= 0.5; } return v; }
`;

/** A turbulent ball of hot gas: fireballs, flames (stretched upward) and smoke (grey, no glow). */
function ballMaterial(mode: "fire" | "smoke"): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    toneMapped: false,
    blending: mode === "fire" ? THREE.AdditiveBlending : THREE.NormalBlending,
    uniforms: { uTime: { value: 0 }, uColor: { value: new THREE.Color(1, 0.6, 0.2) }, uIntensity: { value: 1 }, uSeed: { value: 0 } },
    vertexShader: /* glsl */ `
      uniform float uTime; uniform float uSeed;
      varying vec3 vNormal; varying vec3 vView; varying vec3 vPos;
      ${NOISE}
      void main() {
        vec3 p = position;
        float n = fbm(p * 2.2 + vec3(uSeed, uSeed * 0.7, -uTime * 0.9));
        p *= 1.0 + 0.32 * (n - 0.5) * 2.0;
        vPos = p;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        vNormal = normalize(normalMatrix * normal);
        vView = -mv.xyz;
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */ `
      uniform float uTime; uniform vec3 uColor; uniform float uIntensity; uniform float uSeed;
      varying vec3 vNormal; varying vec3 vView; varying vec3 vPos;
      ${NOISE}
      void main() {
        float fres = pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), 1.6);
        float n = fbm(vPos * 3.1 + vec3(uSeed, 0.0, uTime * 1.3));
        ${mode === "fire" ? `
        vec3 hot = mix(uColor, vec3(1.0), 0.55);
        vec3 cool = uColor * 0.45 + vec3(0.04, 0.02, 0.01);
        vec3 col = mix(hot, cool, clamp(fres * 1.1 + (0.5 - n) * 0.9, 0.0, 1.0)) * (0.6 + 1.6 * uIntensity);
        float a = uIntensity * clamp(1.15 - fres * 0.7 + (n - 0.5) * 0.6, 0.0, 1.0);` : `
        vec3 col = mix(uColor, uColor * 0.55, clamp(fres + (0.5 - n), 0.0, 1.0));
        float a = uIntensity * 0.75 * clamp(1.0 - fres * 0.9 + (n - 0.5) * 0.8, 0.0, 1.0);`}
        gl_FragColor = vec4(col, a);
      }`,
  });
}

/** A thin glowing shell, bright at its rim: a shock front. */
function shellMaterial(): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, toneMapped: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    uniforms: { uColor: { value: new THREE.Color(0.7, 0.85, 1) }, uIntensity: { value: 1 } },
    vertexShader: /* glsl */ `
      varying vec3 vNormal; varying vec3 vView;
      void main() { vec4 mv = modelViewMatrix * vec4(position, 1.0); vNormal = normalize(normalMatrix * normal); vView = -mv.xyz; gl_Position = projectionMatrix * mv; }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor; uniform float uIntensity; varying vec3 vNormal; varying vec3 vView;
      void main() { float rim = pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), 3.0); gl_FragColor = vec4(uColor * 1.6, uIntensity * (0.06 + 0.9 * rim)); }`,
  });
}

/** A flat hot ring whose bands orbit faster near the centre (Keplerian), hotter and whiter inside. */
function diskMaterial(): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, toneMapped: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    uniforms: { uTime: { value: 0 }, uColor: { value: new THREE.Color(1, 0.55, 0.2) }, uIntensity: { value: 1 } },
    vertexShader: /* glsl */ `varying vec2 vXY; void main() { vXY = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */ `
      uniform float uTime; uniform vec3 uColor; uniform float uIntensity; varying vec2 vXY;
      ${NOISE}
      void main() {
        float r = length(vXY);
        float a = atan(vXY.y, vXY.x) - uTime * 0.9 / pow(max(r, 0.2), 1.5);
        float n = fbm(vec3(cos(a) * r * 5.0, sin(a) * r * 5.0, r * 9.0));
        float heat = 1.0 - smoothstep(0.3, 1.0, r);
        vec3 col = mix(uColor * vec3(0.9, 0.45, 0.25), mix(uColor, vec3(1.0, 0.95, 0.85), 0.6), heat) * (0.45 + 1.3 * n);
        float alpha = uIntensity * smoothstep(0.3, 0.36, r) * (1.0 - smoothstep(0.78, 1.0, r)) * (0.35 + 0.75 * n);
        gl_FragColor = vec4(col * (0.8 + heat), alpha);
      }`,
  });
}

/** Twin beams: bright at the source, fading along their length. */
function jetMaterial(): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, toneMapped: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    uniforms: { uTime: { value: 0 }, uColor: { value: new THREE.Color(0.6, 0.8, 1) }, uIntensity: { value: 1 } },
    vertexShader: /* glsl */ `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */ `
      uniform float uTime; uniform vec3 uColor; uniform float uIntensity; varying vec2 vUv;
      ${NOISE}
      void main() {
        float n = noise3(vec3(vUv.x * 12.0, vUv.y * 6.0 - uTime * 4.0, 0.0));
        gl_FragColor = vec4(uColor * 1.5, uIntensity * (1.0 - vUv.y) * (0.5 + 0.5 * n));
      }`,
  });
}

let glowTexture: THREE.Texture | null = null;
/** A soft radial falloff, made once and shared by every glow sprite. */
function glow(): THREE.Texture {
  if (glowTexture) return glowTexture;
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  if (g) {
    const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.25, "rgba(255,255,255,0.55)");
    grad.addColorStop(0.6, "rgba(255,255,255,0.12)");
    grad.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 128);
  }
  glowTexture = new THREE.CanvasTexture(c);
  glowTexture.colorSpace = THREE.SRGBColorSpace;
  return glowTexture;
}

function sprite(): THREE.Sprite {
  return new THREE.Sprite(new THREE.SpriteMaterial({
    map: glow(), transparent: true, depthWrite: false, toneMapped: false, blending: THREE.AdditiveBlending,
  }));
}

/** Deterministic debris: unit directions and speed factors from a fixed seed, so every viewer sees the same blast. */
function debris(count: number, seed: number): THREE.Points {
  const pos = new Float32Array(count * 3);
  const dirs = new Float32Array(count * 4);
  let s = (seed * 9301 + 49297) % 233280 || 1;
  const rnd = () => ((s = (s * 9301 + 49297) % 233280) / 233280);
  for (let k = 0; k < count; k++) {
    const u = rnd() * 2 - 1;
    const th = rnd() * Math.PI * 2;
    const r = Math.sqrt(1 - u * u);
    dirs.set([r * Math.cos(th), u, r * Math.sin(th), 0.85 + rnd() * 0.6], k * 4);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.userData.dirs = dirs;
  const pts = new THREE.Points(geo, new THREE.PointsMaterial({
    size: 0.08, transparent: true, depthWrite: false, toneMapped: false, blending: THREE.AdditiveBlending,
  }));
  pts.frustumCulled = false;
  return pts;
}

interface Built {
  group: THREE.Group;
  update: (radius: number, intensity: number, color: THREE.Color, anim: number) => void;
  dispose: () => void;
}

const SPHERE = () => new THREE.SphereGeometry(1, 64, 48);

function build(kind: EffectKind, seed: number): Built {
  const group = new THREE.Group();
  const disposables: { dispose: () => void }[] = [];
  const keep = <T extends { dispose: () => void }>(x: T) => { disposables.push(x); return x; };
  const light = new THREE.PointLight(0xffffff, 0, 0, 2);
  const parts: ((r: number, i: number, c: THREE.Color, anim: number) => void)[] = [];

  const fireBall = (stretchY: number, mode: "fire" | "smoke", offset = new THREE.Vector3()) => {
    const mat = keep(ballMaterial(mode));
    mat.uniforms.uSeed!.value = seed + offset.x * 3.1 + offset.z * 1.7;
    const mesh = new THREE.Mesh(keep(SPHERE()), mat);
    group.add(mesh);
    parts.push((r, i, c, anim) => {
      mesh.scale.set(r, r * stretchY, r);
      mesh.position.copy(offset).multiplyScalar(r);
      if (stretchY > 1) mesh.position.y += r * (stretchY - 1) * 0.6;
      mat.uniforms.uTime!.value = anim;
      mat.uniforms.uIntensity!.value = i;
      (mat.uniforms.uColor!.value as THREE.Color).copy(c);
    });
  };
  const halo = (size: number, strength: number) => {
    const s = sprite();
    keep(s.material);
    group.add(s);
    parts.push((r, i, c) => {
      s.scale.setScalar(r * size);
      s.material.color.copy(c);
      s.material.opacity = Math.min(1, i * strength);
    });
  };
  const lit = (power: number) => {
    group.add(light);
    parts.push((r, i, c) => {
      light.color.copy(c);
      light.intensity = power * i * Math.max(1, (r * 3) ** 2);
    });
  };

  switch (kind) {
    case "explosion": {
      fireBall(1, "fire");
      halo(3.2, 0.9);
      const pts = debris(360, seed);
      keep(pts.geometry);
      keep(pts.material as THREE.Material);
      group.add(pts);
      const dirs = pts.geometry.userData.dirs as Float32Array;
      const attr = pts.geometry.getAttribute("position") as THREE.BufferAttribute;
      parts.push((r, i, c) => {
        for (let k = 0; k < dirs.length / 4; k++) {
          const f = r * 1.15 * (dirs[k * 4 + 3] as number);
          attr.setXYZ(k, (dirs[k * 4] as number) * f, (dirs[k * 4 + 1] as number) * f, (dirs[k * 4 + 2] as number) * f);
        }
        attr.needsUpdate = true;
        const m = pts.material as THREE.PointsMaterial;
        m.size = Math.max(0.03, r * 0.035);
        m.color.copy(c).lerp(new THREE.Color(1, 1, 1), 0.3);
        m.opacity = Math.min(1, i * 1.2);
      });
      lit(6);
      break;
    }
    case "flash":
      halo(4, 1.2);
      halo(1.2, 1.5);
      lit(40);
      break;
    case "shockwave": {
      const mat = keep(shellMaterial());
      const mesh = new THREE.Mesh(keep(SPHERE()), mat);
      group.add(mesh);
      parts.push((r, i, c) => {
        mesh.scale.setScalar(r);
        mat.uniforms.uIntensity!.value = i;
        (mat.uniforms.uColor!.value as THREE.Color).copy(c);
      });
      break;
    }
    case "fire":
      fireBall(1.7, "fire");
      fireBall(1.2, "fire", new THREE.Vector3(0.45, 0.1, 0.2));
      fireBall(1.3, "fire", new THREE.Vector3(-0.4, 0.05, -0.3));
      halo(2.6, 0.7);
      lit(4);
      break;
    case "smoke":
      for (const [x, y, z] of [[0, 0, 0], [0.5, 0.35, 0.2], [-0.45, 0.3, -0.25], [0.15, 0.75, -0.4], [-0.2, 0.6, 0.45]] as const) {
        fireBall(1, "smoke", new THREE.Vector3(x, y, z));
      }
      break;
    case "glow":
      halo(2.4, 1);
      lit(2);
      break;
    case "accretion_disk": {
      const mat = keep(diskMaterial());
      const mesh = new THREE.Mesh(keep(new THREE.RingGeometry(0.3, 1, 160, 1)), mat);
      mesh.rotation.x = -Math.PI / 2 + 0.12; // nearly edge-on to the default camera's horizon, like the iconic pictures
      group.add(mesh);
      parts.push((r, i, c, anim) => {
        mesh.scale.setScalar(r);
        mat.uniforms.uTime!.value = anim;
        mat.uniforms.uIntensity!.value = i;
        (mat.uniforms.uColor!.value as THREE.Color).copy(c);
      });
      halo(0.9, 0.6); // the photon ring's glow around the shadow
      lit(3);
      break;
    }
    case "splash": {
      // droplets thrown up and out in a crown, falling back, plus the crown's ring on the liquid surface
      const pts = debris(260, seed);
      keep(pts.geometry);
      const mat = pts.material as THREE.PointsMaterial;
      mat.blending = THREE.NormalBlending;
      keep(mat);
      group.add(pts);
      const dirs = pts.geometry.userData.dirs as Float32Array;
      const attr = pts.geometry.getAttribute("position") as THREE.BufferAttribute;
      const ring = new THREE.Mesh(keep(new THREE.RingGeometry(0.85, 1, 96, 1)), keep(new THREE.MeshBasicMaterial({
        transparent: true, depthWrite: false, side: THREE.DoubleSide,
      })));
      ring.rotation.x = -Math.PI / 2;
      group.add(ring);
      parts.push((r, i, c) => {
        for (let k = 0; k < dirs.length / 4; k++) {
          const f = r * (dirs[k * 4 + 3] as number);
          const up = Math.abs(dirs[k * 4 + 1] as number); // upward hemisphere: a crown, not a sphere
          attr.setXYZ(k, (dirs[k * 4] as number) * f, up * f * 0.8, (dirs[k * 4 + 2] as number) * f);
        }
        attr.needsUpdate = true;
        mat.size = Math.max(0.03, r * 0.05);
        mat.color.copy(c);
        mat.opacity = Math.min(1, i * 1.1);
        ring.scale.setScalar(r);
        (ring.material as THREE.MeshBasicMaterial).color.copy(c);
        (ring.material as THREE.MeshBasicMaterial).opacity = i * 0.7;
      });
      break;
    }
    case "jet": {
      const mat = keep(jetMaterial());
      // narrow at the source, widening along its length (uv.y runs from the source to the tip)
      const geo = keep(new THREE.CylinderGeometry(0.12, 0.03, 1, 24, 1, true).translate(0, 0.5, 0));
      for (const dir of [1, -1]) {
        const mesh = new THREE.Mesh(geo, mat);
        group.add(mesh);
        parts.push((r) => mesh.scale.set(r, r * dir, r));
      }
      parts.push((_r, i, c, anim) => {
        mat.uniforms.uTime!.value = anim;
        mat.uniforms.uIntensity!.value = i;
        (mat.uniforms.uColor!.value as THREE.Color).copy(c);
      });
      break;
    }
  }
  return {
    group,
    update: (r, i, c, anim) => { for (const p of parts) p(r, i, c, anim); },
    dispose: () => { for (const d of disposables) d.dispose(); },
  };
}

/** Below this brightness or radius an effect is not drawn at all. */
const OFF = 0.003;

export default function Effects({ playback, player }: { playback: Playback; player: Player }) {
  const scene = useThree((s) => s.scene);
  const items = useMemo(
    () => (playback.effects ?? []).map((fx, k) => ({ prepared: prepareEffect(fx), built: build(fx.kind, k + 1) })),
    [playback],
  );
  useEffect(() => () => { for (const it of items) it.built.dispose(); }, [items]);
  const anchors = useMemo(() => new Map<string, THREE.Object3D>(), [playback]); // eslint-disable-line react-hooks/exhaustive-deps -- a fresh cache per playback
  const color = useMemo(() => new THREE.Color(), []);
  const at = useMemo(() => new THREE.Vector3(), []);

  const anchor = (name: string): THREE.Object3D | null => {
    const hit = anchors.get(name);
    if (hit?.parent) return hit;
    const found = findEntityNode(scene, name); // its own root node (strobe copies come later in the graph)
    if (found) anchors.set(name, found);
    return found ?? null;
  };

  const place = (p: PreparedEffect, g: THREE.Group): boolean => {
    if (p.fx.anchor) {
      const node = anchor(p.fx.anchor);
      if (!node) return false;
      node.getWorldPosition(at);
      g.position.copy(at);
    } else if (p.fx.position) {
      g.position.set(p.fx.position[0], p.fx.position[1], p.fx.position[2]);
    }
    return true;
  };

  useFrame(() => {
    const time = player.state.time;
    const span = player.t1 - player.t0 || 1;
    const anim = ((time - player.t0) / span) * RUN_SECONDS; // turbulence runs on simulation time
    for (const { prepared, built } of items) {
      const s = sampleEffect(prepared, playback.t, time);
      const on = s.intensity > OFF && s.radius > OFF && place(prepared, built.group);
      built.group.visible = on;
      if (!on) continue;
      color.setRGB(s.color[0], s.color[1], s.color[2], THREE.SRGBColorSpace);
      built.update(s.radius, s.intensity, color, anim);
    }
  });

  return (
    <>
      {items.map(({ built, prepared }) => (
        <primitive key={prepared.fx.name} object={built.group} name={`effect:${prepared.fx.name}`} />
      ))}
    </>
  );
}
