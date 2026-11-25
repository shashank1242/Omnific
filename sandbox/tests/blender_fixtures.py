"""Tiny scenes and playbacks for the Blender harness and glTF merge tests (no numpy, no omnific imports)."""

from __future__ import annotations

import base64
import json
import math
import struct

CUBE_POS = [(-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),
            (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5)]
CUBE_IDX = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 2, 3, 7, 2, 7, 6, 1, 2, 6, 1, 6, 5, 0, 4, 7, 0, 7, 3]


def scene_gltf(entities: list[tuple[str, list[float]]], clips: dict[str, list[dict]] | None = None) -> dict:
    """One shared cube mesh, one root node per entity at the given translation (viewer/glTF axes)."""
    pos = b"".join(struct.pack("<3f", *p) for p in CUBE_POS)
    idx = b"".join(struct.pack("<I", i) for i in CUBE_IDX)
    blob = pos + idx
    nodes = []
    for name, t in entities:
        node = {"name": name, "mesh": 0, "translation": t, "extras": {"asset": "box", "bound_state": []}}
        if clips and name in clips:
            node["extras"]["omnific"] = {"source": "procedural", "joint": [], "clips": clips[name]}
        nodes.append(node)
    return {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": 0,
        "scenes": [{"name": "scene", "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": [{"name": "box", "primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"name": "default", "pbrMetallicRoughness": {"baseColorFactor": [0.7, 0.7, 0.75, 1.0],
                                                                   "metallicFactor": 0.0, "roughnessFactor": 0.8}}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3", "min": [-0.5] * 3, "max": [0.5] * 3},
            {"bufferView": 1, "componentType": 5125, "count": len(CUBE_IDX), "type": "SCALAR"},
        ],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
                        {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx)}],
        "buffers": [{"byteLength": len(blob),
                     "uri": "data:application/octet-stream;base64," + base64.b64encode(blob).decode("ascii")}],
    }


def pendulum_and_ball(n: int = 61) -> tuple[dict, dict]:
    """A pendulum bob (v2 articulation) swinging under a fixed support, and a ball flying in an arc (v1 tracks)."""
    t = [2.0 * i / (n - 1) for i in range(n)]
    theta = [0.6 * math.cos(math.pi * x) for x in t]
    x = [1.5 * s for s in t]
    y = [3.0 * s - 1.4 * s * s for s in t]
    temp = [300 + 50 * s for s in t]
    pb = {
        "version": 2, "title": "pendulum and ball", "fidelity": "analytic", "t": t, "unit_t": "second",
        "length_scale": 1.0,
        "series": {"theta": {"unit": "radian", "values": theta}, "x": {"unit": "meter", "values": x},
                   "y": {"unit": "meter", "values": y}, "T": {"unit": "kelvin", "values": temp}},
        "entities": [{"name": "support", "kind": "box", "bound_state": []},
                     {"name": "bob", "kind": "sphere", "bound_state": ["theta"], "parent": None,
                      "pivot": [0.0, 4.0, 0.0], "arm": [0.0, -2.5, 0.0]},
                     {"name": "ball", "kind": "sphere", "bound_state": ["x", "y", "T"]}],
        "tracks": [{"entity": "bob", "channel": "rotation_z", "state": "theta", "min": -0.6, "max": 0.6, "scale": 1.0},
                   {"entity": "ball", "channel": "position_x", "state": "x", "min": 0, "max": 3, "scale": 1.0},
                   {"entity": "ball", "channel": "position_y", "state": "y", "min": 0, "max": 1.6, "scale": 1.0},
                   {"entity": "ball", "channel": "color", "state": "T", "min": 300, "max": 400, "scale": 1.0}],
        "environment": {"name": "lab", "description": "", "kind": "indoor", "ground": "flat",
                        "ground_material": "concrete", "lighting": "studio", "sky_color": "#dfe6ee",
                        "ground_color": "#b9bec6", "fog": 0},
    }
    gltf = scene_gltf([("support", [0.0, 4.2, 0.0]), ("bob", [0.0, 1.5, 0.0]), ("ball", [0.0, 0.5, 2.0])])
    return gltf, pb


def cube_glb(edge: float = 2.0) -> bytes:
    """A cube `edge` metres wide as a GLB (a stand-in for a Poly Haven prop)."""
    doc = scene_gltf([("crate", [0.0, 0.0, 0.0])])
    doc["nodes"][0].pop("extras")
    doc["nodes"][0]["scale"] = [edge, edge, edge]
    blob = base64.b64decode(doc["buffers"][0].pop("uri").split(",", 1)[1])
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(blob), b"BIN\x00") + blob)


def write_inputs(d, gltf: dict, pb: dict, mode: str, scripts: dict[str, str] | None = None, stills: int = 0,
                 props: dict[str, bytes] | None = None) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "scene.gltf").write_text(json.dumps(gltf), encoding="utf-8")
    (d / "playback.json").write_text(json.dumps(pb), encoding="utf-8")
    meta = {pid: {"name": pid.replace("_", " ").title(), "credit": f'"{pid}" by Ann (polyhaven), CC0 1.0: u'}
            for pid in (props or {})}
    (d / "job.json").write_text(json.dumps({"mode": mode, "entities": [e["name"] for e in pb["entities"]],
                                            "fps": 12, "stills": stills, "still_size": [160, 90],
                                            "still_samples": 4, "props": meta}), encoding="utf-8")
    if props:
        (d / "props").mkdir(exist_ok=True)
        for pid, data in props.items():
            (d / "props" / f"{pid}.glb").write_bytes(data)
    if scripts:
        (d / "scripts").mkdir(exist_ok=True)
        for name, code in scripts.items():
            (d / "scripts" / f"{name}.py").write_text(code, encoding="utf-8")
