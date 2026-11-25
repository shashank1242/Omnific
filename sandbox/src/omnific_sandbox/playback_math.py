"""Entity poses from playback.json, exactly as the browser viewer computes them (pure Python, no dependencies).

A line-by-line port of apps/web/lib/{playback,articulation,tracks,colormap,clips}.ts so a Blender file plays the
same motion as the viewer. The two implementations are pinned to one golden fixture
(apps/web/lib/fixtures/playback-golden.json), checked by vitest and by pytest, so they cannot drift.

Runs inside the Blender runner (copied next to blender_harness.py), so: stdlib only, no omnific imports.
All coordinates here are the viewer's (glTF: y up). Converting to Blender (z up) is the harness's job.
"""

from __future__ import annotations

import math
import re

CHANNELS = ("position_x", "position_y", "position_z", "rotation_z", "scale", "color", "color_field")


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else math.nan


def is_field(series: dict) -> bool:
    vals = series.get("values") or []
    return bool(vals) and isinstance(vals[0], list)


# --- playback.ts -------------------------------------------------------------------------------------------------

def locate(t: list[float], time: float) -> tuple[int, int, float]:
    last = len(t) - 1
    if last <= 0 or time <= t[0]:
        return 0, 0, 0.0
    if time >= t[last]:
        return last, last, 0.0
    lo, hi = 0, last
    while hi - lo > 1:
        mid = (lo + hi) >> 1
        if t[mid] <= time:
            lo = mid
        else:
            hi = mid
    span = t[hi] - t[lo]
    return lo, hi, ((time - t[lo]) / span if span > 0 else 0.0)


def sample_scalar(values: list[float], t: list[float], time: float) -> float:
    i, j, f = locate(t, time)
    a, b = _num(values[i]), _num(values[j])
    if not math.isfinite(a):
        return b
    if not math.isfinite(b):
        return a
    return a + (b - a) * f


# --- colormap.ts ----------------------------------------------------------------------------------------------

def _hex(h: str) -> tuple[float, float, float]:
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


STOPS = {
    "viridis": [_hex(h) for h in ("440154", "472d7b", "3b528b", "2c728e", "21918c", "28ae80", "5ec962", "addc30",
                                  "fde725")],
    "inferno": [_hex(h) for h in ("000004", "1b0c41", "4a0c6b", "781c6d", "a52c60", "cf4446", "ed6925", "fb9b06",
                                  "f7d13d", "fcffa4")],
}


def normalize(value: float, lo: float, hi: float) -> float:
    return (value - lo) / (hi - lo) if hi > lo else 0.0


def colormap(name: str, x: float) -> tuple[float, float, float]:
    """sRGB colour for x in [0, 1] (clamped; NaN maps to 0)."""
    stops = STOPS[name]
    u = min(1.0, max(0.0, x)) if math.isfinite(x) else 0.0
    p = u * (len(stops) - 1)
    i = min(len(stops) - 2, math.floor(p))
    f = p - i
    a, b = stops[i], stops[i + 1]
    return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f


def colormap_for_unit(unit: str) -> str:
    return "inferno" if re.search(r"kelvin|celsius|fahrenheit|degC|degF|^K$", unit, re.IGNORECASE) else "viridis"


# --- articulation.ts -----------------------------------------------------------------------------------------------

def articulated_order(pb: dict) -> list[dict]:
    """Entities with an `arm` (v2), parents first."""
    by_name = {e["name"]: e for e in pb.get("entities") or []}
    out: list[dict] = []
    done: set[str] = set()

    def visit(e: dict) -> None:
        if e["name"] in done:
            return
        done.add(e["name"])
        parent = by_name.get(e["parent"]) if isinstance(e.get("parent"), str) else None
        if parent:
            visit(parent)
        if e.get("arm"):
            out.append(e)

    for e in pb.get("entities") or []:
        visit(e)
    return out


def _track_position(pb: dict, name: str, time: float, base) -> list[float]:
    b = base(name) if base else None
    p = [b[0], b[1], b[2]] if b else [0.0, 0.0, 0.0]
    for tr in pb.get("tracks") or []:
        if tr["entity"] != name:
            continue
        idx = {"position_x": 0, "position_y": 1, "position_z": 2}.get(tr["channel"], -1)
        s = pb["series"].get(tr["state"])
        if idx < 0 or not s or is_field(s):
            continue
        v = sample_scalar(s["values"], pb["t"], time)
        if math.isfinite(v):
            p[idx] = v * tr["scale"]
    return p


