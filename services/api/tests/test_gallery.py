"""Finished-job snapshots, artifact caching headers, and the opt-in public library."""

import base64

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from omnific_api.app import LRU, create_app, etag_matches, outcome_key, sniff_image
from omnific_common.contracts import JobOutcome
from omnific_common.storage import LocalStore
from omnific_rag.models import PublishedSimulation
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, TimeSpan
from temporalio.client import WorkflowExecutionStatus
from test_api import FakeClient, FakeHandle

JID = "c" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SPEC = SimSpec(title="Ball drop", prompt="drop a ball from 10 m", domains=[Domain.MECHANICS],
               fidelity=Fidelity.ANALYTIC, assumptions=[], parameters=[], state=[], equations=[],
               time=TimeSpan(end=1.0), entities=[Entity(name="ball", kind="ball")])


class FakeGallery:
    def __init__(self):
        self.items: dict[str, PublishedSimulation] = {}
        self.migrated = False

    async def migrate(self):
        self.migrated = True

    async def publish(self, sim):
        self.items[sim.job_id] = sim
        return sim

    async def unpublish(self, job_id):
        return self.items.pop(job_id, None) is not None

    async def get(self, job_id):
        return self.items.get(job_id)

    async def search(self, q="", limit=24, offset=0):
        hits = [s for s in self.items.values() if q.lower() in (s.title + s.prompt).lower()]
        return hits[offset:offset + limit]


def approved(jid=JID, **kw) -> JobOutcome:
    return JobOutcome(job_id=jid, status="approved", revisions=1, spec=SPEC,
                      playback_key=f"jobs/{jid}/playback.json", **kw)


@pytest.fixture
def setup(tmp_path):
    client, store, gallery = FakeClient(), LocalStore(tmp_path), FakeGallery()

    async def factory():
        return client

    with TestClient(create_app(factory, store, gallery=gallery)) as tc:
        yield tc, client, store, gallery


def test_finished_job_is_snapshotted_and_served_without_temporal(setup):
    tc, client, store, _ = setup
    store.put(f"jobs/{JID}/playback.json", b'{"t": []}')
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, approved())
    assert tc.get(f"/jobs/{JID}").json()["result"]["status"] == "approved"
    assert store.exists(outcome_key(JID))
    # Temporal forgets the job (local dev server restart): the snapshot still serves page and artifacts
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, missing=True)
    view = tc.get(f"/jobs/{JID}").json()
    assert view["state"] == "completed" and view["status"]["stage"] == "approved"
    assert tc.get(f"/jobs/{JID}/artifacts/playback.json").content == b'{"t": []}'


def test_snapshot_from_a_real_handle_result_dict(setup):
    """The real Temporal handle (no result_type) returns the outcome as a dict, not a JobOutcome."""
    tc, client, store, _ = setup
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, approved().model_dump(mode="json"))
    r = tc.get(f"/jobs/{JID}")
    assert r.status_code == 200 and r.json()["result"]["spec"]["title"] == "Ball drop"
    assert store.exists(outcome_key(JID))


def test_snapshot_survives_a_new_api_process(tmp_path):
    store = LocalStore(tmp_path)
    store.put(outcome_key(JID), approved().model_dump_json().encode())
    client = FakeClient()
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, missing=True)

    async def factory():
        return client

    with TestClient(create_app(factory, store)) as tc:
        assert tc.get(f"/jobs/{JID}").json()["result"]["spec"]["title"] == "Ball drop"


def test_cache_headers_final_vs_draft(setup):
    tc, client, store, _ = setup
    store.put(f"jobs/{JID}/playback.json", b'{"t": [1]}')
    draft = approved().model_copy(update={"status": "awaiting_review"})
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, draft=draft, stage="awaiting_review")
    r = tc.get(f"/jobs/{JID}/artifacts/playback.json")
    assert r.headers["cache-control"] == "no-cache" and r.headers["etag"]
    again = tc.get(f"/jobs/{JID}/artifacts/playback.json", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.content == b""

    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, approved())
    r = tc.get(f"/jobs/{JID}/artifacts/playback.json")
    assert "immutable" in r.headers["cache-control"]
    assert tc.get(f"/jobs/{JID}/artifacts/playback.json",
                  headers={"If-None-Match": f'W/{r.headers["etag"]}'}).status_code == 304


