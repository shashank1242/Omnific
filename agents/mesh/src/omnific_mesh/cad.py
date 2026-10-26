"""CAD route: an OpenAI model writes a build123d script; the sandbox runs it (cad_harness.py, never this worker);
this module turns the sandbox output into an Asset, rigs it from the script's joints (one bone per moving joint,
each part rigidly skinned to its bone), makes kinematic clips for joints that spin or oscillate, and judges it all.

CAD joints drive mechanisms inside one entity (wheels, propellers, pistons, gears). An entity that swings about a
pivot in the simulation still gets that motion from its SimSpec physics joint, as for every route.
"""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
from omnific_common.contracts import AssetPlan, CadWriteInput, CadWriteOutput, CheckResult, ClipPlan
from omnific_common.llm import LLM, Role

from . import anim, meshops, rig
from .asset import Asset, Bone, Clip, axis_angle_quat

# Overlap allowed between two parts, as a fraction of the smaller part: joined by a moving joint (a pin in its
# hole, an axle in a hub) or not joined at all. Fastened pairs may overlap freely (they are one rigid body).
JOINED_TOL, LOOSE_TOL = 0.05, 0.01
SPIN_PERIOD, OSCILLATE_PERIOD = 1.0, 2.0

WRITER_INSTRUCTIONS = """\
You write parametric CAD models as Python code for build123d, for a physics-simulation viewer. Your module must:
- `from build123d import *` and `from omnific_cad import part, revolute, slider, fastened` (plus `math` if needed).
  No other imports, no file, network or OS access, no printing.
- define `build()` returning a dict {label: part(shape, color="#rrggbb")} with 1-12 simple parts.
- optionally define `JOINTS = [...]` connecting parts (the joint graph must be a tree):
    revolute(name, parent, child, origin=(x, y, z), axis=(x, y, z), limits=(lo_deg, hi_deg), motion=..., clip=...)
    slider(name, parent, child, origin, axis, limits=(lo_m, hi_m))
    fastened(name, parent, child)
  motion "spin" = continuous rotation (wheels, propellers, gears), "oscillate" = out from the rest pose and back
  within limits, "none" = a joint that does not animate. Only revolute joints can animate (sliders: motion "none").
  `clip` = the name of the planned clip that plays it ("" if none).
Conventions: metres, +Y up, the object resting at its rest pose (every joint value 0 = the geometry as built).
build123d algebra API (use only this):
  Box(x_size, y_size, z_size), Cylinder(radius, height) (axis along Z), Sphere(radius), Cone(bottom_r, top_r, h),
  Torus(major_r, minor_r); place with Pos(x, y, z) * shape and Rot(x_deg, y_deg, z_deg) * shape (Rot first then
  Pos: Pos(...) * Rot(...) * shape); combine with a + b (union) and a - b (cut). Shapes are centred on the origin.
  A wheel turning about X: Pos(x, y, z) * Rot(0, 90, 0) * Cylinder(r, width).
Rules that the checks enforce:
- Every part is a valid closed solid with volume.
- Parts must not overlap, except parts joined by a fastened joint; parts joined by a moving joint may touch at the
  joint (at most a few percent of the smaller part). Leave ~2% clearance around moving parts.
- Moving parts must not collide anywhere across their joint range (spinning parts over a full turn).
- Each joint origin lies on its axis of motion where both parts meet (e.g. a wheel's centre).
- Avoid fillets, chamfers, text and thin features: simple solids are judged on shape and proportions.
Return the complete module in `code`, the part labels in `parts` and one sentence in `notes`."""


