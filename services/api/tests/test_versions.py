"""Follow-up versions: POST /jobs/{id}/edits creates a version (a job of its own), GET /jobs/{id}/versions lists them."""

import json

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from omnific_api.app import (
    create_app,
    outcome_key,
    version_job_id,
    version_link_key,
    versions_index_key,
)
from omnific_common.contracts import JobOutcome, ResearchOutput
from omnific_common.storage import LocalStore
from omnific_sim.spec import Domain, Fidelity, Parameter, SimSpec, TimeSpan
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError
from test_api import FakeClient, FakeHandle

ROOT = "a" * 32
SPEC = SimSpec(title="Drop", prompt="drop a ball", domains=[Domain.MECHANICS], fidelity=Fidelity.ANALYTIC,
               assumptions=[], parameters=[Parameter(name="g", value=9.81, unit="m/s**2")], state=[], equations=[],
               time=TimeSpan(end=2.0))


def finished(job_id: str, status="approved") -> JobOutcome:
    return JobOutcome(job_id=job_id, status=status, revisions=1, spec=SPEC,
                      research=ResearchOutput(summary="r", phenomena=[], facts=[]),
                      code_key=f"jobs/{job_id}/solver.py", result_key=f"jobs/{job_id}/result.npz",
                      playback_key=f"jobs/{job_id}/playback.json", mesh_backend="generated")


class ByIdClient(FakeClient):
    """One handle per workflow id; ids without one are unknown to Temporal."""

    def __init__(self):
        super().__init__()
        self.handles: dict[str, FakeHandle] = {}

    async def start_workflow(self, name, req, **kw):
        if kw["id"] in self.handles:
            raise WorkflowAlreadyStartedError(kw["id"], name)
        await super().start_workflow(name, req, **kw)
        self.handles[kw["id"]] = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="planning_edit")

    def get_workflow_handle(self, wf_id):
        return self.handles.get(wf_id) or FakeHandle(WorkflowExecutionStatus.RUNNING, missing=True)


@pytest.fixture
def setup(tmp_path):
    client, store = ByIdClient(), LocalStore(tmp_path)

    def finish(job_id, status="approved"):
        store.put(f"jobs/{job_id}/solver.py", b"def simulate(spec, refine=1): ...")
        store.put(outcome_key(job_id), finished(job_id, status).model_dump_json().encode())

    finish(ROOT)

    async def factory():
        return client

    with TestClient(create_app(factory, store)) as tc:
        yield tc, client, store, finish


def test_an_accepted_simulation_gets_a_new_version(setup):
    tc, client, store, _ = setup
    r = tc.post(f"/jobs/{ROOT}/edits", json={"request": "  make the ball red  "})
    assert r.status_code == 201
    vid = r.json()["job_id"]
    name, req, kw = client.started[-1]
    assert name == "SimulationJob" and kw["id"] == vid == req.job_id
    assert req.mesh_backend == "generated" and req.prompt == "drop a ball"
    ed = req.edit
    assert (ed.root_job_id, ed.base_job_id, ed.version, ed.request) == (ROOT, ROOT, 2, "make the ball red")
    assert ed.code == "def simulate(spec, refine=1): ..." and ed.base.spec.title == "Drop"
    assert json.loads(store.get(versions_index_key(ROOT))) == [ROOT, vid]
    assert json.loads(store.get(version_link_key(vid)))["version"] == 2


def test_only_accepted_simulations_can_be_changed(setup):
    tc, client, _, _ = setup
    running = "b" * 32
    client.handles[running] = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="awaiting_review",
                                         draft=finished(running, "awaiting_review"))
    assert tc.post(f"/jobs/{running}/edits", json={"request": "x"}).status_code == 409
    assert tc.post(f"/jobs/{ROOT}/edits", json={"request": "   "}).status_code == 422
    assert tc.post(f"/jobs/{'c' * 32}/edits", json={"request": "x"}).status_code == 404


def test_one_change_at_a_time_then_versions_chain(setup):
    tc, client, store, finish = setup
    v2 = tc.post(f"/jobs/{ROOT}/edits", json={"request": "red"}).json()["job_id"]
    assert tc.post(f"/jobs/{ROOT}/edits", json={"request": "blue"}).status_code == 409  # v2 still building
    finish(v2)
    v3 = tc.post(f"/jobs/{v2}/edits", json={"request": "blue"})
    assert v3.status_code == 201
    ed = client.started[-1][1].edit
    assert (ed.root_job_id, ed.base_job_id, ed.base_version, ed.version) == (ROOT, v2, 2, 3)
    assert json.loads(store.get(versions_index_key(ROOT))) == [ROOT, v2, v3.json()["job_id"]]


def test_versions_list_marks_the_live_one(setup):
    tc, client, _, finish = setup
    assert [(v["version"], v["live"]) for v in tc.get(f"/jobs/{ROOT}/versions").json()] == [(1, True)]
    v2 = tc.post(f"/jobs/{ROOT}/edits", json={"request": "make it red"}).json()["job_id"]
    client.handles[v2].stage = "modeling"
    for any_id in (ROOT, v2):  # the same list from any version
        vs = tc.get(f"/jobs/{any_id}/versions").json()
        assert [(v["version"], v["stage"], v["live"]) for v in vs] == [(1, "approved", True), (2, "modeling", False)]
        assert vs[1]["request"] == "make it red"
    finish(v2)
    vs = tc.get(f"/jobs/{v2}/versions").json()
    assert [v["live"] for v in vs] == [False, True]


def test_versions_of_an_unknown_job(setup):
    tc, _, _, _ = setup
    assert tc.get(f"/jobs/{'d' * 32}/versions").status_code == 404
    assert tc.get("/jobs/not-an-id/versions").status_code == 404


def test_a_version_number_is_reserved_by_its_workflow_id(setup):
    tc, client, store, _ = setup
    v2 = version_job_id(ROOT, 2)
    # another API replica already started version 2 but its index write never landed here
    client.handles[v2] = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="modeling")
    r = tc.post(f"/jobs/{ROOT}/edits", json={"request": "red"})
    assert r.status_code == 409
    assert json.loads(store.get(versions_index_key(ROOT))) == [ROOT, v2]  # listed now, and not started twice
    assert client.started == []
    assert version_job_id(ROOT, 2) == v2 != version_job_id(ROOT, 3)
