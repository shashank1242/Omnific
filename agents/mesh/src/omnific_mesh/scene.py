"""Assemble the final scene.gltf from per-entity artifacts, and the scene-level evaluator.

Contract with the viewer (docs/playback-format.md, "scene.gltf v3 extras"):
- one node per SimSpec entity, named exactly as the entity; the viewer moves it from playback tracks;
- generated meshes are a child node `mesh:<entity>`; their bones `bone:<entity>:<bone>` hang below it;
- node extras.omnific = {source, clips: [{name, kind, drive_state}], joint: [pivot xyz, arm xyz] | [],
  attribution?: {title, author, license, url, source}} (attribution only on reused library models);
- each clip is a glTF animation named `<entity>:<clip>`;
- asset.copyright lists every library model's credit line.
A scene with only procedural assets is written by gltf.build_gltf exactly as before (demos are unchanged).
"""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Callable

from omnific_common.contracts import CheckResult, MeshArtifact
from omnific_sim import visual
from omnific_sim.spec import SimSpec

from .asset import Asset, _Writer, add_asset, from_glb
from .assets import GENERATORS, SHAPE_ASSET, sized
from .gltf import build_gltf, rest_positions
from .library.licence import credit
from .meshops import fit_longest

MAX_SCENE_BYTES = 50 * 1024 * 1024


def _procedural_asset(asset_id: str) -> Asset:
    pos, nrm, idx = GENERATORS[asset_id]()
    return Asset(positions=pos, normals=nrm, indices=idx.reshape(-1, 3), base_color=(0.7, 0.7, 0.75, 1.0))


def metric_geometry(spec: SimSpec, length_scale: float | None) -> dict[str, tuple]:
    """Entities with true geometry: name -> (geometry, size in scene units, rest centre in scene units or None, enlargement).
    A body too small to see next to the rest is drawn larger by a uniform factor (visual.enlarge_factor)."""
    if not length_scale:
        return {}
    units = {v.name: v.unit for v in spec.state}
    out = {}
    for e in spec.entities:
        found = visual.geometry(e)
        if found is None:
            continue
        g, size = found
        place = visual.static_place(e, units)
        scene_size = [s * length_scale for s in size]
        k = visual.enlarge_factor(max(scene_size), visual.MIN_VISIBLE_OBJECT)
        out[e.name] = (g, [s * k for s in scene_size], [c * length_scale for c in place] if place else None, k)
    return out


def _yaw(deg: float) -> list[float]:
    a = math.radians(deg) / 2
    return [0.0, math.sin(a), 0.0, math.cos(a)]


DEFAULT_COLOR = (0.7, 0.7, 0.75, 1.0)
# Procedural stand-ins whose colour IS what they are (a model or the dressing restyles everything else).
KIND_COLORS = (("black hole", (0.004, 0.004, 0.005, 1.0)), ("event horizon", (0.004, 0.004, 0.005, 1.0)))


def procedural_color(entity) -> tuple[float, float, float, float]:
    text = f"{entity.kind} {entity.name}".lower()
    return next((c for word, c in KIND_COLORS if word in text), DEFAULT_COLOR)


