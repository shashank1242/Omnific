"""In-memory asset shared by every mesh v2 stage, with GLB I/O.

Deliberately small: one triangle mesh, an optional base-colour texture or per-vertex colours, an optional skeleton
(bones with rest-pose heads; rest rotations are identity) with linear-blend skin weights, and rotation-only clips.
That covers what TRELLIS, the riggers and the animators produce once normalised, and keeps every check plain numpy.
"""

from __future__ import annotations

import io
import json
import struct
from dataclasses import dataclass, field

import numpy as np

FLOAT, UNSIGNED_SHORT, UNSIGNED_INT, UNSIGNED_BYTE = 5126, 5123, 5125, 5121
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_DTYPE = {FLOAT: np.float32, UNSIGNED_SHORT: np.uint16, UNSIGNED_INT: np.uint32, UNSIGNED_BYTE: np.uint8}


@dataclass
class Bone:
    name: str
    parent: int  # -1 for a root
    head: np.ndarray  # (3,) rest position in mesh space


@dataclass
class Clip:
    name: str
    times: np.ndarray  # (k,) seconds, increasing
    rotations: dict[str, np.ndarray]  # bone name -> (k, 4) quaternions xyzw, local to the rest pose
    root_translation: np.ndarray | None = None  # (k, 3); must be zero for us (the solver owns position)

    @property
    def duration(self) -> float:
        return float(self.times[-1] - self.times[0]) if len(self.times) else 0.0


@dataclass
class Asset:
    positions: np.ndarray  # (n, 3) float32
    indices: np.ndarray  # (m, 3) uint32
    normals: np.ndarray | None = None
    uvs: np.ndarray | None = None  # (n, 2)
    colors: np.ndarray | None = None  # (n, 3) float32 in [0, 1], linear
    texture: bytes | None = None  # PNG/JPEG base-colour image
    texture_mime: str = "image/png"
    base_color: tuple[float, float, float, float] = (0.8, 0.8, 0.8, 1.0)
    bones: list[Bone] = field(default_factory=list)
    joints: np.ndarray | None = None  # (n, 4) uint16 bone indices
    weights: np.ndarray | None = None  # (n, 4) float32
    clips: list[Clip] = field(default_factory=list)

    @property
    def n_tris(self) -> int:
        return len(self.indices)

    def bbox(self) -> tuple[np.ndarray, np.ndarray]:
        return self.positions.min(axis=0), self.positions.max(axis=0)

    def bone_index(self) -> dict[str, int]:
        return {b.name: i for i, b in enumerate(self.bones)}

    def compute_normals(self) -> np.ndarray:
        p, f = self.positions.astype(np.float64), self.indices.astype(np.int64)
        fn = np.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]])
        vn = np.zeros_like(p)
        for k in range(3):
            np.add.at(vn, f[:, k], fn)
        n = np.linalg.norm(vn, axis=1, keepdims=True)
        self.normals = (vn / np.where(n > 0, n, 1)).astype(np.float32)
        return self.normals


# --- GLB writing ------------------------------------------------------------------------------

class _Writer:
    def __init__(self):
        self.blob = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def _pad(self):
        while len(self.blob) % 4:
            self.blob.append(0)

    def view(self, raw: bytes, target: int | None = None) -> int:
        self._pad()
        v = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(raw)}
        if target is not None:
            v["target"] = target
        self.views.append(v)
        self.blob.extend(raw)
        return len(self.views) - 1

    def accessor(self, data: np.ndarray, comp: int, typ: str, target: int | None = None,
                 minmax: bool = False) -> int:
        arr = np.ascontiguousarray(data, dtype=_DTYPE[comp])
        acc = {"bufferView": self.view(arr.tobytes(), target), "componentType": comp,
               "count": int(arr.shape[0]), "type": typ}
        if minmax:
            flat = arr.reshape(arr.shape[0], -1)
            acc["min"] = flat.min(axis=0).astype(float).tolist()
            acc["max"] = flat.max(axis=0).astype(float).tolist()
        self.accessors.append(acc)
        return len(self.accessors) - 1


