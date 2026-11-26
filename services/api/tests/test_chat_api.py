"""Simulation chat: the API sends the job's own record with the question and returns the guide's reply."""

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from omnific_api.app import create_app, outcome_key
from omnific_common.contracts import ChatReply, JobOutcome, ResearchOutput
from omnific_common.storage import LocalStore
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, StateVariable, TimeSpan
from test_api import FakeClient

JID = "c" * 32
SPEC = SimSpec(title="Drop", prompt="drop a ball", domains=[Domain.MECHANICS], fidelity=Fidelity.ANALYTIC,
               assumptions=["no air"], parameters=[], state=[StateVariable(name="y", unit="m", initial_value=10.0)],
               equations=[], time=TimeSpan(end=2.0), entities=[Entity(name="ball", kind="ball")])


class ChatClient(FakeClient):
    def __init__(self, fail: Exception | None = None):
        super().__init__()
        self.executed, self.fail = [], fail

    async def execute_workflow(self, name, inp, **kw):
        self.executed.append((name, inp, kw))
        if self.fail:
            raise self.fail
        return ChatReply(answer=f"It falls for {inp.view_time} s.", grounded_in=["simulation"], follow_ups=["Why?"])


@pytest.fixture
def setup(tmp_path):
    client, store = ChatClient(), LocalStore(tmp_path)
    outcome = JobOutcome(job_id=JID, status="approved", revisions=1, spec=SPEC, playback_key=f"jobs/{JID}/playback.json",
                         research=ResearchOutput(summary="free fall", phenomena=["gravity"], facts=[]))
    store.put(outcome_key(JID), outcome.model_dump_json().encode())

    async def factory():
        return client

    with TestClient(create_app(factory, store)) as tc:
        yield tc, client


def test_a_question_goes_with_the_jobs_own_record(setup):
    tc, client = setup
    history = [{"role": "user", "content": f"q{i}"} if i % 2 == 0 else {"role": "assistant", "content": f"a{i}"}
               for i in range(25)]
    r = tc.post(f"/jobs/{JID}/chat", json={"messages": history, "view_time": 0.5})
    assert r.status_code == 200, r.text
    assert r.json() == {"answer": "It falls for 0.5 s.", "grounded_in": ["simulation"], "follow_ups": ["Why?"]}
    name, inp, kw = client.executed[0]
    assert name == "SimulationChat" and kw["task_queue"] == "workflow" and kw["id"].startswith(f"chat-{JID}-")
    assert inp.spec.title == "Drop" and inp.research.summary == "free fall"
    assert inp.playback_key == f"jobs/{JID}/playback.json" and inp.prompt == "drop a ball"
    assert len(inp.messages) == 16 and inp.messages[-1].content == "q24"  # the latest, the question last


def test_bad_questions_are_refused(setup):
    tc, client = setup
    url = f"/jobs/{JID}/chat"
    assert tc.post(url, json={"messages": []}).status_code == 422
    assert tc.post(url, json={"messages": [{"role": "assistant", "content": "hi"}]}).status_code == 422
    assert tc.post(url, json={"messages": [{"role": "user", "content": "  "}]}).status_code == 422
    assert tc.post(url, json={"messages": [{"role": "user", "content": "x" * 2001}]}).status_code == 422
    assert tc.post(url, json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422
    assert tc.post("/jobs/" + "d" * 32 + "/chat", json={"messages": [{"role": "user", "content": "x"}]}).status_code == 404
    assert client.executed == []


def test_a_failed_answer_says_why(setup):
    tc, client = setup
    client.fail = RuntimeError("Error code: 429 - insufficient_quota credit_balance_exhausted")
    r = tc.post(f"/jobs/{JID}/chat", json={"messages": [{"role": "user", "content": "why?"}]})
    assert r.status_code == 502 and r.json()["detail"]
    client.fail = RuntimeError("boom")
    r = tc.post(f"/jobs/{JID}/chat", json={"messages": [{"role": "user", "content": "why?"}]})
    assert r.status_code == 502 and "try again" in r.json()["detail"]
