import base64
import json

import numpy as np
import pytest
from omnific_common.contracts import SceneInput
from omnific_common.storage import LocalStore
from omnific_mesh import activities
from omnific_mesh.assets import GENERATORS, asset_for_kind
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, TimeSpan


def spec_with(entities):
    return SimSpec(title="t", prompt="p", domains=[Domain.OTHER], fidelity=Fidelity.HEURISTIC,
                   assumptions=[], parameters=[], state=[], equations=[], entities=entities,
                   time=TimeSpan(end=1.0))


@pytest.mark.parametrize("kind,asset", [
    ("sphere", "sphere"), ("Ball", "sphere"), ("pizza", "disk"), ("planet_surface", "planet"),
    ("ground plane", "plane"), ("steel rod", "cylinder"), ("crate", "box"), ("weird thing", "box"),
])
def test_keyword_table(kind, asset):
    assert asset_for_kind(kind) == asset


@pytest.mark.parametrize("name", list(GENERATORS))
def test_generators_consistent(name):
    pos, nrm, idx = GENERATORS[name]()
    assert pos.shape == nrm.shape and pos.shape[1] == 3
    assert len(idx) % 3 == 0 and idx.max() < len(pos)
    assert np.all(np.isfinite(pos))
    np.testing.assert_allclose(np.linalg.norm(nrm, axis=1), 1.0, atol=1e-5)


def check_gltf(g):
    assert g["asset"]["version"] == "2.0"
    buf = g["buffers"][0]
    data = base64.b64decode(buf["uri"].split(",", 1)[1])
    assert len(data) == buf["byteLength"]
    for v in g["bufferViews"]:
        assert v["byteOffset"] + v["byteLength"] <= len(data)
        assert v["byteOffset"] % 4 == 0
    ncomp = {"VEC3": 3, "SCALAR": 1}
    for a in g["accessors"]:
        v = g["bufferViews"][a["bufferView"]]
        assert a["count"] * ncomp[a["type"]] * 4 == v["byteLength"]
    for m in g["meshes"]:
        for p in m["primitives"]:
            pos = g["accessors"][p["attributes"]["POSITION"]]
            nrm = g["accessors"][p["attributes"]["NORMAL"]]
            idx = g["accessors"][p["indices"]]
            assert pos["count"] == nrm["count"]
            assert "min" in pos and "max" in pos
            v = g["bufferViews"][idx["bufferView"]]
            ind = np.frombuffer(data, np.uint32, idx["count"], v["byteOffset"])
            assert ind.max() < pos["count"]
    for n in g["nodes"]:
        assert 0 <= n["mesh"] < len(g["meshes"])
    assert all(0 <= i < len(g["nodes"]) for i in g["scenes"][g["scene"]]["nodes"])


async def test_build_scene(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    monkeypatch.setattr(activities, "get_store", lambda: store)
    ents = [Entity(name="ball", kind="sphere", bound_state=["x"]),
            Entity(name="ball2", kind="sphere"),
            Entity(name="pie", kind="pizza"),
            Entity(name="earth_ground", kind="planet_surface"),
            Entity(name="mystery", kind="flux capacitor")]
    out = await activities.build_scene(SceneInput(job_id="j", spec=spec_with(ents)))
    assert out.scene_key == "jobs/j/scene.gltf"
    assert out.entity_assets == {"ball": "sphere", "ball2": "sphere", "pie": "disk",
                                 "earth_ground": "planet", "mystery": "box"}
    g = json.loads(store.get(out.scene_key))
    check_gltf(g)
    assert len(g["nodes"]) == 5
    assert len(g["meshes"]) == 4  # sphere shared
    assert g["nodes"][0]["extras"]["bound_state"] == ["x"]


async def test_empty_scene(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    monkeypatch.setattr(activities, "get_store", lambda: store)
    out = await activities.build_scene(SceneInput(job_id="j", spec=spec_with([])))
    g = json.loads(store.get(out.scene_key))
    assert g["nodes"] == [] and g["buffers"][0]["byteLength"] == 0


@pytest.mark.parametrize("kind", ["bob", "pendulum", "mass", "Pendulum Bob"])
def test_pendulum_kinds_are_spheres(kind):
    assert asset_for_kind(kind) == "sphere"


async def test_articulated_initial_translation(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    monkeypatch.setattr(activities, "get_store", lambda: store)
    ents = [Entity(name="support", kind="box", pivot=[0, 2.0, 0]),
            Entity(name="bob1", kind="bob", bound_state=["a"], pivot=[0, 2.0, 0], arm=[0, -1.0, 0]),
            Entity(name="bob2", kind="bob", parent="bob1", pivot=[0, 0, 0], arm=[1.0, 0, 0]),
            Entity(name="free", kind="sphere")]
    out = await activities.build_scene(SceneInput(job_id="j", spec=spec_with(ents)))
    g = json.loads(store.get(out.scene_key))
    check_gltf(g)
    tr = {n["name"]: n["translation"] for n in g["nodes"]}
    k = 5.0  # 10 / max component (2.0)
    assert tr["support"] == [0, 2.0 * k, 0]
    assert tr["bob1"] == pytest.approx([0, 1.0 * k, 0])
    assert tr["bob2"] == pytest.approx([1.0 * k, 1.0 * k, 0])
    assert tr["free"] == [6.0, 0.0, 0.0]  # default layout unchanged
