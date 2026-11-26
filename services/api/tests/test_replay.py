"""Demo replay: a prompt that matches a published simulation opens it, with no workflow (and no AI) started."""

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from omnific_api.app import create_app, normalise_prompt, outcome_key
from omnific_common.contracts import JobOutcome
from omnific_common.storage import LocalStore
from omnific_rag.models import PublishedSimulation
from test_api import FakeClient
from test_gallery import FakeGallery

JID = "e" * 32
PROMPT = "Atomic bomb exploding in the event horizon of a black hole"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_REPLAY", raising=False)
    client, store, gallery = FakeClient(), LocalStore(tmp_path), FakeGallery()
    store.put(outcome_key(JID), JobOutcome(job_id=JID, status="approved", revisions=1).model_dump_json().encode())
    gallery.items[JID] = PublishedSimulation(job_id=JID, title="Black hole", description="", prompt=PROMPT,
                                             fidelity="heuristic", domains=[], has_thumbnail=False)

    async def factory():
        return client

    with TestClient(create_app(factory, store, gallery=gallery)) as tc:
        yield tc, client, gallery


def test_normalise_ignores_case_spacing_and_punctuation():
    assert normalise_prompt("  Atomic bomb, exploding in the EVENT horizon of a black-hole!! ") == \
        "atomic bomb exploding in the event horizon of a black hole"
    assert normalise_prompt("Café_Straße ÜBER") == "café strasse über"


@pytest.mark.parametrize("prompt", [PROMPT, "atomic   bomb exploding in the event horizon of a black hole."])
def test_a_published_prompt_replays_its_simulation(setup, prompt):
    tc, client, _ = setup
    r = tc.post("/jobs", json={"prompt": prompt})
    assert r.status_code == 200 and r.json() == {"job_id": JID, "replayed": True}
    assert client.started == []


def test_other_requests_run_the_pipeline(setup, monkeypatch):
    tc, client, gallery = setup
    assert tc.post("/jobs", json={"prompt": "a ball rolls down a ramp"}).json()["replayed"] is False
    assert tc.post("/jobs", json={"prompt": PROMPT, "input_ids": ["f" * 32]}).status_code == 422  # unknown input
    monkeypatch.setenv("DEMO_REPLAY", "0")
    assert tc.post("/jobs", json={"prompt": PROMPT}).json()["replayed"] is False
    monkeypatch.delenv("DEMO_REPLAY")

    async def broken(*a):
        raise RuntimeError("db down")

    gallery.search = broken
    assert tc.post("/jobs", json={"prompt": PROMPT}).json()["replayed"] is False
    assert [name for name, _, _ in client.started] == ["SimulationJob"] * 3
