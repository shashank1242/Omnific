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
    assert doc["entities"] == [{"name": "ball", "kind": "sphere", "bound_state": ["x"]}]


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
    assert doc["version"] == 1 and doc["title"] == "Title" and doc["fidelity"] == "heuristic"
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
