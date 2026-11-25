"""The Blender runner harness end to end, in real Blender (the `bpy` wheel).

Needs BLENDER_PYTHON: a Python 3.11 with `bpy` installed (e.g. `uv venv .venv-blender --python 3.11` then
`uv pip install --python .venv-blender bpy==5.0.1`). Skipped without it, like the CAD runner tests without build123d.
"""

import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from blender_fixtures import cube_glb, pendulum_and_ball, write_inputs
from omnific_sandbox import gltf_merge
from omnific_sandbox.backends import SubprocessBackend

HARNESS = Path(__file__).resolve().parents[1] / "src" / "omnific_sandbox" / "blender_harness.py"
PY = os.environ.get("BLENDER_PYTHON", "")


def _has_bpy() -> bool:
    if not PY or not Path(PY).exists():
        return False
    return subprocess.run([PY, "-c", "import bpy"], check=False, capture_output=True, timeout=120).returncode == 0


pytestmark = pytest.mark.skipif(not _has_bpy(), reason="BLENDER_PYTHON with bpy not configured")

GOOD = {
    "10_look": """
omnific.restyle("ball", base_color=(0.9, 0.1, 0.1), roughness=0.35)
omnific.restyle("support", metallic=0.8, roughness=0.25)
""",
    "20_scenery": """
import bpy
lo, hi = omnific.motion_bounds()
bpy.ops.mesh.primitive_cube_add(size=0.6, location=(hi[0] + 2.0, hi[1] + 2.0, 0.3))
bpy.context.active_object.name = "crate"
""",
    "30_lighting": """
import bpy
lamp = bpy.data.lights.new("key", "AREA")
lamp.energy = 400
obj = bpy.data.objects.new("key", lamp)
omnific.scenery().objects.link(obj)
obj.location = (4, -4, 6)
""",
}


def run(tmp_path, mode, scripts=None, stills=0, props=None):
    gltf, pb = pendulum_and_ball()
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    write_inputs(in_dir, gltf, pb, mode, scripts, stills, props)
    out_dir.mkdir()
    res = SubprocessBackend(HARNESS, "result.zip", PY).run(in_dir, out_dir, 600)
    assert res.ok, res.stderr
    z = zipfile.ZipFile(io.BytesIO(res.result_path.read_bytes()))
    return json.loads(z.read("report.json")), z, gltf


def test_dress_mode_end_to_end(tmp_path):
    report, z, base = run(tmp_path, "dress", GOOD, stills=2)
    assert [s["ok"] for s in report["scripts"]] == [True, True, True], report["scripts"]
    checks = report["checks"]
    for name in ("protected", "gltf_protected", "clearance", "fidelity", "framing"):
        assert checks[name]["ok"], (name, checks[name])
    assert checks["fidelity"]["max_error"] < 1e-4
    dressed = json.loads(z.read("scene.gltf"))
    assert dressed["scenes"][0]["extras"]["omnific_scene"]["dressed"] is True
    assert dressed["scenes"][0]["extras"]["omnific_scene"]["lights"] == 1
    names = {n.get("name") for n in dressed["nodes"]}
    assert {"support", "bob", "ball", "scenery", "crate", "key"} <= names
    ents = ["support", "bob", "ball"]
    assert gltf_merge.entity_snapshot(dressed, ents) == gltf_merge.entity_snapshot(base, ents)
    ball = next(n for n in dressed["nodes"] if n["name"] == "ball")
    mat = dressed["materials"][dressed["meshes"][ball["mesh"]]["primitives"][0]["material"]]
    assert mat["pbrMetallicRoughness"]["baseColorFactor"][:3] == pytest.approx([0.9, 0.1, 0.1])
    assert "scene.blend" in z.namelist()
    assert len([n for n in z.namelist() if n.startswith("stills/")]) == 2
    # the three entities share one mesh: a colour track or restyle on one must not recolour the others
    mats = report["materials"]
    assert all(m.startswith("ball:") for m in mats["ball"]) and all(m.startswith("support:") for m in mats["support"])
    assert not any(m.startswith(("ball:", "support:")) for m in mats["bob"])


