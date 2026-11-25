"""Compose the dressed scene.gltf: SceneBuild's scene, untouched, plus what Blender added (stdlib only).

Blender's glTF importer/exporter restructures skinned meshes and bones, so round-tripping the entities through it
could break the viewer's scene.gltf v3 contract (entity roots, `mesh:`/`bone:` children, clip names, extras). So the
entities never round-trip. The dressed file is the original JSON with:
  * a `scenery` root node holding everything Blender's scenery/lighting scripts made (merged from an exported GLB),
    with its lights (KHR_lights_punctual), meshes, materials and textures;
  * per-entity material overrides (base colour, metallic, roughness, emission) on cloned materials;
  * `scenes[0].extras.omnific_scene = {"dressed": true, ...}`.

Runs inside the Blender runner (copied next to blender_harness.py) and in worker-side tests.
"""

from __future__ import annotations

import base64
import copy
import json
import math
import struct

LIGHTS_EXT = "KHR_lights_punctual"
SCENERY_ROOT = "scenery"
DATA_URI = "data:application/octet-stream;base64,"


class MergeError(ValueError):
    pass


# --- containers ------------------------------------------------------------------------------------------------

def glb_to_gltf(data: bytes) -> dict:
    """A GLB as one self-contained glTF dict (its BIN chunk becomes a data-URI buffer)."""
    if len(data) < 20 or data[:4] != b"glTF":
        raise MergeError("not a GLB file")
    (length,) = struct.unpack_from("<I", data, 8)
    off, doc, binchunk = 12, None, b""
    while off + 8 <= min(length, len(data)):
        clen, ctype = struct.unpack_from("<I4s", data, off)
        chunk = data[off + 8: off + 8 + clen]
        if ctype == b"JSON":
            doc = json.loads(chunk.decode("utf-8"))
        elif ctype == b"BIN\x00":
            binchunk = chunk
        off += 8 + clen
    if doc is None:
        raise MergeError("GLB has no JSON chunk")
    for b in doc.get("buffers", []):
        if "uri" not in b:  # the GLB-stored buffer
            b["uri"] = DATA_URI + base64.b64encode(binchunk[: b.get("byteLength", len(binchunk))]).decode("ascii")
    return doc


def dumps(gltf: dict) -> bytes:
    return json.dumps(gltf, separators=(",", ":")).encode("utf-8")


# --- entity snapshot (the protected set) --------------------------------------------------------------------------

def _subtree(gltf: dict, root: int) -> list[int]:
    nodes, out, stack = gltf.get("nodes", []), [], [root]
    while stack:
        i = stack.pop()
        if i in out or not 0 <= i < len(nodes):
            continue
        out.append(i)
        stack.extend(nodes[i].get("children", []))
    return out


def entity_roots(gltf: dict, entities: list[str]) -> dict[str, int]:
    nodes = gltf.get("nodes", [])
    roots = gltf.get("scenes", [{}])[gltf.get("scene", 0)].get("nodes", [])
    return {nodes[i]["name"]: i for i in roots if 0 <= i < len(nodes) and nodes[i].get("name") in entities}


def entity_snapshot(gltf: dict, entities: list[str]) -> dict:
    """Everything about the entities the dressing must not change: node names, hierarchy, transforms, extras,
    skins, geometry accessors and the clip animations. Materials are excluded (the Look agent may restyle them)."""
    nodes, meshes = gltf.get("nodes", []), gltf.get("meshes", [])
    accessors = gltf.get("accessors", [])
    snap: dict = {}
    for name, root in sorted(entity_roots(gltf, entities).items()):
        items = []
        for i in sorted(_subtree(gltf, root), key=lambda k: nodes[k].get("name", "")):
            n = nodes[i]
            geo = []
            if "mesh" in n and 0 <= n["mesh"] < len(meshes):
                for p in meshes[n["mesh"]].get("primitives", []):
                    attrs = {k: accessors[v].get("count") for k, v in sorted(p.get("attributes", {}).items())
                             if 0 <= v < len(accessors)}
                    geo.append(attrs)
            items.append({k: n.get(k) for k in ("name", "translation", "rotation", "scale", "matrix", "extras")}
                         | {"children": sorted(nodes[c].get("name", "") for c in n.get("children", [])
                                               if 0 <= c < len(nodes)),
                            "skinned": "skin" in n, "geometry": geo})
        snap[name] = items
    snap["#animations"] = sorted(a.get("name", "") for a in gltf.get("animations", []))
    return snap


