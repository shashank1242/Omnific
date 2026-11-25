"""CAD + generation on one asset: the CAD model stays the mechanical truth (dimensions, joints, clips, STEP file) and
a generated mesh supplies the surface. The generated mesh is fitted to the CAD envelope, then cut along the CAD parts:
each triangle follows the bone of the nearest CAD part, with vertices duplicated at the cuts, so the pieces move as
rigid bodies about the exact CAD joints instead of stretching across them.
"""

from __future__ import annotations

import numpy as np
import trimesh
from omnific_common.contracts import CheckResult
from scipy.spatial import cKDTree

from . import meshops, rig
from .asset import Asset

SAMPLES = 4000
MAX_SCALE_RATIO = 1.5  # per-axis fit: beyond this the generated shape has the wrong proportions, not just size
MAX_CHAMFER = 0.06  # mean surface distance, as a fraction of the longest side
MAX_PART_GAP = 0.08  # mean distance from a moving CAD part to the surface bound to it, same units


def check(name: str, ok: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=bool(ok), detail=detail)


def _sample(a: Asset) -> tuple[np.ndarray, np.ndarray]:
    mesh = trimesh.Trimesh(a.positions, a.indices, process=False)
    pts, faces = trimesh.sample.sample_surface(mesh, SAMPLES, seed=0)
    return np.asarray(pts), np.asarray(faces)


def _yaw(quarter_turns: int) -> np.ndarray:
    c, s = np.round(np.cos(quarter_turns * np.pi / 2)), np.round(np.sin(quarter_turns * np.pi / 2))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _chamfer(a: np.ndarray, b: np.ndarray) -> float:
    return float((cKDTree(b).query(a)[0].mean() + cKDTree(a).query(b)[0].mean()) / 2)


def fit(gen: Asset, cad: Asset) -> tuple[np.ndarray | None, float, float]:
    """Best of the four upright orientations, scaled per axis onto the CAD bounding box (both meshes are +Y up).
    Returns (fitted vertices or None, chamfer as a fraction of the longest side, scale ratio of the best fit)."""
    lo, hi = cad.bbox()
    centre, extent = (lo + hi) / 2, hi - lo
    longest = float(extent.max())
    target, _ = _sample(cad)
    samples, _ = _sample(gen)
    best: tuple[np.ndarray | None, float, float] = (None, float("inf"), float("inf"))
    for k in range(4):
        r = _yaw(k)
        turned = gen.positions @ r.T
        t_lo, t_hi = turned.min(axis=0), turned.max(axis=0)
        scale = extent / np.maximum(t_hi - t_lo, 1e-9)
        ratio = float(scale.max() / max(scale.min(), 1e-9))
        mid = (t_lo + t_hi) / 2
        d = _chamfer((samples @ r.T - mid) * scale + centre, target) / max(longest, 1e-12)
        if ratio <= MAX_SCALE_RATIO and d < best[1]:
            best = ((turned - mid) * scale + centre, d, ratio)
        elif best[0] is None and ratio < best[2]:
            best = (None, float("inf"), ratio)
    return best


def _pick(arr: np.ndarray | None, idx: np.ndarray) -> np.ndarray | None:
    return None if arr is None else arr[idx]


