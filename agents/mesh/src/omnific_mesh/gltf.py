"""Minimal glTF 2.0 writer: one embedded base64 buffer, a mesh per distinct asset, a node per entity."""

import base64
import json

import numpy as np

from .assets import GENERATORS

ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
FLOAT, UNSIGNED_INT = 5126, 5125


def build_gltf(entities: list[tuple[str, str, list[str]]]) -> tuple[dict, dict[str, str]]:
    """entities: (name, asset_id, bound_state). Returns (gltf dict, entity name -> asset id)."""
    blob = bytearray()
    views, accessors, meshes, nodes = [], [], [], []
    mesh_index: dict[str, int] = {}

    def add(data: np.ndarray, target: int, comp: int, typ: str, minmax: bool) -> int:
        raw = data.tobytes()
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(raw), "target": target})
        blob.extend(raw)  # float32/uint32 data keeps 4-byte alignment
        acc = {"bufferView": len(views) - 1, "componentType": comp, "count": int(data.shape[0]),
               "type": typ}
        if minmax:
            acc["min"] = data.min(axis=0).astype(float).tolist()
            acc["max"] = data.max(axis=0).astype(float).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    for asset in dict.fromkeys(a for _, a, _ in entities):
        pos, nrm, idx = GENERATORS[asset]()
        p = add(pos, ARRAY_BUFFER, FLOAT, "VEC3", True)
        n = add(nrm, ARRAY_BUFFER, FLOAT, "VEC3", False)
        i = add(idx, ELEMENT_ARRAY_BUFFER, UNSIGNED_INT, "SCALAR", False)
        mesh_index[asset] = len(meshes)
        meshes.append({"name": asset, "primitives": [{
            "attributes": {"POSITION": p, "NORMAL": n}, "indices": i, "material": 0, "mode": 4}]})

    for k, (name, asset, bound) in enumerate(entities):
        nodes.append({"name": name, "mesh": mesh_index[asset], "translation": [2.0 * k, 0.0, 0.0],
                      "extras": {"asset": asset, "bound_state": bound}})

    gltf = {
        "asset": {"version": "2.0", "generator": "omnific-mesh"},
        "scene": 0,
        "scenes": [{"name": "scene", "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": [{"name": "default", "pbrMetallicRoughness": {
            "baseColorFactor": [0.7, 0.7, 0.75, 1.0], "metallicFactor": 0.0, "roughnessFactor": 0.8}}],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob),
                     "uri": "data:application/octet-stream;base64,"
                            + base64.b64encode(bytes(blob)).decode("ascii")}],
    }
    return gltf, {name: asset for name, asset, _ in entities}


def dumps(gltf: dict) -> bytes:
    return json.dumps(gltf).encode("utf-8")