def assemble(spec: SimSpec, artifacts: list[MeshArtifact], load: Callable[[str], bytes],
             length_scale: float | None = None) -> tuple[dict, dict]:
    """With `length_scale` (the playback's scene units per metre), entities with geometry are drawn at their true size,
    static ones at their true place and turn: the picture matches the motion. Without it, the MVP unit primitives."""
    by_entity = {a.entity: a for a in artifacts}
    metric = metric_geometry(spec, length_scale)
    rest = rest_positions(spec.entities, length_scale if metric else None)
    for name, (_, _, place, _k) in metric.items():
        if place is not None:
            rest[name] = place
    ents = [(e.name, SHAPE_ASSET[metric[e.name][0].shape] if e.name in metric and (
                by_entity.get(e.name) is None or by_entity[e.name].source == "procedural")
             else by_entity[e.name].asset if e.name in by_entity else "box", list(e.bound_state))
            for e in spec.entities]
    if not metric and all(by_entity.get(e.name) is None or by_entity[e.name].source == "procedural"
                          for e in spec.entities) \
            and not any(by_entity.get(e.name) and by_entity[e.name].clips for e in spec.entities):
        return build_gltf(ents, rest)  # byte-identical to the MVP scene (extras.omnific absent = procedural)

    w = _Writer()
    gltf: dict = {"asset": {"version": "2.0", "generator": "omnific-mesh"}, "scene": 0, "nodes": []}
    roots, assets = [], {}
    entity_of = {e.name: e for e in spec.entities}
    for k, (name, asset_id, bound) in enumerate(ents):
        art = by_entity.get(name)
        node = len(gltf["nodes"])
        gltf["nodes"].append({"name": name, "translation": rest.get(name, [2.0 * k, 0.0, 0.0]),
                              "extras": {"asset": asset_id, "bound_state": bound, "omnific": _extras(art)}})
        geo = metric.get(name)
        if geo is not None and geo[3] > 1.0:  # drawn larger than life so it can be seen: the viewer says so
            gltf["nodes"][node]["extras"]["omnific"]["enlarged"] = round(geo[3], 3)
        if geo is not None and geo[0].yaw_deg:
            gltf["nodes"][node]["rotation"] = _yaw(geo[0].yaw_deg)
        roots.append(node)
        if art is not None and art.source != "procedural" and art.glb_key:
            a = from_glb(load(art.glb_key))
            if geo is not None:  # a model keeps its proportions; its longest side gets the object's true size
                fit_longest(a, max(geo[1]))
            assets[name] = f"{art.source}:{art.glb_key}"
        elif geo is not None:
            pos, nrm, idx = sized(geo[0].shape, geo[1])
            a = Asset(positions=pos, normals=nrm, indices=idx.reshape(-1, 3), base_color=procedural_color(entity_of[name]))
            assets[name] = asset_id
        else:
            a = _procedural_asset(asset_id)
            if procedural_color(entity_of[name]) != DEFAULT_COLOR:
                a.base_color = procedural_color(entity_of[name])
            assets[name] = asset_id
        add_asset(w, gltf, a, name, parent_node=node, anim_prefix=f"{name}:")
    gltf["scenes"] = [{"name": "scene", "nodes": roots}]
    credits = [credit(a.attribution.title, a.attribution.author, a.attribution.license, a.attribution.url,
                      a.attribution.source) for a in artifacts if a.attribution is not None and a.source == "library"]
    if credits:
        gltf["asset"]["copyright"] = "; ".join(credits)
    blob = bytes(w.blob)
    gltf["bufferViews"], gltf["accessors"] = w.views, w.accessors
    gltf["buffers"] = [{"byteLength": len(blob),
                        "uri": "data:application/octet-stream;base64," + base64.b64encode(blob).decode("ascii")}]
    return {k: v for k, v in gltf.items() if v != []}, assets


def _extras(art: MeshArtifact | None) -> dict:
    if art is None:
        return {"source": "procedural", "clips": [], "joint": []}
    out = {"source": art.source, "joint": list(art.joint),
           "clips": [{"name": c.name, "kind": c.kind, "drive_state": c.drive_state} for c in art.clips]}
    if art.attribution is not None and art.source == "library":
        out["attribution"] = art.attribution.model_dump()
    return out


def dumps(gltf: dict) -> bytes:
    return json.dumps(gltf).encode("utf-8")


def evaluate_scene(spec: SimSpec, gltf: dict, size: int) -> list[CheckResult]:
    def check(name, ok, detail):
        return CheckResult(name=name, passed=bool(ok), detail=detail)

    out = []
    names = {n.get("name") for n in gltf.get("nodes", [])}
    missing = [e.name for e in spec.entities if e.name not in names]
    out.append(check("scene.entities", not missing, f"missing nodes for {missing}" if missing
                     else f"all {len(spec.entities)} entities have a node"))
    nodes = gltf.get("nodes", [])
    roots = {nodes[i].get("name") for i in gltf.get("scenes", [{}])[0].get("nodes", []) if 0 <= i < len(nodes)}
    not_root = [e.name for e in spec.entities if e.name in names and e.name not in roots]
    out.append(check("scene.entity_roots", not not_root,
                     "entity nodes are scene roots (the viewer sets their world position)" if not not_root
                     else f"not at the scene root: {not_root}"))
    out.append(check("scene.size", size <= MAX_SCENE_BYTES, f"{size / 1e6:.1f} MB (max {MAX_SCENE_BYTES / 1e6:.0f} MB)"))
    out.extend(_structure(gltf))
    clips = [a["name"] for a in gltf.get("animations", [])]
    declared = [f"{n['name']}:{c['name']}" for n in gltf.get("nodes", [])
                for c in n.get("extras", {}).get("omnific", {}).get("clips", [])]
    out.append(check("scene.clips_declared", sorted(clips) == sorted(declared),
                     f"{len(clips)} animations, {len(declared)} declared in extras"))
    return out