def test_script_errors_and_violations_are_reported(tmp_path):
    scripts = {
        "10_blocked": "import os\nos.listdir('.')",
        "20_crash": "import bpy\nbpy.data.objects['nope'].location.x = 1",
        # transform edits are undone by the entity's own keyframes; what persists is caught by the protected set
        "30_changes_entity": "import bpy\nbpy.data.objects['ball'].scale = (3, 3, 3)\n"
                             "bpy.data.objects['ball'].modifiers.new('melt', 'SUBSURF')",
        "40_blocks_the_swing": """
import bpy
lo, hi = omnific.entity_bounds("bob")
bpy.ops.mesh.primitive_cube_add(size=1, location=((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2))
""",
    }
    report, _, _ = run(tmp_path, "dress", scripts)
    by = {s["name"]: s for s in report["scripts"]}
    assert not by["10_blocked"]["ok"] and "os" in by["10_blocked"]["error"]
    assert not by["20_crash"]["ok"] and "KeyError" in by["20_crash"]["error"] and "line 2" in by["20_crash"]["error"]
    assert by["30_changes_entity"]["ok"]  # it ran, but the protected-set check catches what it did
    assert not report["checks"]["protected"]["ok"] and "ball" in report["checks"]["protected"]["changed"]
    assert not report["checks"]["clearance"]["ok"]


def test_export_mode_builds_only_the_blend(tmp_path):
    report, z, _ = run(tmp_path, "export")
    assert "scene.gltf" not in z.namelist() and "scene.blend" in z.namelist()
    assert report["scripts"] == []
    assert report["checks"]["fidelity"]["ok"] and report["checks"]["framing"]["ok"]
    assert report["checks"]["fidelity"]["frames"] == 10 * 12 + 1


def test_props_are_placed_scaled_and_credited(tmp_path):
    scripts = {"20_scenery": """
lo, hi = omnific.motion_bounds()
crate = omnific.prop("crate_01", location=(hi[0] + 3.0, hi[1] + 3.0, 0.0), rotation=0.5)
assert crate is not None and crate.name == "prop:crate_01"
assert omnific.prop("not_listed", location=(0, 0, 0)) is None
small = omnific.prop("crate_01", location=(lo[0] - 3.0, hi[1] + 3.0, 0.0), size=0.5)
"""}
    report, z, _ = run(tmp_path, "dress", scripts, props={"crate_01": cube_glb(2.0)})
    assert report["scripts"][0]["ok"], report["scripts"]
    assert report["checks"]["clearance"]["ok"] and report["checks"]["gltf_protected"]["ok"]
    assert report["props_used"] == ["crate_01"]
    assert any("not_listed" in n for n in report["notes"])
    dressed = json.loads(z.read("scene.gltf"))
    names = [n.get("name", "") for n in dressed["nodes"]]
    assert names.count("prop:crate_01") == 1 and any(n.startswith("prop:crate_01.") for n in names)
    assert "CC0 1.0" in dressed["asset"]["copyright"] and "crate_01" in dressed["asset"]["copyright"]


def test_moved_vertices_are_caught(tmp_path):
    scripts = {"10_nudge": "import bpy\nbpy.data.objects['ball'].data.vertices[0].co.x += 0.3"}
    report, _, _ = run(tmp_path, "dress", scripts)
    assert report["scripts"][0]["ok"] and not report["checks"]["protected"]["ok"]


def test_result_archives_must_hold_what_the_mode_promises(tmp_path):
    import zipfile as zf

    from omnific_sandbox.activities import read_blender_result

    path = tmp_path / "r.zip"
    with zf.ZipFile(path, "w") as z:
        z.writestr("report.json", "{}")
        z.writestr("scene.blend", b"BLENDER")
        z.writestr("stills/0.png", b"png")
        z.writestr("../evil.txt", b"x")
    files = read_blender_result(path, "export")
    assert sorted(files) == ["report.json", "scene.blend", "stills/0.png"]
    with pytest.raises(zf.BadZipFile, match="scene.gltf"):
        read_blender_result(path, "dress")