def render_request(inp: CadWriteInput) -> str:
    plan, spec = inp.plan, inp.spec
    ent = next((e for e in spec.entities if e.name == plan.entity), None)
    lines = [f"Entity: {plan.entity} (kind {plan.kind!r})", f"Design brief: {plan.cad_brief}",
             f"Looks like: {plan.gen_prompt or plan.kind}"]
    if plan.clips:
        lines.append("Planned clips (give the joints that animate them motion + clip=<name>): " + "; ".join(
            f"{c.name} ({c.kind}): {c.description}" for c in plan.clips))
    else:
        lines.append("No clips are planned: joints, if any, use motion='none'.")
    if ent is not None and ent.arm is not None:
        lines.append(f"In the simulation the whole entity swings about a pivot (arm {ent.arm} m); that motion is "
                     "applied to the whole model, so do not add a joint for it.")
    params = ", ".join(f"{p.name}={p.value:g} {p.unit}" for p in spec.parameters)
    if params:
        lines.append(f"Simulation parameters (use real dimensions from these when they apply): {params}")
    if inp.critique:
        lines.append(f"\nYour previous attempt failed these checks; fix them:\n{inp.critique}")
    if inp.previous_code:
        lines.append(f"\nPrevious code:\n```python\n{inp.previous_code}\n```")
    return "\n".join(lines)


async def write(llm: LLM, inp: CadWriteInput) -> CadWriteOutput:
    return await llm.structured(Role.REASONING, WRITER_INSTRUCTIONS, render_request(inp), CadWriteOutput)


# --- sandbox output -> asset ----------------------------------------------------------------------------

def load_result(data: bytes) -> tuple[dict, dict[str, np.ndarray]]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        report = json.loads(z.read("cad.json"))
        meshes = dict(np.load(io.BytesIO(z.read("parts.npz"))))
    return report, meshes


