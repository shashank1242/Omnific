import json

import numpy as np
import pytest
from omnific_common.contracts import PlaybackInput
from omnific_common.storage import LocalStore
from omnific_data import activities
from omnific_sim.result import SimResult
from omnific_sim.spec import (
    Domain,
    Entity,
    Fidelity,
    SimSpec,
    StateVariable,
    TimeSpan,
)
from temporalio.exceptions import ApplicationError


def make_spec():
    return SimSpec(
        title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.ANALYTIC,
        assumptions=[], parameters=[],
        state=[StateVariable(name="x", unit="m", initial_value=0),
               StateVariable(name="T", unit="K", initial_value=1)],
        equations=[], time=TimeSpan(end=10.0, unit="second", output_samples=2000),
        entities=[Entity(name="ball", kind="sphere", bound_state=["x"])],
    )


def put_result(store, n, field=False):
    t = np.linspace(0, 10, n)
    state = {"x": 2 * t}
    units = {"x": "m"}
    if field:
        state["T"] = np.outer(t, np.ones((4, 3))).reshape(n, 4, 3)
        units["T"] = "K"
    p = store.root / "tmp.npz"
    SimResult(t=t, state=state, units=units).save_npz(str(p))
    store.put("jobs/j/r.npz", p.read_bytes())


async def run(store, monkeypatch, n, field=False):
    monkeypatch.setattr(activities, "get_store", lambda: store)
    put_result(store, n, field)
    out = await activities.prepare_playback(
        PlaybackInput(job_id="j", spec=make_spec(), result_key="jobs/j/r.npz"))
    return out, json.loads(store.get(out.playback_key))


async def test_downsample_scalars(tmp_path, monkeypatch):
    out, doc = await run(LocalStore(tmp_path), monkeypatch, 2000)
    assert out.playback_key == "jobs/j/playback.json"
    assert out.n_frames == 600 == len(doc["t"])
    assert doc["unit_t"] == "second"
    assert doc["series"]["x"]["unit"] == "m"
    np.testing.assert_allclose(doc["series"]["x"]["values"], 2 * np.array(doc["t"]), atol=1e-9)
    assert doc["t"][0] == 0 and doc["t"][-1] == 10
    assert doc["entities"] == [{"name": "ball", "kind": "sphere", "bound_state": ["x"], "parent": None,
                                "pivot": None, "arm": None}]


async def test_small_result_untouched(tmp_path, monkeypatch):
    out, _ = await run(LocalStore(tmp_path), monkeypatch, 50)
    assert out.n_frames == 50


async def test_fields_keep_shape(tmp_path, monkeypatch):
    _, doc = await run(LocalStore(tmp_path), monkeypatch, 1000, field=True)
    arr = np.array(doc["series"]["T"]["values"])
    assert arr.shape == (600, 4, 3)
    np.testing.assert_allclose(arr[:, 0, 0], doc["t"], atol=1e-9)


