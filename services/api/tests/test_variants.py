"""Variants: change values, re-run the job's own solver, compare."""

import json

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from omnific_api.app import apply_changes, create_app, outcome_key
from omnific_common.contracts import JobOutcome, ValueChange, VariantChanges, VariantOutcome
from omnific_common.storage import LocalStore
from omnific_sim.spec import Domain, Entity, Fidelity, Parameter, SimSpec, StateVariable, TimeSpan
from temporalio.client import WorkflowExecutionStatus
from test_api import FakeClient, FakeHandle

JID = "a" * 32
SPEC = SimSpec(title="Drop", prompt="drop a ball", domains=[Domain.MECHANICS], fidelity=Fidelity.ANALYTIC,
               assumptions=[], parameters=[Parameter(name="g", value=9.81, unit="m/s**2"),
                                           Parameter(name="m", value=1.0, unit="kg")],
               state=[StateVariable(name="y", unit="m", initial_value=10.0)], equations=[],
               time=TimeSpan(end=2.0), entities=[Entity(name="ball", kind="ball")])


@pytest.fixture
def setup(tmp_path):
    client, store = FakeClient(), LocalStore(tmp_path)
    store.put(f"jobs/{JID}/solver.py", b"def simulate(spec, refine=1): ...")
    outcome = JobOutcome(job_id=JID, status="approved", revisions=1, spec=SPEC, code_key=f"jobs/{JID}/solver.py",
                         playback_key=f"jobs/{JID}/playback.json")
    store.put(outcome_key(JID), outcome.model_dump_json().encode())  # a finished job

    async def factory():
        return client

    with TestClient(create_app(factory, store)) as tc:
        yield tc, client, store


def test_apply_changes():
    out = apply_changes(SPEC, VariantChanges(parameters=[ValueChange(name="g", value=3.71)],
                                             initial=[ValueChange(name="y", value=5.0)], end=4.0))
    assert out.parameter("g").value == 3.71 and out.parameter("m").value == 1.0
    assert out.state[0].initial_value == 5.0 and out.time.end == 4.0
    assert SPEC.parameter("g").value == 9.81  # the original is untouched
    for bad, msg in [(VariantChanges(parameters=[{"name": "nope", "value": 1}]), "unknown parameter"),
                     (VariantChanges(initial=[{"name": "v", "value": 1}]), "unknown state"),
                     (VariantChanges(parameters=[{"name": "g", "value": float("inf")}]), "finite"),
                     (VariantChanges(end=0.0), "after the start")]:
        with pytest.raises(ValueError, match=msg):
            apply_changes(SPEC, bad)


def test_create_runs_the_original_solver_with_changed_values(setup):
    tc, client, store = setup
    r = tc.post(f"/jobs/{JID}/variants", json={"parameters": [{"name": "g", "value": 1.62}], "end": 3.0})
    assert r.status_code == 201
    rec = r.json()
    assert rec["status"] == "running" and rec["changes"]["parameters"] == [{"name": "g", "value": 1.62}]
    name, inp, kw = client.started[-1]
    assert name == "SimulationVariant" and kw["id"] == f"variant-{JID}-{rec['variant_id']}"
    assert inp.spec.parameter("g").value == 1.62 and inp.spec.time.end == 3.0
    assert inp.code.code.startswith("def simulate")
    assert json.loads(store.get(f"jobs/{JID}/variants/index.json")) == [rec["variant_id"]]


def test_status_refreshes_and_playback_is_served(setup):
    tc, client, store = setup
    vid = tc.post(f"/jobs/{JID}/variants", json={"initial": [{"name": "y", "value": 2.0}]}).json()["variant_id"]
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING)
    assert tc.get(f"/jobs/{JID}/variants/{vid}").json()["status"] == "running"
    assert tc.get(f"/jobs/{JID}/variants/{vid}/playback.json").status_code == 404

    key = f"jobs/{JID}/variants/{vid}/playback.json"
    store.put(key, b'{"t": [0, 1]}')
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED,
                               VariantOutcome(variant_id=vid, ok=True, playback_key=key).model_dump(mode="json"))
    rec = tc.get(f"/jobs/{JID}/variants/{vid}").json()
    assert rec["status"] == "ready" and rec["playback_key"] == key
    r = tc.get(f"/jobs/{JID}/variants/{vid}/playback.json")
    assert r.content == b'{"t": [0, 1]}' and "immutable" in r.headers["cache-control"]
    # finished records are read from the store, not Temporal
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, missing=True)
    assert [v["status"] for v in tc.get(f"/jobs/{JID}/variants").json()] == ["ready"]


def test_failed_and_interrupted_runs(setup):
    tc, client, store = setup
    vid = tc.post(f"/jobs/{JID}/variants", json={"parameters": [{"name": "m", "value": 2.0}]}).json()["variant_id"]
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED,
                               VariantOutcome(variant_id=vid, ok=False, error="ZeroDivisionError").model_dump())
    rec = tc.get(f"/jobs/{JID}/variants/{vid}").json()
    assert rec["status"] == "failed" and "ZeroDivision" in rec["error"]
    vid2 = tc.post(f"/jobs/{JID}/variants", json={"parameters": [{"name": "m", "value": 3.0}]}).json()["variant_id"]
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, missing=True)
    # just created: its workflow may still be starting, so it stays running
    assert tc.get(f"/jobs/{JID}/variants/{vid2}").json()["status"] == "running"
    # old and still unknown to Temporal: lost
    key = f"jobs/{JID}/variants/{vid2}/variant.json"
    rec = json.loads(store.get(key))
    store.put(key, json.dumps({**rec, "created_at": "2020-01-01T00:00:00+00:00"}).encode())
    assert tc.get(f"/jobs/{JID}/variants/{vid2}").json()["status"] == "failed"