# --- material overrides ---------------------------------------------------------------------------------------

def _mesh_users(gltf: dict) -> dict[int, list[int]]:
    users: dict[int, list[int]] = {}
    for i, n in enumerate(gltf.get("nodes", [])):
        if "mesh" in n:
            users.setdefault(n["mesh"], []).append(i)
    return users


def apply_material_overrides(gltf: dict, entities: list[str], overrides: dict[str, dict]) -> list[str]:
    """Restyle each entity's materials in place: clone the materials (and any mesh shared with another entity) so an
    override never leaks onto a different entity. Returns notes. Override keys: base_color [r,g,b,a] (linear),
    metallic, roughness, emissive [r,g,b], emissive_strength."""
    notes: list[str] = []
    nodes, meshes = gltf.setdefault("nodes", []), gltf.setdefault("meshes", [])
    materials = gltf.setdefault("materials", [])
    roots = entity_roots(gltf, entities)
    users = _mesh_users(gltf)
    for entity, ov in overrides.items():
        if entity not in roots or not ov:
            if entity not in roots:
                notes.append(f"no entity named {entity!r}: override ignored")
            continue
        sub = set(_subtree(gltf, roots[entity]))
        cloned: dict[int | None, int] = {}
        for i in sorted(sub):
            n = nodes[i]
            if "mesh" not in n:
                continue
            m = n["mesh"]
            if any(u not in sub for u in users.get(m, [])):  # shared with another entity: give this one a copy
                meshes.append(copy.deepcopy(meshes[m]))
                n["mesh"] = m = len(meshes) - 1
                users.setdefault(m, []).append(i)
            for prim in meshes[m].get("primitives", []):
                src = prim.get("material")
                if src not in cloned:
                    base = copy.deepcopy(materials[src]) if src is not None and 0 <= src < len(materials) else {
                        "pbrMetallicRoughness": {}}
                    base["name"] = f"{entity}:{base.get('name', 'material')}"
                    _apply(base, ov)
                    materials.append(base)
                    cloned[src] = len(materials) - 1
                prim["material"] = cloned[src]
    return notes


def _num(v, lo=0.0, hi=1.0, default=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, f)) if math.isfinite(f) else default


def _apply(mat: dict, ov: dict) -> None:
    pbr = mat.setdefault("pbrMetallicRoughness", {})
    bc = ov.get("base_color")
    if isinstance(bc, (list, tuple)) and len(bc) in (3, 4):
        rgba = [_num(c, default=1.0) for c in bc] + ([1.0] if len(bc) == 3 else [])
        pbr["baseColorFactor"] = rgba
        if rgba[3] < 1.0:
            mat["alphaMode"] = "BLEND"
    for key, field in (("metallic", "metallicFactor"), ("roughness", "roughnessFactor")):
        v = _num(ov.get(key))
        if v is not None:
            pbr[field] = v
    em = ov.get("emissive")
    if isinstance(em, (list, tuple)) and len(em) == 3:
        factor = [_num(c, default=0.0) for c in em]
        strength = _num(ov.get("emissive_strength"), 0.0, 100.0)
        exts = mat.setdefault("extensions", {})
        exts.pop("KHR_materials_emissive_strength", None)  # never keep a strength inherited from the clone
        if strength is not None and strength > 1.0:
            exts["KHR_materials_emissive_strength"] = {"emissiveStrength": strength}
        elif strength is not None:  # <= 1 (zero turns it off): fold into the factor
            factor = [c * strength for c in factor]
        if not exts:
            mat.pop("extensions")
        mat["emissiveFactor"] = factor


def _use_ext(gltf: dict, name: str) -> None:
    used = gltf.setdefault("extensionsUsed", [])
    if name not in used:
        used.append(name)


# --- scenery merge ----------------------------------------------------------------------------------------------

_KEYS = ("buffers", "bufferViews", "accessors", "images", "samplers", "textures", "materials", "meshes", "nodes")