def hex_rgb(color: str) -> np.ndarray:
    """#rrggbb (sRGB) -> linear RGB; grey for anything unparsable."""
    try:
        s = color.strip().lstrip("#")
        srgb = np.array([int(s[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255.0
    except (ValueError, AttributeError, IndexError):
        srgb = np.array([0.6, 0.62, 0.65], np.float32)
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4).astype(np.float32)


def moving(joints: list[dict]) -> list[dict]:
    return [j for j in joints if j["kind"] != "fastened"]


def build_asset(report: dict, meshes: dict[str, np.ndarray]) -> Asset:
    """One mesh with per-part vertex colours; with moving joints, a skeleton (root + one bone per moving joint at
    its origin) and rigid weights (each part follows the bone of the nearest moving joint above it)."""
    parts = report["parts"]
    joints = report["joints"]
    pos, idx, cols, owner = [], [], [], []
    count = 0
    for k, p in enumerate(parts):
        v, f = meshes[f"v{p['index']}"], meshes[f"f{p['index']}"]
        pos.append(v)
        idx.append(f + count)
        cols.append(np.tile(hex_rgb(p["color"]), (len(v), 1)))
        owner.append(np.full(len(v), k))
        count += len(v)
    a = Asset(positions=np.concatenate(pos).astype(np.float32), indices=np.concatenate(idx).astype(np.uint32),
              colors=np.concatenate(cols).astype(np.float32), base_color=(1.0, 1.0, 1.0, 1.0))
    mj = moving(joints)
    if not mj:
        return a
    labels = [p["label"] for p in parts]
    parent_joint = {j["child"]: j for j in joints}
    centre = (a.positions.min(axis=0) + a.positions.max(axis=0)) / 2
    a.bones = [Bone("root", -1, centre.astype(np.float32))]
    bone_of_joint: dict[str, int] = {}

    def part_bone(label: str) -> int:
        j = parent_joint.get(label)
        if j is None:
            return 0
        if j["kind"] == "fastened":
            return part_bone(j["parent"])
        if j["name"] not in bone_of_joint:
            parent = part_bone(j["parent"])
            bone_of_joint[j["name"]] = len(a.bones)
            a.bones.append(Bone(j["name"], parent, np.asarray(j["origin"], np.float32)))
        return bone_of_joint[j["name"]]

    bones = np.array([part_bone(lab) for lab in labels])
    a.joints = np.zeros((len(a.positions), 4), np.uint16)
    a.joints[:, 0] = bones[np.concatenate(owner)]
    a.weights = np.zeros((len(a.positions), 4), np.float32)
    a.weights[:, 0] = 1.0
    return a


def kinematic_clips(report: dict, plans: list[ClipPlan]) -> tuple[list[tuple[Clip, ClipPlan, float | None]],
                                                                     list[str]]:
    """(clip, plan, max joint angle in rad or None for unbounded spin) per planned clip some joint animates."""
    out, notes = [], []
    for cp in plans:
        js = [j for j in moving(report["joints"]) if j.get("clip") == cp.name and j.get("motion", "none") != "none"]
        js = [j for j in js if j["kind"] == "revolute"]  # the harness rejects animated sliders
        if not js:
            notes.append(f"clip {cp.name!r}: no CAD joint animates it")
            continue
        spin = any(j["motion"] == "spin" for j in js)
        period = SPIN_PERIOD if spin else OSCILLATE_PERIOD
        t = np.linspace(0, period, int(period * anim.FPS) + 1).astype(np.float32)
        rot, limit = {}, 0.0
        for j in js:
            axis = np.asarray(j["axis"], float)
            axis = axis / np.linalg.norm(axis)
            if j["motion"] == "spin":
                angle = 2 * np.pi * t / period
            else:  # out from the rest pose (0) to 80% of the wider side of the range and back
                lo, hi = np.radians(j["limits"])
                reach = 0.8 * (hi if abs(hi) >= abs(lo) else lo) if lo <= 0 <= hi else 0.0
                angle = reach * (1 - np.cos(2 * np.pi * t / period)) / 2
                limit = max(limit, float(np.abs(angle).max()))
            rot[j["name"]] = axis_angle_quat(axis, angle)
        out.append((Clip(name=cp.name, times=t, rotations=rot), cp, None if spin else limit + 1e-3))
    return out, notes


# --- evaluation -----------------------------------------------------------------------------------------------

def check(name: str, ok: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=bool(ok), detail=detail)


def _worst(items: list[dict]) -> tuple[float, str]:
    """Worst disallowed overlap: fraction beyond its tolerance, and a description."""
    worst, what = 0.0, ""
    for it in items:
        if it.get("link") == "fastened":
            continue
        tol = JOINED_TOL if it.get("link") else LOOSE_TOL
        if it["frac"] - tol > worst:
            worst = it["frac"] - tol
            where = f" at {it['joint']}={it['value']:g}" if "joint" in it else ""
            what = f"{it['a']} overlaps {it['b']} by {it['frac']:.1%}{where} (allowed {tol:.0%})"
    return worst, what


def evaluate_report(report: dict, expected_parts: list[str]) -> list[CheckResult]:
    parts = report["parts"]
    labels = [p["label"] for p in parts]
    out = []
    if expected_parts:
        same = set(labels) == set(expected_parts)
        out.append(check("cad.parts", same, f"parts {labels}" if same
                         else f"build() returned {labels}, but the design lists {expected_parts}"))
    bad = [p["label"] for p in parts if not p["valid"] or p["volume"] <= 0]
    out.append(check("cad.solids", not bad, f"{len(parts)} valid solids" if not bad
                     else f"invalid or empty solids: {bad}"))
    rest, what = _worst(report["interference"]["rest"])
    out.append(check("cad.interference", rest <= 0, "no part overlaps at rest" if rest <= 0 else what))
    sweep, what = _worst(report["interference"]["sweep"])
    n_moving = len(moving(report["joints"]))
    out.append(check("cad.joint_sweep", sweep <= 0,
                     f"{n_moving} moving joints sweep their range without collisions" if sweep <= 0 else what))
    return out


def evaluate(report: dict, a: Asset, plan: AssetPlan, expected_parts: list[str],
             clips: list[tuple[Clip, ClipPlan, float | None]]) -> list[CheckResult]:
    out = evaluate_report(report, expected_parts)
    out += meshops.evaluate_generated(a)
    if a.bones:
        skin = rig.evaluate_skin(a)
        out += skin
        if all(c.passed for c in skin):
            out += rig.evaluate_deformation(a)
    for clip, cp, limit in clips:
        cc = anim.evaluate_clip(a, clip, "spin" if limit is None else cp.kind, max_angle=limit)
        out += [c.model_copy(update={"name": f"{cp.name}.{c.name}"}) for c in cc]
    return out