def add_asset(w: _Writer, gltf: dict, a: Asset, name: str, parent_node: int | None = None,
              anim_prefix: str = "") -> int:
    """Append `a` to a glTF dict being built with writer `w`. Returns the mesh node index.

    The mesh node is a child of `parent_node` if given; bones become child nodes of the mesh node so the whole
    skeleton moves with the entity. Clips become glTF animations named `<anim_prefix><clip>`.
    """
    for key in ("nodes", "meshes", "materials", "textures", "images", "samplers", "skins", "animations"):
        gltf.setdefault(key, [])
    attrs = {"POSITION": w.accessor(a.positions, FLOAT, "VEC3", ARRAY_BUFFER, minmax=True)}
    normals = a.normals if a.normals is not None else a.compute_normals()
    attrs["NORMAL"] = w.accessor(normals, FLOAT, "VEC3", ARRAY_BUFFER)
    material: dict = {"name": f"{name}-material", "pbrMetallicRoughness": {
        "baseColorFactor": list(a.base_color), "metallicFactor": 0.0, "roughnessFactor": 0.8}}
    if a.texture is not None and a.uvs is not None:
        attrs["TEXCOORD_0"] = w.accessor(a.uvs, FLOAT, "VEC2", ARRAY_BUFFER)
        if not gltf["samplers"]:
            gltf["samplers"].append({"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497})
        gltf["images"].append({"bufferView": w.view(a.texture), "mimeType": a.texture_mime})
        gltf["textures"].append({"sampler": 0, "source": len(gltf["images"]) - 1})
        material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": len(gltf["textures"]) - 1}
    elif a.colors is not None:
        attrs["COLOR_0"] = w.accessor(a.colors, FLOAT, "VEC3", ARRAY_BUFFER)
    if a.bones:
        attrs["JOINTS_0"] = w.accessor(a.joints, UNSIGNED_SHORT, "VEC4", ARRAY_BUFFER)
        attrs["WEIGHTS_0"] = w.accessor(a.weights, FLOAT, "VEC4", ARRAY_BUFFER)
    gltf["materials"].append(material)
    idx = w.accessor(a.indices.reshape(-1), UNSIGNED_INT, "SCALAR", ELEMENT_ARRAY_BUFFER)
    gltf["meshes"].append({"name": name, "primitives": [{
        "attributes": attrs, "indices": idx, "material": len(gltf["materials"]) - 1, "mode": 4}]})
    mesh_node = len(gltf["nodes"])
    gltf["nodes"].append({"name": f"mesh:{name}", "mesh": len(gltf["meshes"]) - 1})
    if parent_node is not None:
        gltf["nodes"][parent_node].setdefault("children", []).append(mesh_node)

    if a.bones:
        base = len(gltf["nodes"])
        for i, b in enumerate(a.bones):  # local translation = head - parent head
            ph = a.bones[b.parent].head if b.parent >= 0 else np.zeros(3)
            gltf["nodes"].append({"name": f"bone:{name}:{b.name}",
                                  "translation": (np.asarray(b.head) - ph).astype(float).tolist()})
        for i, b in enumerate(a.bones):
            holder = base + b.parent if b.parent >= 0 else mesh_node
            gltf["nodes"][holder].setdefault("children", []).append(base + i)
        ibm = np.stack([_translation_matrix(-np.asarray(b.head)).T.reshape(-1) for b in a.bones])
        gltf["skins"].append({"name": name, "joints": list(range(base, base + len(a.bones))),
                              "inverseBindMatrices": w.accessor(ibm, FLOAT, "MAT4"),
                              "skeleton": base + next(i for i, b in enumerate(a.bones) if b.parent < 0)})
        gltf["nodes"][mesh_node]["skin"] = len(gltf["skins"]) - 1
        bi = a.bone_index()
        for clip in a.clips:
            samplers, channels = [], []
            t_acc = w.accessor(clip.times.astype(np.float32), FLOAT, "SCALAR", minmax=True)
            for bone, q in clip.rotations.items():
                if bone not in bi:
                    continue
                samplers.append({"input": t_acc, "output": w.accessor(q, FLOAT, "VEC4"),
                                 "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1,
                                 "target": {"node": base + bi[bone], "path": "rotation"}})
            if channels:
                gltf["animations"].append({"name": f"{anim_prefix}{clip.name}", "samplers": samplers,
                                           "channels": channels})
    return mesh_node


def _translation_matrix(t: np.ndarray) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = t
    return m


def to_glb(a: Asset, name: str = "asset") -> bytes:
    w = _Writer()
    gltf: dict = {"asset": {"version": "2.0", "generator": "omnific-mesh"}, "scene": 0, "nodes": []}
    root = add_asset(w, gltf, a, name)
    gltf["scenes"] = [{"nodes": [root]}]
    gltf["extras"] = {"omnific": {"clips": [c.name for c in a.clips]}}
    return pack_glb(gltf, bytes(w.blob), w)


def pack_glb(gltf: dict, blob: bytes, w: _Writer | None = None) -> bytes:
    if w is not None:
        gltf["bufferViews"], gltf["accessors"] = w.views, w.accessors
    gltf["buffers"] = [{"byteLength": len(blob)}]
    gltf = {k: v for k, v in gltf.items() if v != []}
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    blob = blob + b"\0" * (-len(blob) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(blob), b"BIN\0") + blob)


# --- GLB reading ------------------------------------------------------------------------------

def unpack_glb(data: bytes) -> tuple[dict, bytes]:
    magic, version, _ = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2:
        raise ValueError("not a glTF 2.0 binary")
    off, gltf, blob = 12, None, b""
    while off < len(data):
        length, kind = struct.unpack_from("<I4s", data, off)
        chunk = data[off + 8: off + 8 + length]
        if kind == b"JSON":
            gltf = json.loads(chunk)
        elif kind == b"BIN\0":
            blob = chunk
        off += 8 + length
    if gltf is None:
        raise ValueError("GLB has no JSON chunk")
    return gltf, blob


def _read(gltf: dict, blob: bytes, idx: int) -> np.ndarray:
    acc = gltf["accessors"][idx]
    view = gltf["bufferViews"][acc["bufferView"]]
    dt = np.dtype(_DTYPE[acc["componentType"]])
    n = _NCOMP[acc["type"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride", 0)
    if stride and stride != dt.itemsize * n:
        rows = [np.frombuffer(blob, dt, n, start + i * stride) for i in range(acc["count"])]
        arr = np.stack(rows)
    else:
        arr = np.frombuffer(blob, dt, acc["count"] * n, start).reshape(acc["count"], n)
    if acc.get("normalized") and dt.kind == "u":
        arr = arr.astype(np.float32) / np.iinfo(dt).max
    return arr.copy()


def _node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.asarray(node["matrix"], float).reshape(4, 4).T
    m = np.eye(4)
    if "rotation" in node:
        m[:3, :3] = quat_to_matrix(np.asarray(node["rotation"], float))
    if "scale" in node:
        m[:3, :3] = m[:3, :3] * np.asarray(node["scale"], float)
    if "translation" in node:
        m[:3, 3] = node["translation"]
    return m


def from_glb(data: bytes) -> Asset:
    """Flatten a GLB (e.g. TRELLIS output, a rigger's output) into one Asset in world space.

    All triangle primitives are merged; the first base-colour texture is kept (others are dropped); one skin and
    its rotation animations are kept when present.
    """
    gltf, blob = unpack_glb(data)
    nodes = gltf.get("nodes", [])
    world = [None] * len(nodes)
    parents = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}

    def wm(i):
        if world[i] is None:
            world[i] = (wm(parents[i]) if i in parents else np.eye(4)) @ _node_matrix(nodes[i])
        return world[i]

    pos, idx, nrm, uvs, cols, joints, weights = [], [], [], [], [], [], []
    texture, mime, base_color, skin_idx = None, "image/png", (0.8, 0.8, 0.8, 1.0), None
    count = 0
    for ni, node in enumerate(nodes):
        if "mesh" not in node:
            continue
        m = wm(ni) if "skin" not in node else np.eye(4)  # skinned meshes are in bind space
        for prim in gltf["meshes"][node["mesh"]]["primitives"]:
            if prim.get("mode", 4) != 4:
                continue
            a = prim["attributes"]
            p = _read(gltf, blob, a["POSITION"]).astype(np.float64)
            p = (np.c_[p, np.ones(len(p))] @ m.T)[:, :3]
            f = (_read(gltf, blob, prim["indices"]).reshape(-1) if "indices" in prim
                 else np.arange(len(p))).reshape(-1, 3).astype(np.int64)
            pos.append(p)
            idx.append(f + count)
            nrm.append(_read(gltf, blob, a["NORMAL"]) @ m[:3, :3].T if "NORMAL" in a else np.zeros_like(p))
            uvs.append(_read(gltf, blob, a["TEXCOORD_0"]) if "TEXCOORD_0" in a else np.zeros((len(p), 2)))
            cols.append(_read(gltf, blob, a["COLOR_0"])[:, :3] if "COLOR_0" in a else np.ones((len(p), 3)))
            if "JOINTS_0" in a and "WEIGHTS_0" in a:
                joints.append(_read(gltf, blob, a["JOINTS_0"]).astype(np.uint16))
                weights.append(_read(gltf, blob, a["WEIGHTS_0"]).astype(np.float32))
                skin_idx = node.get("skin", skin_idx)
            mat = gltf.get("materials", [{}])[prim["material"]] if "material" in prim else {}
            pbr = mat.get("pbrMetallicRoughness", {})
            base_color = tuple(pbr.get("baseColorFactor", base_color))
            tex = pbr.get("baseColorTexture")
            if texture is None and tex is not None:
                image = gltf["images"][gltf["textures"][tex["index"]]["source"]]
                if "bufferView" in image:
                    v = gltf["bufferViews"][image["bufferView"]]
                    texture = bytes(blob[v.get("byteOffset", 0): v.get("byteOffset", 0) + v["byteLength"]])
                    mime = image.get("mimeType", "image/png")
            count += len(p)
    if not pos:
        raise ValueError("GLB contains no triangle meshes")
    asset = Asset(positions=np.concatenate(pos).astype(np.float32),
                  indices=np.concatenate(idx).astype(np.uint32),
                  normals=np.concatenate(nrm).astype(np.float32),
                  uvs=np.concatenate(uvs).astype(np.float32) if texture is not None else None,
                  colors=np.concatenate(cols).astype(np.float32) if texture is None and any(
                      "COLOR_0" in p_["attributes"] for m_ in gltf["meshes"] for p_ in m_["primitives"]) else None,
                  texture=texture, texture_mime=mime, base_color=base_color)
    if skin_idx is not None and len(joints) == len(pos):
        _read_skin(gltf, blob, skin_idx, wm, asset, np.concatenate(joints), np.concatenate(weights))
    return asset


def _read_skin(gltf, blob, skin_idx, wm, asset: Asset, joints, weights) -> None:
    skin = gltf["skins"][skin_idx]
    jn = skin["joints"]
    names = [gltf["nodes"][j].get("name", f"bone{k}") for k, j in enumerate(jn)]
    names = [n.split(":")[-1] for n in names]
    parents = {c: i for i, n in enumerate(gltf["nodes"]) for c in n.get("children", [])}
    asset.bones = [Bone(name=names[k], parent=jn.index(parents[j]) if parents.get(j) in jn else -1,
                        head=wm(j)[:3, 3].astype(np.float32)) for k, j in enumerate(jn)]
    asset.joints, asset.weights = joints, weights
    for anim in gltf.get("animations", []):
        rot: dict[str, np.ndarray] = {}
        times = None
        root_t = None
        for ch in anim["channels"]:
            node = ch["target"].get("node")
            if node not in jn:
                continue
            s = anim["samplers"][ch["sampler"]]
            t = _read(gltf, blob, s["input"]).reshape(-1)
            out = _read(gltf, blob, s["output"])
            if times is None:
                times = t
            if len(t) != len(times) or not np.allclose(t, times):
                out = np.stack([np.interp(times, t, out[:, c]) for c in range(out.shape[1])], 1)
            if ch["target"]["path"] == "rotation":
                rot[names[jn.index(node)]] = out.astype(np.float32)
            elif ch["target"]["path"] == "translation" and asset.bones[jn.index(node)].parent < 0:
                root_t = out.astype(np.float32) - asset.bones[jn.index(node)].head
        if times is not None and rot:
            asset.clips.append(Clip(name=anim.get("name", f"clip{len(asset.clips)}"), times=times,
                                    rotations=rot, root_translation=root_t))


# --- quaternions (xyzw, glTF order) -----------------------------------------------------------

def quat_to_matrix(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q / (np.linalg.norm(q) or 1.0)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def axis_angle_quat(axis, angle: float | np.ndarray) -> np.ndarray:
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    angle = np.asarray(angle, float)
    s = np.sin(angle / 2)[..., None]
    return np.concatenate([axis * s, np.cos(angle / 2)[..., None]], axis=-1).astype(np.float32)


def quat_angle(q: np.ndarray) -> np.ndarray:
    """Rotation angle (rad) of each quaternion relative to identity."""
    w = np.clip(np.abs(np.asarray(q)[..., 3]) / np.maximum(np.linalg.norm(q, axis=-1), 1e-12), -1, 1)
    return 2 * np.arccos(w)


def png(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