def bind(gen: Asset, cad: Asset) -> Asset:
    """`gen` (already fitted to `cad`) with the CAD skeleton and clips, cut along the CAD parts."""
    if not cad.bones:
        return Asset(positions=gen.positions, indices=gen.indices, normals=gen.normals, uvs=gen.uvs,
                     colors=gen.colors, texture=gen.texture, texture_mime=gen.texture_mime,
                     base_color=gen.base_color)
    target, target_faces = _sample(cad)
    target_bone = cad.joints[cad.indices[target_faces, 0], 0].astype(np.int64)
    centroids = gen.positions[gen.indices].mean(axis=1)
    face_bone = target_bone[cKDTree(target).query(centroids)[1]]
    # one vertex per (vertex, bone) pair: a vertex shared across a cut becomes two, so nothing stretches at the cut
    n_bones = len(cad.bones)
    corners = gen.indices.reshape(-1).astype(np.int64)
    pairs, inverse = np.unique(corners * n_bones + np.repeat(face_bone, 3), return_inverse=True)
    vertex, bone = pairs // n_bones, pairs % n_bones
    joints = np.zeros((len(pairs), 4), np.uint16)
    joints[:, 0] = bone
    weights = np.zeros((len(pairs), 4), np.float32)
    weights[:, 0] = 1.0
    return Asset(positions=gen.positions[vertex], indices=inverse.reshape(-1, 3).astype(np.uint32),
                 normals=_pick(gen.normals, vertex), uvs=_pick(gen.uvs, vertex), colors=_pick(gen.colors, vertex),
                 texture=gen.texture, texture_mime=gen.texture_mime, base_color=gen.base_color,
                 bones=cad.bones, joints=joints, weights=weights, clips=cad.clips)


def _moving_part_gaps(skinned: Asset, cad: Asset) -> dict[str, float]:
    """Mean distance from each moving CAD part to the surface now bound to its bone, over the longest side."""
    lo, hi = cad.bbox()
    longest = max(float((hi - lo).max()), 1e-12)
    target, target_faces = _sample(cad)
    target_bone = cad.joints[cad.indices[target_faces, 0], 0]
    samples, faces = _sample(skinned)
    bone = skinned.joints[skinned.indices[faces, 0], 0]
    gaps: dict[str, float] = {}
    cad_vertex_bone = cad.joints[:, 0]
    for i, b in enumerate(cad.bones):
        # area samples plus the part's own vertices: a thin part may get no area sample at all
        here = np.concatenate([target[target_bone == i], cad.positions[cad_vertex_bone == i]])
        if b.parent < 0 or not len(here):
            continue
        mine = samples[bone == i]
        gaps[b.name] = float(cKDTree(mine).query(here)[0].mean()) / longest if len(mine) else float("inf")
    return gaps


def apply_skin(gen: Asset, cad: Asset) -> tuple[Asset | None, list[CheckResult]]:
    """The skinned asset and its checks; None when the generated shape can't be fitted to the CAD model at all."""
    if gen.n_tris == 0 or len(gen.positions) == 0:
        return None, [check("cad_skin.fit", False, "the generated surface is empty")]
    positions, chamfer, ratio = fit(gen, cad)
    if positions is None:
        return None, [check("cad_skin.proportions", False,
                            f"needs {ratio:.2f}x more stretch on one axis than another (max {MAX_SCALE_RATIO:g}x)")]
    fitted = Asset(**{**vars(gen), "positions": positions.astype(np.float32), "normals": None})
    fitted.compute_normals()
    out = bind(fitted, cad)
    checks = [check("cad_skin.proportions", True, f"per-axis scale ratio {ratio:.2f}x (max {MAX_SCALE_RATIO:g}x)"),
              check("cad_skin.fit", chamfer <= MAX_CHAMFER,
                    f"mean distance to the CAD surface {chamfer:.1%} of size (max {MAX_CHAMFER:.0%})")]
    if cad.bones:
        gaps = _moving_part_gaps(out, cad)
        far = {n: g for n, g in gaps.items() if g > MAX_PART_GAP}
        checks.append(check("cad_skin.moving_parts", not far,
                            f"{len(gaps)} moving parts covered by the surface" if not far else
                            "moving parts the surface misses: " + ", ".join(
                                f"{n} ({g:.1%} away, max {MAX_PART_GAP:.0%})" for n, g in far.items())))
    checks += meshops.evaluate_generated(out)
    if cad.bones:
        skin = rig.evaluate_skin(out)
        checks += skin
        if all(c.passed for c in skin):
            checks += rig.evaluate_deformation(out)
    return out, checks
