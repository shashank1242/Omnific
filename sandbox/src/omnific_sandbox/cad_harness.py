"""CAD runner harness. Executed INSIDE the isolated CAD runner (container, or subprocess in local dev), never in a
worker: it executes LLM-written build123d code.

Usage: python cad_harness.py <input_dir> <output_dir>
  input_dir:  cad.py (defines build() -> {label: part} and optionally JOINTS)
  output_dir: result.zip = cad.json (parts, joints, interference), parts.npz (per-part triangle meshes), model.step
Exit 0 = success; contract violations exit 2 with a message the CAD writer can act on.
Next to this file at runtime: harness.py (result emission for Kubernetes) and omnific_cad.py (the joint helper).
"""

import importlib.util
import io
import json
import math
import sys
import traceback
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import _excepthook, _mark_stderr, emit_mode, emit_result, fail

MAX_PARTS = 16
KINDS = ("revolute", "slider", "fastened")
MOTIONS = ("none", "spin", "oscillate")
SWEEP_SAMPLES = 9
TESS_REL = 0.002  # linear tessellation tolerance, fraction of the model's diagonal


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("cad_model", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        _mark_stderr()
        traceback.print_exc()
        sys.exit(1)
    return module


def is_valid(shape) -> bool:
    v = getattr(shape, "is_valid", None)
    return bool(v() if callable(v) else v)


def collect_parts(module) -> dict[str, dict]:
    build = getattr(module, "build", None)
    if build is None:
        fail("cad.py does not define build()")
    try:
        raw = build()
    except BaseException:  # noqa: BLE001
        _mark_stderr()
        traceback.print_exc()
        sys.exit(1)
    if not isinstance(raw, dict) or not raw:
        fail(f"build() must return a non-empty dict {{label: part}}, got {type(raw).__name__}")
    if len(raw) > MAX_PARTS:
        fail(f"build() returned {len(raw)} parts; keep it to at most {MAX_PARTS} simple parts")
    parts = {}
    for label, value in raw.items():
        if not isinstance(label, str) or not label.strip():
            fail(f"part labels must be non-empty strings, got {label!r}")
        shape, color = (value.get("shape"), value.get("color", "#9aa0a6")) if isinstance(value, dict) else (value,
                                                                                                          "#9aa0a6")
        if not hasattr(shape, "volume") or not hasattr(shape, "tessellate"):
            fail(f"part {label!r} is not a build123d shape (got {type(shape).__name__})")
        parts[label] = {"shape": shape, "color": color}
    return parts


def check_joints(raw, labels: set[str]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list | tuple):
        fail("JOINTS must be a list of revolute(...)/slider(...)/fastened(...) entries")
    joints, parent_of, names = [], {}, set()
    for j in raw:
        if not isinstance(j, dict) or j.get("kind") not in KINDS:
            fail(f"bad joint {j!r}: build joints with omnific_cad.revolute / slider / fastened")
        name = j.get("name")
        if not name or name in names:
            fail(f"joint names must be unique and non-empty (got {name!r})")
        names.add(name)
        for end in ("parent", "child"):
            if j.get(end) not in labels:
                fail(f"joint {name!r}: {end} {j.get(end)!r} is not a part label ({sorted(labels)})")
        if j["parent"] == j["child"]:
            fail(f"joint {name!r} connects {j['child']!r} to itself")
        if j["child"] in parent_of:
            fail(f"part {j['child']!r} is the child of two joints; the joint graph must be a tree")
        parent_of[j["child"]] = j["parent"]
        lo, hi = j["limits"]
        if not (math.isfinite(lo) and math.isfinite(hi)):
            fail(f"joint {name!r}: limits must be finite")
        if j["kind"] != "fastened" and not lo < hi:
            fail(f"joint {name!r}: limits must satisfy lo < hi, got {[lo, hi]}")
        if j["kind"] != "fastened" and math.hypot(*j["axis"]) < 1e-9:
            fail(f"joint {name!r}: axis is zero")
        if j.get("motion", "none") not in MOTIONS:
            fail(f"joint {name!r}: motion must be one of {MOTIONS}")
        if j.get("motion", "none") != "none" and j["kind"] != "revolute":
            fail(f"joint {name!r}: only revolute joints can animate (clips rotate bones); use motion='none'")
        if not all(math.isfinite(x) for x in [*j["origin"], *j["axis"]]):
            fail(f"joint {name!r}: origin/axis must be finite")
        joints.append(j)
    for start in parent_of:  # cycles
        seen, node = set(), start
        while node in parent_of:
            if node in seen:
                fail(f"joint graph has a cycle through {node!r}; it must be a tree")
            seen.add(node)
            node = parent_of[node]
    return joints


def bbox(shape):
    b = shape.bounding_box()
    return [b.min.X, b.min.Y, b.min.Z], [b.max.X, b.max.Y, b.max.Z]


def overlap(b1, b2) -> bool:
    return all(b1[0][k] <= b2[1][k] and b2[0][k] <= b1[1][k] for k in range(3))


def common_volume(a, b) -> float:
    try:
        r = a & b  # intersect() returns a ShapeList in build123d >= 0.13; `&` returns one shape
        return float(abs(r.volume)) if r is not None else 0.0
    except Exception:  # noqa: BLE001 - a failed boolean is reported as unknown (0), not as a crash
        return 0.0


def subtree(child: str, joints: list[dict]) -> set[str]:
    out, frontier = {child}, [child]
    while frontier:
        p = frontier.pop()
        for j in joints:
            if j["parent"] == p and j["child"] not in out:
                out.add(j["child"])
                frontier.append(j["child"])
    return out


def moved(shape, joint: dict, value: float):
    from build123d import Axis, Location, Vector

    if joint["kind"] == "revolute":
        return shape.rotate(Axis(tuple(joint["origin"]), tuple(joint["axis"])), value)
    n = math.hypot(*joint["axis"])
    d = Vector(*[a / n * value for a in joint["axis"]])
    return shape.moved(Location(d))


def interference(parts: dict, joints: list[dict]) -> dict:
    """Overlap volumes (fraction of the smaller part) at rest and across each moving joint's range."""
    vols = {k: max(float(abs(v["shape"].volume)), 1e-18) for k, v in parts.items()}
    boxes = {k: bbox(v["shape"]) for k, v in parts.items()}
    linked = {frozenset((j["parent"], j["child"])): j["kind"] for j in joints}
    rest = []
    labels = sorted(parts)
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            if not overlap(boxes[a], boxes[b]):
                continue
            v = common_volume(parts[a]["shape"], parts[b]["shape"])
            if v > 0:
                rest.append({"a": a, "b": b, "frac": v / min(vols[a], vols[b]),
                             "link": linked.get(frozenset((a, b)), "")})
    sweep = []
    for j in joints:
        if j["kind"] == "fastened":
            continue
        lo, hi = j["limits"]
        values = ([360.0 * k / 8 for k in range(1, 8)] if j.get("motion") == "spin"
                  else [lo + (hi - lo) * k / (SWEEP_SAMPLES - 1) for k in range(SWEEP_SAMPLES)])
        moving = subtree(j["child"], joints)
        for value in values:
            if abs(value) < 1e-12:
                continue  # the rest pose is covered above
            for m in sorted(moving):
                shape = moved(parts[m]["shape"], j, value)
                box = bbox(shape)
                for other in labels:
                    if other in moving or not overlap(box, boxes[other]):
                        continue
                    v = common_volume(shape, parts[other]["shape"])
                    if v > 0:
                        sweep.append({"joint": j["name"], "value": value, "a": m, "b": other,
                                      "frac": v / min(vols[m], vols[other]),
                                      "link": linked.get(frozenset((m, other)), "")})
    return {"rest": rest, "sweep": sweep}


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        fail("usage: cad_harness.py <input_dir> <output_dir>", 64)
    in_dir, out_dir = Path(argv[1]), Path(argv[2])
    import numpy as np
    from build123d import Compound, export_step

    module = load_module(in_dir / "cad.py")
    parts = collect_parts(module)
    joints = check_joints(getattr(module, "JOINTS", None), set(parts))

    report_parts, meshes = [], {}
    all_lo, all_hi = [math.inf] * 3, [-math.inf] * 3
    for label, p in parts.items():
        lo, hi = bbox(p["shape"])
        all_lo = [min(a, b) for a, b in zip(all_lo, lo, strict=True)]
        all_hi = [max(a, b) for a, b in zip(all_hi, hi, strict=True)]
    diag = math.dist(all_lo, all_hi) or 1.0
    for i, (label, p) in enumerate(parts.items()):
        shape = p["shape"]
        verts, tris = shape.tessellate(TESS_REL * diag, 0.2)
        v = np.array([[q.X, q.Y, q.Z] for q in verts], np.float32).reshape(-1, 3)
        f = np.array(tris, np.int64).reshape(-1, 3)
        meshes[f"v{i}"], meshes[f"f{i}"] = v, f
        lo, hi = bbox(shape)
        report_parts.append({"label": label, "index": i, "color": p["color"], "valid": is_valid(shape),
                             "volume": float(abs(shape.volume)), "bbox_min": lo, "bbox_max": hi,
                             "n_tris": len(f)})
    report = {"units": "m", "parts": report_parts, "joints": joints, "interference": interference(parts, joints)}

    out_dir.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    np.savez_compressed(buf, **meshes)
    step = out_dir / "model.step"
    try:
        export_step(Compound([p["shape"] for p in parts.values()]), str(step))
        step_bytes = step.read_bytes()
    except Exception as e:  # noqa: BLE001 - STEP is a download convenience, not required for the scene
        step_bytes = b""
        report["step_error"] = f"{type(e).__name__}: {e}"
    with zipfile.ZipFile(out_dir / "result.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("cad.json", json.dumps(report))
        z.writestr("parts.npz", buf.getvalue())
        if step_bytes:
            z.writestr("model.step", step_bytes)
    if emit_mode():
        emit_result(out_dir / "result.zip")


if __name__ == "__main__":
    sys.excepthook = _excepthook
    main(sys.argv)
