"""Rigs: the physics joint derived from the SimSpec, linear-blend skinning, and the rig evaluators."""

from __future__ import annotations

import numpy as np
from omnific_common.contracts import CheckResult

from .asset import Asset, quat_to_matrix

MAX_INFLUENCES = 4
WEIGHT_SUM_TOL = 1e-4
DEFORM_ANGLE = np.radians(30)
# Bending one bone ±30°: linear-blend skinning always stretches a few small triangles at the joint (measured on real
# UniRig rigs of TRELLIS.2 meshes, 2026-11-25: worst edge 2.4-3.1x, at most 0.6% of edges past 1.5x). A broken rig
# tears (a part weighted to the wrong bone opens a seam: edges far past 4x) or smears (many edges stretch).
MAX_EDGE_STRETCH = 4.0  # no single edge beyond this: a tear
STRETCH = 1.5  # an edge past this counts as stretched...
MAX_STRETCHED_FRACTION = 0.01  # ...and no bend may stretch more than this share of the mesh's edges
JOINT_TOL = 1e-3  # scene units (the scene is ~10 units across, so ~0.1 mm at a 1 m pendulum)


def check(name: str, ok: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=bool(ok), detail=detail)


# --- physics joint ---------------------------------------------------------------------------

def length_scale(entities) -> float:
    """Same scene scale as gltf.rest_positions and the Data agent: 10 / max |pivot or arm component|."""
    comps = [abs(c) for e in entities for v in (e.pivot, e.arm) for c in v or []]
    return 10.0 / max(comps) if comps and max(comps) > 0 else 1.0


def physics_joint(spec, entity_name: str) -> list[float]:
    """[pivot x, y, z, arm x, y, z] in scene units for an articulated entity, else []."""
    e = next((x for x in spec.entities if x.name == entity_name), None)
    if e is None or e.arm is None:
        return []
    k = length_scale(spec.entities)
    pivot = [c * k for c in (e.pivot or [0.0, 0.0, 0.0])]
    return pivot + [c * k for c in e.arm]


def pose_offset(arm, angle: float) -> np.ndarray:
    """Pivot -> entity offset at `angle` about z: exactly the viewer's articulation formula (lib/articulation.ts)."""
    c, s = np.cos(angle), np.sin(angle)
    return np.array([arm[0] * c - arm[1] * s, arm[0] * s + arm[1] * c, arm[2]])


def evaluate_physics_joint(spec, entity_name: str, joint: list[float], rest: list[float] | None) -> list[CheckResult]:
    e = next((x for x in spec.entities if x.name == entity_name), None)
    if e is None:
        return [check("joint.entity", False, f"{entity_name!r} is not a SimSpec entity")]
    if e.arm is None:
        return [check("joint.absent", not joint, "no arm in the spec, so no joint" if not joint
                      else "a joint was set on an entity without an arm")]
    out = [check("joint.present", len(joint) == 6, f"joint has {len(joint)} components (want 6)")]
    if len(joint) != 6:
        return out
    k = length_scale(spec.entities)
    want = [c * k for c in (e.pivot or [0.0, 0.0, 0.0])] + [c * k for c in e.arm]
    err = max(abs(a - b) for a, b in zip(joint, want, strict=True))
    out.append(check("joint.matches_spec", err <= JOINT_TOL, f"max |joint - spec·scale| = {err:.2e}"))
    pivot, arm = np.array(joint[:3]), np.array(joint[3:])
    if rest is not None and e.parent is None:  # root-level entity: rest node position must be pivot + arm
        d = float(np.abs(pivot + arm - np.asarray(rest)).max())
        out.append(check("joint.rest_pose", d <= JOINT_TOL, f"|rest - (pivot + arm)| = {d:.2e}"))
    length = np.linalg.norm(arm)
    worst = max(abs(np.linalg.norm(pose_offset(arm, a)) - length) for a in (-np.pi / 2, -np.pi / 4, np.pi / 4,
                                                                              np.pi / 2, np.pi))
    out.append(check("joint.rigid_swing", worst <= JOINT_TOL,
                     f"arm length drift over ±90°/180° swing = {worst:.2e}"))
    parent_ok = e.parent is None or any(x.name == e.parent for x in spec.entities)
    out.append(check("joint.hierarchy", parent_ok, f"parent {e.parent!r} " + ("exists" if parent_ok else "missing")))
    return out


# --- skinning --------------------------------------------------------------------------------

def bone_world_matrices(a: Asset, local_rot: dict[str, np.ndarray] | None = None) -> np.ndarray:
    """(b, 4, 4) skinning matrices (world · inverse bind) with per-bone local rotations about each head."""
    local_rot = local_rot or {}
    n = len(a.bones)
    world = [None] * n

    def wm(i):
        if world[i] is not None:
            return world[i]
        b = a.bones[i]
        m = np.eye(4)
        ph = a.bones[b.parent].head if b.parent >= 0 else np.zeros(3)
        m[:3, 3] = np.asarray(b.head) - ph
        if b.name in local_rot:
            m[:3, :3] = quat_to_matrix(np.asarray(local_rot[b.name], float))
        world[i] = (wm(b.parent) if b.parent >= 0 else np.eye(4)) @ m
        return world[i]

    out = np.empty((n, 4, 4))
    for i, b in enumerate(a.bones):
        ib = np.eye(4)
        ib[:3, 3] = -np.asarray(b.head)
        out[i] = wm(i) @ ib
    return out


