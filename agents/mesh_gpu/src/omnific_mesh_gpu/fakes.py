"""Deterministic CPU stand-ins for the GPU models (MESH_*_BACKEND=fake): tests, CI, and running the generated-mesh
path end to end on a machine without a 24 GB GPU. They exercise every evaluator for real; only the models are fake."""

from __future__ import annotations

import numpy as np
from omnific_common.contracts import ClipPlan
from omnific_mesh import anim
from omnific_mesh.asset import Asset, Bone, from_glb, to_glb


class FakeGenerator:
    """A coloured capsule 'body' with four leg cylinders (a crude quadruped), jittered by seed."""

    async def generate(self, image_png: bytes, seed: int, decimation_target: int, texture_size: int) -> bytes:
        import trimesh

        rng = np.random.default_rng(seed)
        parts = [trimesh.creation.capsule(height=1.2, radius=0.35, count=[16, 16])]
        parts[0].apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 0, 1]))
        parts[0].apply_translation([0, 1.0, 0])
        for x in (-0.55, 0.55):
            for z in (-0.25, 0.25):
                leg = trimesh.creation.cylinder(radius=0.09, height=0.8, sections=12)
                leg.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
                leg.apply_translation([x, 0.45, z])
                parts.append(leg)
        m = trimesh.util.concatenate(parts)
        m.vertices += rng.normal(0, 0.002, m.vertices.shape)
        colour = np.array([0.55, 0.35, 0.2]) + rng.uniform(-0.05, 0.05, 3)
        a = Asset(positions=m.vertices.astype(np.float32), indices=m.faces.astype(np.uint32),
                  colors=np.tile(colour.astype(np.float32), (len(m.vertices), 1)))
        return to_glb(a, "generated")


class FakeRigger:
    """Root at the body centre, one bone per leg (nearest-bone rigid skinning)."""

    async def rig(self, glb: bytes) -> bytes:
        a = from_glb(glb)
        lo, hi = a.bbox()
        mid = (lo + hi) / 2
        height = hi[1] - lo[1]
        bones = [Bone("root", -1, np.array([mid[0], lo[1] + 0.7 * height, mid[2]], np.float32))]
        low = a.positions[:, 1] < lo[1] + 0.45 * height
        for sx in (-1, 1):
            for sz in (-1, 1):
                sel = low & (np.sign(a.positions[:, 0] - mid[0]) == sx) & (np.sign(a.positions[:, 2] - mid[2]) == sz)
                if sel.any():
                    top = a.positions[sel].mean(0)
                    top[1] = lo[1] + 0.45 * height
                    bones.append(Bone(f"leg_{'r' if sx > 0 else 'l'}{'b' if sz > 0 else 'f'}", 0,
                                      top.astype(np.float32)))
        heads = np.array([b.head for b in bones])
        owner = np.zeros(len(a.positions), np.int64)
        if len(bones) > 1:
            d = np.linalg.norm(a.positions[:, None, [0, 2]] - heads[None, 1:, [0, 2]], axis=2)
            owner = np.where(low, 1 + d.argmin(1), 0)
        a.bones = bones
        a.joints = np.zeros((len(a.positions), 4), np.uint16)
        a.joints[:, 0] = owner
        a.weights = np.zeros((len(a.positions), 4), np.float32)
        a.weights[:, 0] = 1
        return to_glb(a, "rigged")


class FakeAnimator:
    async def animate(self, rigged_glb: bytes, clips: list[dict]) -> bytes:
        a = from_glb(rigged_glb)
        a.clips = [anim.procedural_clip(a, ClipPlan.model_validate(c)) for c in clips]
        return to_glb(a, "animated")
