"""Composing the dressed scene.gltf: entities untouched, scenery merged with every index remapped."""

import copy

from blender_fixtures import scene_gltf
from omnific_sandbox import gltf_merge as gm


def scenery_doc():
    """A small exported scenery file: one mesh with a textured material, a light, two root nodes."""
    doc = scene_gltf([("crate", [5.0, 0.5, 0.0])])
    doc["nodes"][0].pop("extras")
    doc["nodes"].append({"name": "lamp", "extensions": {gm.LIGHTS_EXT: {"light": 0}}, "translation": [0, 5, 0]})
    doc["scenes"][0]["nodes"] = [0, 1]
    doc["extensions"] = {gm.LIGHTS_EXT: {"lights": [{"type": "point", "intensity": 50}]}}
    doc["extensionsUsed"] = [gm.LIGHTS_EXT]
    doc["images"] = [{"bufferView": 1, "mimeType": "image/png"}]
    doc["samplers"] = [{}]
    doc["textures"] = [{"source": 0, "sampler": 0}]
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
    doc["animations"] = [{"name": "sway", "channels": [], "samplers": []}]
    return doc


def test_merge_keeps_entities_and_remaps_indices():
    base = scene_gltf([("ball", [0, 1, 0]), ("ramp", [2, 0, 0])])
    before = gm.entity_snapshot(base, ["ball", "ramp"])
    root = gm.merge_scenery(base, scenery_doc())
    assert gm.entity_snapshot(base, ["ball", "ramp"]) == before
    nodes = base["nodes"]
    assert nodes[root]["name"] == "scenery" and root in base["scenes"][0]["nodes"]
    crate, lamp = (nodes[i] for i in nodes[root]["children"])
    assert crate["name"] == "crate" and crate["mesh"] == 1
    prim = base["meshes"][1]["primitives"][0]
    assert prim["material"] == 1 and prim["attributes"]["POSITION"] == 2 and prim["indices"] == 3
    assert base["accessors"][2]["bufferView"] == 2 and base["bufferViews"][2]["buffer"] == 1
    assert base["materials"][1]["pbrMetallicRoughness"]["baseColorTexture"]["index"] == 0
    assert base["images"][0]["bufferView"] == 3
    assert lamp["extensions"][gm.LIGHTS_EXT]["light"] == 0
    assert gm.LIGHTS_EXT in base["extensionsUsed"]
    assert "animations" not in base  # scenery is static; the base had no clips


def test_merge_offsets_existing_lights():
    base = scene_gltf([("ball", [0, 1, 0])])
    base["extensions"] = {gm.LIGHTS_EXT: {"lights": [{"type": "spot"}]}}
    gm.merge_scenery(base, scenery_doc())
    lamp = next(n for n in base["nodes"] if n.get("name") == "lamp")
    assert lamp["extensions"][gm.LIGHTS_EXT]["light"] == 1
    assert len(base["extensions"][gm.LIGHTS_EXT]["lights"]) == 2


def test_empty_scenery_still_adds_root():
    base = scene_gltf([("ball", [0, 1, 0])])
    root = gm.merge_scenery(base, {"scenes": [{"nodes": []}]})
    assert base["nodes"][root] == {"name": "scenery"}


def test_material_override_never_leaks_to_other_entities():
    base = scene_gltf([("ball", [0, 1, 0]), ("ramp", [2, 0, 0])])  # both share mesh 0 and material 0
    orig = copy.deepcopy(base)
    notes = gm.apply_material_overrides(base, ["ball", "ramp"], {
        "ball": {"base_color": [1, 0, 0], "roughness": 0.3, "emissive": [1, 0.5, 0], "emissive_strength": 4},
        "ghost": {"base_color": [0, 0, 1]}})
    assert notes == ["no entity named 'ghost': override ignored"]
    ball, ramp = base["nodes"][0], base["nodes"][1]
    assert ball["mesh"] != ramp["mesh"]
    ball_mat = base["materials"][base["meshes"][ball["mesh"]]["primitives"][0]["material"]]
    ramp_mat = base["materials"][base["meshes"][ramp["mesh"]]["primitives"][0]["material"]]
    assert ball_mat["pbrMetallicRoughness"]["baseColorFactor"] == [1, 0, 0, 1.0]
    assert ball_mat["pbrMetallicRoughness"]["roughnessFactor"] == 0.3
    assert ball_mat["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"] == 4
    assert ramp_mat == orig["materials"][0]
    gm.finalize(base)
    assert "KHR_materials_emissive_strength" in base["extensionsUsed"]
    assert gm.entity_snapshot(base, ["ball", "ramp"]) == gm.entity_snapshot(orig, ["ball", "ramp"])


def test_snapshot_detects_entity_changes():
    base = scene_gltf([("ball", [0, 1, 0])])
    changed = copy.deepcopy(base)
    changed["nodes"][0]["translation"] = [0, 2, 0]
    assert gm.entity_snapshot(base, ["ball"]) != gm.entity_snapshot(changed, ["ball"])


def test_glb_round_trip():
    import json
    import struct

    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 4}]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    binary = b"\x01\x02\x03\x04"
    glb = (b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js) + 8 + len(binary))
           + struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binary), b"BIN\x00") + binary)
    out = gm.glb_to_gltf(glb)
    assert out["buffers"][0]["uri"] == gm.DATA_URI + "AQIDBA=="


def test_emission_strength_at_or_below_one_folds_into_the_factor():
    base = scene_gltf([("ball", [0, 1, 0])])
    base["materials"][0]["extensions"] = {"KHR_materials_emissive_strength": {"emissiveStrength": 5.0}}
    gm.apply_material_overrides(base, ["ball"], {"ball": {"emissive": [1, 0.5, 0], "emissive_strength": 0.5}})
    mat = base["materials"][base["meshes"][base["nodes"][0]["mesh"]]["primitives"][0]["material"]]
    assert mat["emissiveFactor"] == [0.5, 0.25, 0.0] and "extensions" not in mat  # the inherited 5x is gone
    off = scene_gltf([("ball", [0, 1, 0])])
    gm.apply_material_overrides(off, ["ball"], {"ball": {"emissive": [1, 1, 1], "emissive_strength": 0}})
    assert off["materials"][-1]["emissiveFactor"] == [0.0, 0.0, 0.0]