def skin(a: Asset, local_rot: dict[str, np.ndarray] | None = None) -> np.ndarray:
    """Linear-blend-skinned positions (n, 3)."""
    if not a.bones:
        return a.positions.astype(np.float64)
    mats = bone_world_matrices(a, local_rot)
    p = np.c_[a.positions.astype(np.float64), np.ones(len(a.positions))]
    out = np.zeros((len(p), 3))
    for k in range(a.joints.shape[1]):
        m = mats[a.joints[:, k].astype(np.int64)]  # (n, 4, 4)
        out += a.weights[:, k:k + 1] * np.einsum("nij,nj->ni", m, p)[:, :3]
    return out


# --- evaluators ------------------------------------------------------------------------------

def is_tree(a: Asset) -> tuple[bool, str]:
    roots = [i for i, b in enumerate(a.bones) if b.parent < 0]
    if len(roots) != 1:
        return False, f"{len(roots)} root bones (want 1)"
    for i, b in enumerate(a.bones):
        seen, j = set(), i
        while j >= 0:
            if j in seen or j >= len(a.bones):
                return False, f"cycle or bad parent index at bone {b.name!r}"
            seen.add(j)
            j = a.bones[j].parent
    names = [b.name for b in a.bones]
    if len(set(names)) != len(names):
        return False, "duplicate bone names"
    return True, f"{len(a.bones)} bones, single root {a.bones[roots[0]].name!r}"


def evaluate_skin(a: Asset) -> list[CheckResult]:
    if not a.bones:
        return [check("skin.bones", False, "no skeleton")]
    out = []
    ok, why = is_tree(a)
    out.append(check("skin.tree", ok, why))
    if a.joints is None or a.weights is None or len(a.weights) != len(a.positions):
        out.append(check("skin.weights_present", False, "missing or mis-sized JOINTS_0/WEIGHTS_0"))
        return out
    w = a.weights.astype(np.float64)
    finite = bool(np.isfinite(w).all()) and bool((w >= -1e-6).all())
    out.append(check("skin.weights_valid", finite, "weights finite and non-negative" if finite
                     else "NaN/inf or negative weights"))
    sums = w.sum(axis=1)
    unweighted = int((sums <= 1e-8).sum())
    out.append(check("skin.every_vertex_weighted", unweighted == 0, f"{unweighted} vertices with no weight"))
    worst = float(np.abs(sums - 1).max())
    out.append(check("skin.weights_sum_to_1", worst <= WEIGHT_SUM_TOL, f"max |Σw - 1| = {worst:.2e}"))
    infl = int((w > 1e-6).sum(axis=1).max())
    out.append(check("skin.max_influences", infl <= MAX_INFLUENCES, f"max influences per vertex = {infl}"))
    bad_j = int((a.joints >= len(a.bones)).sum())
    out.append(check("skin.joint_indices", bad_j == 0, f"{bad_j} joint indices out of range"))
    lo, hi = a.bbox()
    pad = 0.05 * (hi - lo).max()
    heads = np.array([b.head for b in a.bones])
    outside = [b.name for b, h in zip(a.bones, heads, strict=True) if (h < lo - pad).any() or (h > hi + pad).any()]
    out.append(check("skin.bones_inside", not outside,
                     "all bone heads within the mesh bounds" if not outside else f"outside the mesh: {outside[:5]}"))
    return out


def edges(a: Asset) -> np.ndarray:
    f = a.indices.astype(np.int64)
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    return np.unique(np.sort(e, axis=1), axis=0)


def evaluate_deformation(a: Asset, angle: float = DEFORM_ANGLE) -> list[CheckResult]:
    """Rotate each bone by ±angle about two axes. No edge may stretch past MAX_EDGE_STRETCH× (a tear), and no bend
    may stretch more than MAX_STRETCHED_FRACTION of the edges past STRETCH× (a smear)."""
    if not a.bones or a.weights is None:
        return []
    from .asset import axis_angle_quat

    e = edges(a)
    rest = np.linalg.norm(a.positions[e[:, 0]] - a.positions[e[:, 1]], axis=1).astype(np.float64)
    rest = np.where(rest > 1e-9, rest, np.nan)
    worst, where, nan = 1.0, "", False
    spread, spread_where = 0.0, ""
    n = max(len(e), 1)
    for b in a.bones:
        if b.parent < 0:
            continue  # the root moves the whole mesh rigidly
        for axis in ((1, 0, 0), (0, 0, 1)):
            for sgn in (-1, 1):
                p = skin(a, {b.name: axis_angle_quat(axis, sgn * angle)})
                if not np.isfinite(p).all():
                    nan = True
                    continue
                ratio = np.linalg.norm(p[e[:, 0]] - p[e[:, 1]], axis=1) / rest
                r = float(np.nanmax(ratio))
                if r > worst:
                    worst, where = r, f"bone {b.name!r} about {axis}"
                share = float(np.sum(ratio > STRETCH)) / n
                if share > spread:
                    spread, spread_where = share, f"bone {b.name!r} about {axis}"
    out = [check("deform.finite", not nan, "posed vertices finite" if not nan else "NaN while posing")]
    deg = f"±{np.degrees(angle):.0f}°"
    out.append(check("deform.edge_stretch", worst <= MAX_EDGE_STRETCH,
                     f"worst edge {worst:.2f}x at {deg} ({where or 'n/a'}; tear above {MAX_EDGE_STRETCH:g}x)"))
    out.append(check("deform.stretched_share", spread <= MAX_STRETCHED_FRACTION,
                     f"at most {spread:.2%} of edges past {STRETCH:g}x in one bend ({spread_where or 'n/a'}; "
                     f"max {MAX_STRETCHED_FRACTION:.0%})"))
    return out