def test_large_artifacts_are_gzipped(setup):
    tc, client, store, _ = setup
    store.put(f"jobs/{JID}/playback.json", b'{"t": [' + b"0.0," * 5000 + b"0.0]}")
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, approved())
    r = tc.get(f"/jobs/{JID}/artifacts/playback.json", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"


def test_publish_requires_an_accepted_simulation(setup):
    tc, client, _, _ = setup
    client.handle = FakeHandle(WorkflowExecutionStatus.RUNNING, stage="awaiting_review")
    assert tc.post(f"/jobs/{JID}/publish", json={}).status_code == 409
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, approved().model_copy(update={"status": "failed"}))
    assert tc.post(f"/jobs/{JID}/publish", json={}).status_code == 409
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, missing=True)
    assert tc.post(f"/jobs/{'d' * 32}/publish", json={}).status_code == 404


def test_publish_list_and_unpublish(setup):
    tc, client, _, gallery = setup
    assert gallery.migrated
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, approved())
    thumb = "data:image/png;base64," + base64.b64encode(PNG).decode()
    r = tc.post(f"/jobs/{JID}/publish", json={"description": "A classic.", "thumbnail": thumb})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Ball drop" and body["fidelity"] == "analytic" and body["has_thumbnail"]
    assert body["prompt"] == "drop a ball from 10 m" and body["domains"] == ["mechanics"]
    t = tc.get(f"/jobs/{JID}/thumbnail")
    assert t.content == PNG and t.headers["content-type"] == "image/png"

    # custom title; an existing thumbnail is kept when none is sent
    assert tc.post(f"/jobs/{JID}/publish", json={"title": "  My drop "}).json()["title"] == "My drop"
    assert gallery.items[JID].has_thumbnail
    assert [s["job_id"] for s in tc.get("/gallery", params={"q": "drop"}).json()] == [JID]
    assert tc.get("/gallery", params={"q": "nothing"}).json() == []
    assert tc.get(f"/gallery/{JID}").status_code == 200

    assert tc.delete(f"/jobs/{JID}/publish").status_code == 204
    assert tc.get(f"/gallery/{JID}").status_code == 404
    assert tc.delete(f"/jobs/{JID}/publish").status_code == 404


@pytest.mark.parametrize(("thumb", "code"), [
    ("not a data url", 422),
    ("data:image/png;base64,@@@", 422),
    ("data:image/png;base64," + base64.b64encode(b"GIF89a....").decode(), 415),
    ("data:text/html;base64," + base64.b64encode(PNG).decode(), 422),
])
def test_publish_rejects_bad_thumbnails(setup, thumb, code):
    tc, client, _, gallery = setup
    client.handle = FakeHandle(WorkflowExecutionStatus.COMPLETED, approved())
    assert tc.post(f"/jobs/{JID}/publish", json={"thumbnail": thumb}).status_code == code
    assert JID not in gallery.items


def test_gallery_503_without_database(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_DATABASE_URL", raising=False)
    client = FakeClient()

    async def factory():
        return client

    with TestClient(create_app(factory, LocalStore(tmp_path))) as tc:
        assert tc.get("/gallery").status_code == 503
        assert tc.get(f"/gallery/{JID}").status_code == 503


def test_helpers():
    assert sniff_image(PNG) == "image/png" and sniff_image(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp" and sniff_image(b"<svg") is None
    assert etag_matches('"a", W/"b"', '"b"') and etag_matches("*", '"x"') and not etag_matches(None, '"x"')
    lru = LRU(max_items=10, max_bytes=100)
    lru.put("a", b"x" * 25)
    lru.put("b", b"x" * 25)
    lru.get("a")  # a is now most recent
    for k in "cde":
        lru.put(k, b"x" * 25)
    assert lru.get("b") is None and lru.get("a") is not None and lru.size <= 100
    lru.put("huge", b"x" * 60)  # over a quarter of the budget: never cached
    assert lru.get("huge") is None