def evaluate_articulation(pb: dict, time: float, base=None) -> dict[str, dict]:
    """{entity: {position, pivot_world, angle}} for articulated entities. Angles are absolute."""
    poses: dict[str, dict] = {}
    for e in articulated_order(pb):
        arm = e["arm"]
        pv = e.get("pivot") or [0.0, 0.0, 0.0]
        pivot_world = [pv[0], pv[1], pv[2]]
        if isinstance(e.get("parent"), str):
            parent = poses.get(e["parent"])
            pp = parent["position"] if parent else _track_position(pb, e["parent"], time, base)
            pivot_world = [pp[0] + pv[0], pp[1] + pv[1], pp[2] + pv[2]]
        angle = 0.0
        for tr in pb.get("tracks") or []:
            if tr["entity"] != e["name"] or tr["channel"] != "rotation_z":
                continue
            s = pb["series"].get(tr["state"])
            if not s or is_field(s):
                continue
            v = sample_scalar(s["values"], pb["t"], time)
            if math.isfinite(v):
                angle = v * tr["scale"]
        c, sn = math.cos(angle), math.sin(angle)
        position = [pivot_world[0] + arm[0] * c - arm[1] * sn,
                    pivot_world[1] + arm[0] * sn + arm[1] * c,
                    pivot_world[2] + arm[2]]
        poses[e["name"]] = {"position": position, "pivot_world": pivot_world, "angle": angle}
    return poses


# --- tracks.ts -----------------------------------------------------------------------------------------------

def evaluate_poses(pb: dict, time: float, base=None) -> dict[str, dict]:
    """{entity: {position: {x?, y?, z?}, rotation_z?, scale?, color?}}: the viewer's EntityPose (no colour fields).

    `position` axes that are absent keep the node's base translation, exactly as in the viewer."""
    art = evaluate_articulation(pb, time, base)
    poses: dict[str, dict] = {}
    for tr in pb.get("tracks") or []:
        s = pb["series"].get(tr["state"])
        if not s:
            continue
        pose = poses.setdefault(tr["entity"], {"position": {}})
        if tr["channel"] == "color_field" or is_field(s):
            continue
        v = sample_scalar(s["values"], pb["t"], time)
        if not math.isfinite(v):
            continue
        ch = tr["channel"]
        if ch in ("position_x", "position_y", "position_z"):
            if tr["entity"] not in art:
                pose["position"][ch[-1]] = v * tr["scale"]
        elif ch == "rotation_z":
            if tr["entity"] not in art:
                pose["rotation_z"] = v * tr["scale"]
        elif ch == "scale":
            pose["scale"] = 0.5 + 1.0 * normalize(v, tr["min"], tr["max"])
        elif ch == "color":
            pose["color"] = list(colormap(colormap_for_unit(s.get("unit", "")), normalize(v, tr["min"], tr["max"])))
    for name, a in art.items():
        pose = poses.setdefault(name, {"position": {}})
        pose["position"] = {"x": a["position"][0], "y": a["position"][1], "z": a["position"][2]}
        pose["rotation_z"] = a["angle"]
    return poses


def resolve_pose(pose: dict, base_position, base_rotation) -> tuple[list[float], list[float], float | None]:
    """The node's local translation and XYZ Euler rotation, as Viewport.tsx applies a pose over the node's base."""
    p = pose.get("position") or {}
    position = [p.get("x", base_position[0]), p.get("y", base_position[1]), p.get("z", base_position[2])]
    rz = pose.get("rotation_z")
    rotation = [base_rotation[0], base_rotation[1], base_rotation[2] if rz is None else rz]
    return position, rotation, pose.get("scale")


def driven_entities(pb: dict) -> list[str]:
    """Entity names the viewer poses: the playback's entities, or (v1 files without entities) the tracked ones."""
    ents = [e["name"] for e in pb.get("entities") or []]
    if ents:
        return ents
    seen: list[str] = []
    for tr in pb.get("tracks") or []:
        if tr["entity"] not in seen:
            seen.append(tr["entity"])
    return seen


# --- clips.ts --------------------------------------------------------------------------------------------------

def drive_reference(pb: dict, state: str) -> float:
    s = pb["series"].get(state)
    if not s or is_field(s):
        return 0.0
    return max((abs(v) for v in s["values"] if _finite(v)), default=0.0)


def clip_rate(pb: dict, drive_state: str, time: float, reference: float) -> float:
    if not drive_state:
        return 1.0
    s = pb["series"].get(drive_state)
    if not s or is_field(s) or reference <= 0:
        return 1.0
    v = sample_scalar(s["values"], pb["t"], time)
    return abs(v) / reference if math.isfinite(v) else 0.0


def advance_phase(phase: float, dt: float, rate: float, duration: float) -> float:
    if not duration > 0:
        return 0.0
    nxt = phase + dt * rate
    return ((nxt % duration) + duration) % duration


# --- helpers for the harness ----------------------------------------------------------------------------------

# Seconds of wall clock a full run takes at 1x in the viewer (apps/web/components/player.ts RUN_SECONDS).
RUN_SECONDS = 10


def frame_times(pb: dict, fps: int) -> list[float]:
    """Simulation time of every Blender frame. Like the viewer, the whole run plays in RUN_SECONDS at 1x, so a
    3-second pendulum and a 3-year orbit both take 10 s; frame 1 is t[0] and the last frame is t[-1]."""
    t = pb["t"]
    t0, t1 = float(t[0]), float(t[-1])
    n = RUN_SECONDS * fps + 1 if t1 > t0 else 1
    return [t0 + (t1 - t0) * i / (n - 1) for i in range(n)] if n > 1 else [t0]
