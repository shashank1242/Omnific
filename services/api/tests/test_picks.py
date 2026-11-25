"""Asset picker endpoints: choices on the job view, picks, and thumbnails from the job's own choices."""

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from omnific_api.app import create_app, outcome_key
from omnific_common.contracts import (
    AssetChoices,
    AssetOption,
    EntityChoices,
    JobOutcome,
    LibraryCandidate,
)
from omnific_common.storage import LocalStore
from temporalio.client import WorkflowExecutionStatus
from test_api import FakeClient, FakeHandle

JID = "b" * 32


def choices(key=f"jobs/{JID}/choices/car/0.png"):
    c = LibraryCandidate(source="fake", id="2", name="Car", license="CC0 1.0", author="ann", url="https://x.test/2")
    return AssetChoices(entities=[EntityChoices(entity="car", kind="car", default_route="library",
                                                routes_available=["library", "procedural"],
                                                options=[AssetOption(candidate=c, thumb_key=key, score=9)])])


@pytest.fixture
def setup(tmp_path):
    client, store = FakeClient(), LocalStore(tmp_path)

    async def factory():
        return client

    with TestClient(create_app(factory, store)) as tc:
        yield tc, client, store


def test_job_view_carries_choices_while_building(setup):
    tc, client, _ = setup
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="awaiting_asset_picks", choices=choices())
    view = tc.get(f"/jobs/{JID}").json()
    assert view["asset_choices"]["entities"][0]["options"][0]["candidate"]["id"] == "2"
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="packaging", choices=choices())
    assert tc.get(f"/jobs/{JID}").json()["asset_choices"] is None  # too late to pick: not shown


def test_picks_are_signalled_until_packaging(setup):
    tc, client, _ = setup
    body = {"picks": [{"entity": "car", "route": "library", "candidate_id": "fake/2"}]}
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="running", choices=choices())
    assert tc.post(f"/jobs/{JID}/assets/picks", json=body).status_code == 202
    name, arg = client.handle.signals[-1]
    assert name == "pick_assets" and arg.picks[0].candidate_id == "fake/2"
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="packaging", choices=choices())
    assert tc.post(f"/jobs/{JID}/assets/picks", json=body).status_code == 409
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="running", choices=None)
    assert tc.post(f"/jobs/{JID}/assets/picks", json=body).status_code == 409
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED)
    assert tc.post(f"/jobs/{JID}/assets/picks", json=body).status_code == 409
    bad = {"picks": [{"entity": "car", "route": "teleport"}]}
    assert tc.post(f"/jobs/{JID}/assets/picks", json=bad).status_code == 422


def test_thumbnails_only_from_the_jobs_own_choices(setup):
    tc, client, store = setup
    store.put(f"jobs/{JID}/choices/car/0.png", b"\x89PNG-thumb")
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, choices=choices())
    r = tc.get(f"/jobs/{JID}/choices/car/0/thumb")
    assert r.status_code == 200 and r.content == b"\x89PNG-thumb" and r.headers["content-type"] == "image/png"
    assert tc.get(f"/jobs/{JID}/choices/car/5/thumb").status_code == 404
    assert tc.get(f"/jobs/{JID}/choices/boat/0/thumb").status_code == 404
    # a key outside this job's choices prefix is never served, even if the choices name it
    store.put("jobs/other/secret.png", b"secret")
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, choices=choices("jobs/other/secret.png"))
    assert tc.get(f"/jobs/{JID}/choices/car/0/thumb").status_code == 404
    # a finished job serves them from its snapshot
    store.put(outcome_key(JID), JobOutcome(job_id=JID, status="approved", revisions=1,
                                           asset_choices=choices()).model_dump_json().encode())
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, missing=True)
    r = tc.get(f"/jobs/{JID}/choices/car/0/thumb")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