async def test_bad_result_nonretryable(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    monkeypatch.setattr(activities, "get_store", lambda: store)
    store.put("jobs/j/r.npz", b"garbage")
    with pytest.raises(ApplicationError):
        await activities.prepare_playback(
            PlaybackInput(job_id="j", spec=make_spec(), result_key="jobs/j/r.npz"))


def spec_with(entities, state=()):
    return SimSpec(
        title="Title", prompt="p", domains=[Domain.OTHER], fidelity=Fidelity.HEURISTIC,
        assumptions=[], parameters=[], state=list(state), equations=[],
        time=TimeSpan(end=1.0, unit="second"), entities=entities,
    )


def res(state, units, n=5):
    return SimResult(t=np.linspace(0, 1, n), state=state, units=units)


def by(doc, entity, channel):
    return next(t for t in doc["tracks"] if t["entity"] == entity and t["channel"] == channel)


def test_header_fields():
    spec = spec_with([])
    doc = activities.build_playback(spec, res({"a": np.arange(5.0)}, {"a": "K"}))
    assert doc["version"] == 2 and doc["title"] == "Title" and doc["fidelity"] == "heuristic"
    assert "field_axis" not in doc["series"]["a"]
    assert doc["tracks"] == []


def test_length_tracks_share_scale_and_axes():
    spec = spec_with([Entity(name="b", kind="sphere", bound_state=["x", "y", "z", "w"]),
                      Entity(name="c", kind="sphere", bound_state=["y"])])
    r = res({"x": np.linspace(0, 20, 5), "y": np.linspace(0, 5, 5), "z": np.zeros(5),
             "w": np.ones(5), "vx": np.linspace(0, 100, 5)},
            {"x": "meter", "y": "kilometer", "z": "m", "w": "m", "vx": "m/s"})
    doc = activities.build_playback(spec, r)
    chans = [(t["entity"], t["channel"]) for t in doc["tracks"]]
    assert chans == [("b", "position_x"), ("b", "position_y"), ("b", "position_z"), ("c", "position_x")]
    assert {t["scale"] for t in doc["tracks"]} == {0.5}  # 10 / 20; velocity ignored
    z = by(doc, "b", "position_z")
    assert (z["min"], z["max"]) == (-0.5, 0.5)  # degenerate range padded


def test_angle_scale():
    spec = spec_with([Entity(name="p", kind="rod", bound_state=["th", "ph"])])
    r = res({"th": np.linspace(-1, 1, 5), "ph": np.linspace(0, 90, 5)}, {"th": "radian", "ph": "degree"})
    doc = activities.build_playback(spec, r)
    assert [t["channel"] for t in doc["tracks"]] == ["rotation_z"]  # second angle: no free channel
    assert by(doc, "p", "rotation_z")["scale"] == 1.0
    spec = spec_with([Entity(name="p", kind="rod", bound_state=["ph"])])
    doc = activities.build_playback(spec, r)
    assert by(doc, "p", "rotation_z")["scale"] == pytest.approx(np.pi / 180)
    assert by(doc, "p", "rotation_z")["max"] == 90.0


def test_field_and_scalar_tracks():
    spec = spec_with([Entity(name="s", kind="pizza", bound_state=["F", "T", "P", "Q"])])
    r = res({"F": np.ones((5, 3)) * np.arange(5)[:, None], "T": np.linspace(300, 400, 5),
             "P": np.linspace(1, 2, 5), "Q": np.linspace(0, 1, 5)},
            {"F": "kelvin", "T": "kelvin", "P": "pascal", "Q": ""})
    doc = activities.build_playback(spec, r, field_axes={"F": "x"})
    assert [t["channel"] for t in doc["tracks"]] == ["color_field", "color", "scale"]
    assert doc["series"]["F"]["field_axis"] == "x"
    assert by(doc, "s", "color")["min"] == 300 and by(doc, "s", "color")["max"] == 400
    assert [t["state"] for t in doc["tracks"]] == ["F", "T", "P"]


def test_nan_sanitised_and_roundtrip():
    spec = spec_with([Entity(name="s", kind="box", bound_state=["T"])])
    v = np.array([1.0, np.nan, 3.0, np.inf, 5.0])
    doc = activities.build_playback(spec, res({"T": v}, {"T": "kelvin"}))
    assert doc["series"]["T"]["values"] == [1.0, None, 3.0, None, 5.0]
    tr = by(doc, "s", "color")
    assert (tr["min"], tr["max"]) == (1.0, 5.0)
    raw = activities.dumps(doc)
    assert b"NaN" not in raw and b"Infinity" not in raw
    assert json.loads(raw) == doc


def test_v2_fields_and_entity_scaling():
    spec = spec_with([Entity(name="s", kind="box", pivot=[0.0, 5.0, 0.0]),
                      Entity(name="b", kind="sphere", bound_state=["th"], pivot=[0.0, 5.0, 0.0],
                             arm=[0.0, -2.0, 0.0]),
                      Entity(name="c", kind="sphere", parent="b", pivot=[0, 0, 0], arm=[0.0, -1.0, 0.0]),
                      Entity(name="plain", kind="box")])
    doc = activities.build_playback(spec, res({"th": np.linspace(-1, 1, 5)}, {"th": "radian"}))
    assert doc["version"] == 2 and doc["length_scale"] == pytest.approx(2.0)  # 10 / 5
    ents = {e["name"]: e for e in doc["entities"]}
    assert ents["b"]["pivot"] == [0.0, 10.0, 0.0] and ents["b"]["arm"] == [0.0, -4.0, 0.0]
    assert ents["b"]["parent"] is None and ents["c"]["parent"] == "b"
    assert ents["s"]["arm"] is None and ents["s"]["pivot"] == [0.0, 10.0, 0.0]
    assert all(k in ents["plain"] for k in ("parent", "pivot", "arm"))
    assert ents["plain"]["parent"] is None and ents["plain"]["pivot"] is None and ents["plain"]["arm"] is None


def _ls(entities, state=None, units=None):
    r = res(state or {"a": np.zeros(5)}, units or {"a": "kelvin"})
    return activities.build_playback(spec_with(entities), r)["length_scale"]


def test_length_scale_arms_only():
    assert _ls([Entity(name="b", kind="s", arm=[0, -0.5, 0.25])]) == pytest.approx(20.0)


def test_length_scale_series_only_and_none():
    assert _ls([], {"x": np.linspace(0, 40, 5)}, {"x": "meter"}) == pytest.approx(0.25)
    assert _ls([]) == 1.0


def test_length_scale_both_and_track_scale():
    spec = spec_with([Entity(name="b", kind="s", bound_state=["x"], arm=[0, -2, 0])])
    r = res({"x": np.linspace(0, 20, 5)}, {"x": "meter"})
    doc = activities.build_playback(spec, r)
    assert doc["length_scale"] == pytest.approx(0.5)  # series (20) dominates the arm (2)
    assert {t["scale"] for t in doc["tracks"]} == {0.5}
    spec = spec_with([Entity(name="b", kind="s", bound_state=["x"], arm=[0, -40, 0])])
    doc = activities.build_playback(spec, r)
    assert doc["length_scale"] == pytest.approx(0.25)
    assert {t["scale"] for t in doc["tracks"]} == {0.25}


def place(doc):
    """Reference viewer placement: pivot_world + Rz(angle) . arm, parents first, absolute angles."""
    ents = {e["name"]: e for e in doc["entities"]}
    tracks = {t["entity"]: t for t in doc["tracks"] if t["channel"] == "rotation_z"}
    n = len(doc["t"])
    world: dict[str, list] = {}

    def of(name):
        if name in world:
            return world[name]
        e = ents[name]
        base = of(e["parent"]) if e["parent"] else [(0.0, 0.0, 0.0)] * n
        piv = e["pivot"] or [0.0, 0.0, 0.0]
        tr = tracks.get(name)
        out = []
        for i in range(n):
            a = doc["series"][tr["state"]]["values"][i] * tr["scale"] if tr else 0.0
            x, y, z = e["arm"]
            out.append((base[i][0] + piv[0] + x * np.cos(a) - y * np.sin(a),
                        base[i][1] + piv[1] + x * np.sin(a) + y * np.cos(a), base[i][2] + piv[2] + z))
        world[name] = out
        return out

    return {name: of(name) for name, e in ents.items() if e["arm"] is not None}


def test_pendulum_demo_placement():
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / "scripts" / "make_demo.py"
    mspec = importlib.util.spec_from_file_location("make_demo", path)
    mod = importlib.util.module_from_spec(mspec)
    sys.modules["make_demo"] = mod
    mspec.loader.exec_module(mod)
    _, spec, result, *_ = mod.pendulum_demo()
    doc = activities.build_playback(spec, result)
    ls, L = doc["length_scale"], 1.0
    bob = next(e for e in doc["entities"] if e["name"] == "bob")
    px, py, _ = bob["pivot"]
    theta = np.array(doc["series"]["theta"]["values"])
    pos = np.array(place(doc)["bob"])
    assert np.allclose(np.hypot(pos[:, 0] - px, pos[:, 1] - py), L * ls)
    assert np.allclose(pos[:, 0] - px, L * np.sin(theta) * ls)
    assert np.allclose(pos[:, 1] - py, -L * np.cos(theta) * ls)