def test_bad_requests(setup):
    tc, _, _ = setup
    assert tc.post(f"/jobs/{JID}/variants", json={}).status_code == 422
    assert tc.post(f"/jobs/{JID}/variants", json={"parameters": [{"name": "nope", "value": 1}]}).status_code == 422
    assert tc.get(f"/jobs/{JID}/variants/{'z' * 12}").status_code == 404
    assert tc.get(f"/jobs/{JID}/variants/../../secret").status_code == 404
    assert tc.get(f"/jobs/{JID}/variants").json() == []


def _ready_variant(tc, client, store):
    """A finished Tune run, on a job whose outcome has a scene."""
    outcome = JobOutcome.model_validate_json(store.get(outcome_key(JID)))
    store.put(outcome_key(JID), outcome.model_copy(update={"scene_key": f"jobs/{JID}/scene.gltf"})
              .model_dump_json().encode())
    vid = tc.post(f"/jobs/{JID}/variants", json={"parameters": [{"name": "g", "value": 2.0}]}).json()["variant_id"]
    key = f"jobs/{JID}/variants/{vid}/playback.json"
    store.put(key, b'{"t": [0, 1]}')
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED,
                               VariantOutcome(variant_id=vid, ok=True, playback_key=key).model_dump(mode="json"))
    assert tc.get(f"/jobs/{JID}/variants/{vid}").json()["status"] == "ready"
    return vid, key


def test_variant_blend_export(setup):
    from omnific_common.contracts import BlenderRunOutput

    tc, client, store = setup
    vid, pb_key = _ready_variant(tc, client, store)
    assert tc.post(f"/jobs/{JID}/variants/{vid}/blend").status_code == 202
    name, inp, kw = client.started[-1]
    assert name == "BlendExport" and kw["id"] == f"variant-{JID}-{vid}-blend"
    assert inp.scene_key == f"jobs/{JID}/scene.gltf" and inp.playback_key == pb_key
    assert inp.entities == ["ball"] and inp.prefix == f"variants/{vid}/blend"

    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING)
    assert tc.get(f"/jobs/{JID}/variants/{vid}/scene.blend").status_code == 202
    blend = f"jobs/{JID}/variants/{vid}/blend/scene.blend"
    store.put(blend, b"BLENDER-v500")
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED,
                               BlenderRunOutput(ok=True, blend_key=blend).model_dump(mode="json"))
    r = tc.get(f"/jobs/{JID}/variants/{vid}/scene.blend")
    assert r.status_code == 200 and r.content == b"BLENDER-v500"
    assert r.headers["content-disposition"].startswith('attachment; filename="Drop')
    # a key outside this variant's prefix is never served
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED,
                               BlenderRunOutput(ok=True, blend_key=f"jobs/{JID}/scene.blend").model_dump(mode="json"))
    assert tc.get(f"/jobs/{JID}/variants/{vid}/scene.blend").status_code == 500
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, missing=True)
    assert tc.get(f"/jobs/{JID}/variants/{vid}/scene.blend").status_code == 404


def test_variant_blend_needs_a_finished_run(setup):
    tc, _, _ = setup
    vid = tc.post(f"/jobs/{JID}/variants", json={"parameters": [{"name": "m", "value": 2.0}]}).json()["variant_id"]
    assert tc.post(f"/jobs/{JID}/variants/{vid}/blend").status_code == 409


def test_job_blend_is_a_named_download(setup):
    tc, _, store = setup
    outcome = JobOutcome.model_validate_json(store.get(outcome_key(JID)))
    store.put(f"jobs/{JID}/dress/attempt-0/scene.blend", b"BLENDER-v500")
    store.put(outcome_key(JID), outcome.model_copy(update={"blend_key": f"jobs/{JID}/dress/attempt-0/scene.blend"})
              .model_dump_json().encode())
    r = tc.get(f"/jobs/{JID}/artifacts/scene.blend")
    assert r.status_code == 200 and r.content == b"BLENDER-v500"
    assert r.headers["content-disposition"] == 'attachment; filename="Drop.blend"'
    assert r.headers["content-type"].startswith("application/x-blender")


def test_create_job_dress_default(setup, monkeypatch):
    tc, client, _ = setup
    monkeypatch.delenv("DRESS_ENABLED", raising=False)
    assert tc.post("/jobs", json={"prompt": "a ball"}).status_code == 200
    assert client.started[-1][1].dress is True
    monkeypatch.setenv("DRESS_ENABLED", "0")
    tc.post("/jobs", json={"prompt": "a ball"})
    assert client.started[-1][1].dress is False
    tc.post("/jobs", json={"prompt": "a ball", "dress": True})
    assert client.started[-1][1].dress is True
