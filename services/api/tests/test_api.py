import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from omnific_api.app import create_app
from omnific_common.contracts import JobOutcome, JobStatus, ReviewDecision
from omnific_common.storage import LocalStore
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode


class FakeHandle:
    def __init__(self, status, outcome=None, missing=False, stage="running", draft=None):
        self.status, self.outcome, self.missing = status, outcome, missing
        self.stage, self.draft = stage, draft
        self.signals = []

    async def describe(self):
        if self.missing:
            raise RPCError("nf", RPCStatusCode.NOT_FOUND, b"")
        return type("D", (), {"status": self.status})()

    async def query(self, name, result_type=None):
        if name == "draft":
            return self.draft
        assert name == "status"
        return JobStatus(job_id="x", stage=self.stage)

    async def signal(self, name, arg):
        self.signals.append((name, arg))

    async def result(self):
        return self.outcome


class FakeClient:
    def __init__(self):
        self.started = []
        self.handle = FakeHandle(WorkflowExecutionStatus.RUNNING)

    async def start_workflow(self, name, req, **kw):
        self.started.append((name, req, kw))

    def get_workflow_handle(self, wf_id):
        return self.handle


@pytest.fixture
def setup(tmp_path):
    client = FakeClient()
    store = LocalStore(tmp_path)

    async def factory():
        return client

    with TestClient(create_app(factory, store)) as tc:
        yield tc, client, store


def test_healthz(setup):
    tc, _, _ = setup
    assert tc.get("/healthz").json() == {"status": "ok"}


def test_create_job(setup):
    tc, client, _ = setup
    r = tc.post("/jobs", json={"prompt": "drop a ball", "max_revisions": 2})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    name, req, kw = client.started[0]
    assert name == "SimulationJob" and kw["id"] == job_id and kw["task_queue"] == "workflow"
    assert req.job_id == job_id and req.max_revisions == 2
    assert req.max_review_rounds == 3  # default kept
    tc.post("/jobs", json={"prompt": "x", "max_review_rounds": 5, "review_timeout_hours": 1.5})
    req = client.started[1][1]
    assert req.max_review_rounds == 5 and req.review_timeout_hours == 1.5
    assert tc.post("/jobs", json={"prompt": "x", "max_review_rounds": 0}).status_code == 422
    assert tc.post("/jobs", json={"prompt": ""}).status_code == 422


def test_get_job_running_and_unknown(setup):
    tc, client, _ = setup
    r = tc.get("/jobs/" + "a" * 32)
    assert r.json()["state"] == "running" and r.json()["status"]["stage"] == "running"
    assert r.json()["result"] is None
    client.handle = FakeHandle(None, missing=True)
    assert tc.get("/jobs/" + "a" * 32).status_code == 404
    assert tc.get("/jobs/not-an-id").status_code == 404


def test_artifacts(setup):
    tc, client, store = setup
    jid = "b" * 32
    store.put(f"jobs/{jid}/playback.json", b'{"frames": []}')
    outcome = JobOutcome(job_id=jid, status="approved", revisions=1,
                         playback_key=f"jobs/{jid}/playback.json")
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, outcome)
    r = tc.get(f"/jobs/{jid}")
    assert r.json()["result"]["playback_key"] == f"jobs/{jid}/playback.json"
    r = tc.get(f"/jobs/{jid}/artifacts/playback.json")
    assert r.status_code == 200 and r.content == b'{"frames": []}'
    assert tc.get(f"/jobs/{jid}/artifacts/scene.gltf").status_code == 404  # no key
    assert tc.get(f"/jobs/{jid}/artifacts/..%2F..%2Fsecret").status_code == 404
    assert tc.get(f"/jobs/{jid}/artifacts/other.txt").status_code == 404


def _draft(jid):
    return JobOutcome(job_id=jid, status="awaiting_review", revisions=1,
                      playback_key=f"jobs/{jid}/playback.json")


def test_running_job_exposes_draft_and_artifacts(setup):
    tc, client, store = setup
    jid = "c" * 32
    store.put(f"jobs/{jid}/playback.json", b"{}")
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="awaiting_review",
                               draft=_draft(jid))
    body = tc.get(f"/jobs/{jid}").json()
    assert body["status"]["stage"] == "awaiting_review"
    assert body["draft"]["playback_key"] == f"jobs/{jid}/playback.json"
    assert body["result"] is None
    assert tc.get(f"/jobs/{jid}/artifacts/playback.json").content == b"{}"
    assert tc.get(f"/jobs/{jid}/artifacts/scene.gltf").status_code == 404
    assert tc.get(f"/jobs/{jid}/artifacts/result.npz").status_code == 404
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING)  # no draft yet
    assert tc.get(f"/jobs/{jid}/artifacts/playback.json").status_code == 404


def test_failed_workflow_has_no_artifacts(setup):
    tc, client, _ = setup
    client.handle = FakeHandle(WorkflowExecutionStatus.FAILED)
    assert tc.get("/jobs/" + "d" * 32).json()["state"] == "failed"
    assert tc.get("/jobs/" + "d" * 32 + "/artifacts/playback.json").status_code == 404


def test_review_signals_when_awaiting(setup):
    tc, client, _ = setup
    jid = "e" * 32
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="awaiting_review")
    r = tc.post(f"/jobs/{jid}/review",
                json={"approved": False, "reviewer": "bob", "notes": "bad", "blame": "solver_coder"})
    assert r.status_code == 202
    name, arg = client.handle.signals[0]
    assert name == "review" and isinstance(arg, ReviewDecision)
    assert arg.reviewer == "bob" and arg.blame == "solver_coder" and not arg.approved


def test_review_rejected_when_not_awaiting_or_invalid(setup):
    tc, client, _ = setup
    jid = "f" * 32
    body = {"approved": True, "reviewer": "bob"}
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="running")
    assert tc.post(f"/jobs/{jid}/review", json=body).status_code == 409
    assert client.handle.signals == []
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, stage="approved")
    assert tc.post(f"/jobs/{jid}/review", json=body).status_code == 409
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="awaiting_review")
    assert tc.post(f"/jobs/{jid}/review", json={"approved": True}).status_code == 422
    assert tc.post(f"/jobs/{jid}/review", json={**body, "blame": "nobody"}).status_code == 422
    assert tc.post("/jobs/nope/review", json=body).status_code == 404