def _structure(gltf: dict) -> list[CheckResult]:
    """Structural glTF validation: every index points at something that exists, accessors fit their views."""
    errors: list[str] = []
    n = {k: len(gltf.get(k, [])) for k in ("nodes", "meshes", "accessors", "bufferViews", "buffers", "materials",
                                            "textures", "images", "samplers", "skins")}
    buf_len = [b.get("byteLength", 0) for b in gltf.get("buffers", [])]
    size_of = {5126: 4, 5125: 4, 5123: 2, 5122: 2, 5121: 1, 5120: 1}
    comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
    for i, v in enumerate(gltf.get("bufferViews", [])):
        if v["buffer"] >= n["buffers"] or v.get("byteOffset", 0) + v["byteLength"] > buf_len[v["buffer"]]:
            errors.append(f"bufferView {i} outside its buffer")
    for i, a in enumerate(gltf.get("accessors", [])):
        sparse = a.get("sparse") or {}
        for part in ("indices", "values"):  # sparse storage must point at views that exist
            view = (sparse.get(part) or {}).get("bufferView")
            if sparse and (not isinstance(view, int) or not 0 <= view < n["bufferViews"]):
                errors.append(f"accessor {i} sparse {part} -> missing bufferView")
        if "bufferView" not in a:  # all-zero base (sparse-only or empty): no base storage to bound-check
            continue
        if a["bufferView"] >= n["bufferViews"]:
            errors.append(f"accessor {i} -> missing bufferView")
            continue
        need = a["count"] * comps[a["type"]] * size_of.get(a["componentType"], 4)
        if need > gltf["bufferViews"][a["bufferView"]]["byteLength"]:
            errors.append(f"accessor {i} overruns its bufferView")
    for i, node in enumerate(gltf.get("nodes", [])):
        if "mesh" in node and node["mesh"] >= n["meshes"]:
            errors.append(f"node {i} -> missing mesh")
        if "skin" in node and node["skin"] >= n["skins"]:
            errors.append(f"node {i} -> missing skin")
        errors += [f"node {i} -> missing child {c}" for c in node.get("children", []) if c >= n["nodes"]]
    for m in gltf.get("meshes", []):
        for p in m["primitives"]:
            refs = list(p["attributes"].values()) + ([p["indices"]] if "indices" in p else [])
            errors += [f"mesh {m.get('name')} -> missing accessor {r}" for r in refs if r >= n["accessors"]]
            if "material" in p and p["material"] >= n["materials"]:
                errors.append(f"mesh {m.get('name')} -> missing material")
    for s in gltf.get("skins", []):
        errors += [f"skin {s.get('name')} -> missing joint {j}" for j in s["joints"] if j >= n["nodes"]]
    for a in gltf.get("animations", []):
        for ch in a["channels"]:
            if ch["sampler"] >= len(a["samplers"]) or ch["target"].get("node", 0) >= n["nodes"]:
                errors.append(f"animation {a.get('name')} has a dangling channel")
    for t in gltf.get("textures", []):
        if t.get("source", 0) >= n["images"] or t.get("sampler", 0) >= max(n["samplers"], 1):
            errors.append("texture -> missing image/sampler")
    # every node has at most one parent (glTF requires a forest)
    errors += [f"scene root {i} is not a node" for i in gltf.get("scenes", [{}])[0].get("nodes", [])
               if not 0 <= i < n["nodes"]]
    parents: dict[int, int] = {}
    for i, node in enumerate(gltf.get("nodes", [])):
        for c in node.get("children", []):
            if c in parents:
                errors.append(f"node {c} has two parents")
            parents[c] = i
    return [CheckResult(name="scene.gltf_structure", passed=not errors,
                        detail="valid structure" if not errors else "; ".join(errors[:8]))]
