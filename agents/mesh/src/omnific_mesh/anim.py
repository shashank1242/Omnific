"""Animation clips: procedural fallbacks for skinned assets, and the motion evaluators.

Clips are secondary motion only. Positions come from the solver, so root translation must be zero, and the clip's
playback rate is scaled by its `drive_state` in the viewer.
"""

from __future__ import annotations

import numpy as np
from omnific_common.contracts import CheckResult, ClipPlan

from .asset import Asset, Clip, axis_angle_quat

FPS = 30
MAX_JOINT_ANGLE = np.radians(90)
MAX_FRAME_STEP = np.radians(20)
LOOP_TOL = np.radians(2)
MIN_DURATION, MAX_DURATION = 1.0, 5.0
LOOPING = {"idle", "locomotion", "spin"}  # "spin": a CAD joint turning continuously (wheels, propellers)


def check(name: str, ok: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=bool(ok), detail=detail)


def children(a: Asset) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {i: [] for i in range(len(a.bones))}
    for i, b in enumerate(a.bones):
        if b.parent >= 0:
            out[b.parent].append(i)
    return out


def leg_roots(a: Asset) -> list[int]:
    """Bones that start a chain hanging below the body: their subtree's lowest head is in the bottom 40%."""
    lo, hi = a.bbox()
    height = float(hi[1] - lo[1]) or 1.0
    ch = children(a)

    def lowest(i):
        return min([a.bones[i].head[1]] + [lowest(c) for c in ch[i]])

    roots = []
    for i, b in enumerate(a.bones):
        if b.parent < 0:
            continue
        parent_low = a.bones[b.parent].head[1] > lo[1] + 0.4 * height
        if parent_low and lowest(i) < lo[1] + 0.25 * height and b.head[1] < a.bones[b.parent].head[1]:
            roots.append(i)
    return roots


def procedural_clip(a: Asset, plan: ClipPlan) -> Clip:
    """Sine-based clip on the asset's skeleton (see module docstring)."""
    period = {"idle": 2.0, "locomotion": 1.0}.get(plan.kind, 2.0)
    t = np.linspace(0, period, int(period * FPS) + 1)
    phase = 2 * np.pi * t / period
    rot: dict[str, np.ndarray] = {}
    non_root = [i for i, b in enumerate(a.bones) if b.parent >= 0]
    if plan.kind == "locomotion" and (legs := leg_roots(a)):
        ch = children(a)
        for i in legs:
            h = a.bones[i].head
            trot = (h[0] >= 0) == (h[2] >= 0)  # diagonal pairs swing together
            off = 0.0 if trot else np.pi
            rot[a.bones[i].name] = axis_angle_quat((1, 0, 0), np.radians(25) * np.sin(phase + off))
            for c in ch[i]:  # lower segment follows with a lag
                rot[a.bones[c].name] = axis_angle_quat((1, 0, 0), np.radians(15) * np.sin(phase + off - 0.8))
    else:
        amp = {"idle": 3.0, "locomotion": 8.0}.get(plan.kind, 15.0)
        for k, i in enumerate(non_root):
            rot[a.bones[i].name] = axis_angle_quat((0, 0, 1), np.radians(amp) * np.sin(phase + 0.5 * k))
    return Clip(name=plan.name, times=t.astype(np.float32), rotations=rot)


def _rel_angle(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    d = np.abs(np.sum(q1 * q2, axis=-1)) / np.maximum(np.linalg.norm(q1, axis=-1) * np.linalg.norm(q2, axis=-1), 1e-12)
    return 2 * np.arccos(np.clip(d, -1, 1))


def evaluate_clip(a: Asset, clip: Clip, kind: str, max_angle: float | None = MAX_JOINT_ANGLE) -> list[CheckResult]:
    """`max_angle` (rad) bounds every joint's rotation; None for joints that turn without limit (CAD spin)."""
    out = []
    dur = clip.duration
    out.append(check("anim.duration", MIN_DURATION <= dur <= MAX_DURATION,
                     f"{dur:.2f} s (want {MIN_DURATION:g}-{MAX_DURATION:g} s)"))
    mono = bool(len(clip.times) >= 2 and np.all(np.diff(clip.times) > 0))
    out.append(check("anim.times", mono, "keyframe times increasing" if mono else "times not increasing"))
    names = {b.name for b in a.bones}
    unknown = [b for b in clip.rotations if b not in names]
    out.append(check("anim.targets", bool(clip.rotations) and not unknown,
                     f"{len(clip.rotations)} animated bones" + (f"; unknown: {unknown[:5]}" if unknown else "")))
    if not clip.rotations:
        return out
    q = np.stack(list(clip.rotations.values()))  # (bones, k, 4)
    finite = bool(np.isfinite(q).all())
    out.append(check("anim.finite", finite, "finite quaternions" if finite else "NaN/inf rotations"))
    if not finite:
        return out
    norms = np.linalg.norm(q, axis=-1)
    out.append(check("anim.unit_quaternions", bool(np.abs(norms - 1).max() <= 1e-3),
                     f"max | |q| - 1 | = {np.abs(norms - 1).max():.1e}"))
    ident = np.zeros_like(q)
    ident[..., 3] = 1
    peak = float(_rel_angle(q, ident).max())
    out.append(check("anim.joint_limits", max_angle is None or peak <= max_angle,
                     f"max joint rotation {np.degrees(peak):.0f}° (" + (
                         "unbounded joint)" if max_angle is None else f"limit {np.degrees(max_angle):.0f}°)")))
    step = float(_rel_angle(q[:, 1:], q[:, :-1]).max()) if q.shape[1] > 1 else 0.0
    out.append(check("anim.smooth", step <= MAX_FRAME_STEP,
                     f"max frame-to-frame rotation {np.degrees(step):.1f}° (limit {np.degrees(MAX_FRAME_STEP):.0f}°)"))
    if kind in LOOPING:
        gap = float(_rel_angle(q[:, 0], q[:, -1]).max())
        out.append(check("anim.loops", gap <= LOOP_TOL, f"first/last pose differ by {np.degrees(gap):.1f}°"))
    rt = clip.root_translation
    drift = 0.0 if rt is None else float(np.abs(rt).max())
    out.append(check("anim.no_root_motion", drift <= 1e-6,
                     "no root translation (the solver owns position)" if drift <= 1e-6
                     else f"root translates by up to {drift:.3g}"))
    return out


def strip_root_motion(clip: Clip) -> Clip:
    clip.root_translation = None
    return clip


def sample_poses(clip: Clip, n: int = 4) -> list[tuple[float, dict[str, np.ndarray]]]:
    """n evenly spaced poses across the clip (linear interpolation + renormalise)."""
    out = []
    for t in np.linspace(clip.times[0], clip.times[-1], n, endpoint=False):
        pose = {}
        for bone, q in clip.rotations.items():
            qi = np.stack([np.interp(t, clip.times, q[:, c]) for c in range(4)])
            pose[bone] = qi / (np.linalg.norm(qi) or 1.0)
        out.append((float(t), pose))
    return out