def merge_scenery(base: dict, add: dict, root_name: str = SCENERY_ROOT) -> int:
    """Append `add`'s default scene (as children of one new `root_name` root node) to `base`, with every index
    remapped. Animations, skins and cameras of the scenery are dropped (scenery is static and the viewer owns the
    camera). Returns the new root node's index."""
    off = {k: len(base.get(k, [])) for k in _KEYS}
    light_off = len(base.get("extensions", {}).get(LIGHTS_EXT, {}).get("lights", []))
    a = copy.deepcopy(add)

    for v in a.get("bufferViews", []):
        v["buffer"] += off["buffers"]
    for acc in a.get("accessors", []):
        if "bufferView" in acc:
            acc["bufferView"] += off["bufferViews"]
        sparse = acc.get("sparse")
        if sparse:
            sparse["indices"]["bufferView"] += off["bufferViews"]
            sparse["values"]["bufferView"] += off["bufferViews"]
    for img in a.get("images", []):
        if "bufferView" in img:
            img["bufferView"] += off["bufferViews"]
    for tex in a.get("textures", []):
        if "source" in tex:
            tex["source"] += off["images"]
        if "sampler" in tex:
            tex["sampler"] += off["samplers"]
        for ext in tex.get("extensions", {}).values():
            if isinstance(ext, dict) and "source" in ext:
                ext["source"] += off["images"]

    def fix_textures(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.endswith("Texture") and isinstance(v, dict) and "index" in v:
                    v["index"] += off["textures"]
                fix_textures(v)
        elif isinstance(obj, list):
            for v in obj:
                fix_textures(v)

    for mat in a.get("materials", []):
        fix_textures(mat)
    for mesh in a.get("meshes", []):
        for p in mesh.get("primitives", []):
            if "material" in p:
                p["material"] += off["materials"]
            p["attributes"] = {k: v + off["accessors"] for k, v in p.get("attributes", {}).items()}
            if "indices" in p:
                p["indices"] += off["accessors"]
            p["targets"] = [{k: v + off["accessors"] for k, v in t.items()} for t in p.get("targets", [])]
            if not p["targets"]:
                p.pop("targets")
    for n in a.get("nodes", []):
        if "mesh" in n:
            n["mesh"] += off["meshes"]
        n["children"] = [c + off["nodes"] for c in n.get("children", [])]
        if not n["children"]:
            n.pop("children")
        n.pop("skin", None)
        n.pop("camera", None)
        light = n.get("extensions", {}).get(LIGHTS_EXT)
        if light is not None and "light" in light:
            light["light"] += light_off

    for k in _KEYS:
        if a.get(k):
            base.setdefault(k, []).extend(a[k])
    add_lights = a.get("extensions", {}).get(LIGHTS_EXT, {}).get("lights", [])
    if add_lights:
        base.setdefault("extensions", {}).setdefault(LIGHTS_EXT, {}).setdefault("lights", []).extend(add_lights)
    for ext in a.get("extensionsUsed", []):
        _use_ext(base, ext)
    for ext in a.get("extensionsRequired", []):
        req = base.setdefault("extensionsRequired", [])
        if ext not in req:
            req.append(ext)

    scene = a.get("scenes", [{}])[a.get("scene", 0)] if a.get("scenes") else {}
    children = [i + off["nodes"] for i in scene.get("nodes", [])]
    nodes = base.setdefault("nodes", [])
    nodes.append({"name": root_name, "children": children} if children else {"name": root_name})
    root = len(nodes) - 1
    base.setdefault("scenes", [{"nodes": []}])[base.get("scene", 0)].setdefault("nodes", []).append(root)
    return root


def mark_dressed(gltf: dict, info: dict) -> None:
    scene = gltf.setdefault("scenes", [{"nodes": []}])[gltf.get("scene", 0)]
    scene.setdefault("extras", {})["omnific_scene"] = {"dressed": True, **info}
    gltf.setdefault("asset", {})["generator"] = "omnific-mesh + omnific-dress (Blender)"


def has_emissive_strength(gltf: dict) -> bool:
    return any("KHR_materials_emissive_strength" in m.get("extensions", {}) for m in gltf.get("materials", []))


def finalize(gltf: dict) -> None:
    if has_emissive_strength(gltf):
        _use_ext(gltf, "KHR_materials_emissive_strength")
