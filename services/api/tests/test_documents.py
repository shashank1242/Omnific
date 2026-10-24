import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from omnific_api.app import create_app, safe_filename
from omnific_common.contracts import ClarifyAnswers, ClarifyQuestion, IngestInput, JobStatus
from omnific_common.storage import LocalStore
from temporalio.client import WorkflowExecutionStatus
from test_api import FakeClient, FakeHandle


class FakeRag:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.migrated = False
        self.statuses = []

    async def migrate(self):
        self.migrated = True

    async def create_document(self, id, filename, content_type, meta=None):
        self.docs[id] = {"id": id, "filename": filename, "content_type": content_type,
                         "status": "pending", "meta": meta or {}}
        return self.docs[id]

    async def get_document(self, id):
        return self.docs.get(id)

    async def list_documents(self, limit=100):
        return list(self.docs.values())

    async def delete_document(self, id):
        return self.docs.pop(id, None) is not None

    async def set_status(self, id, status, error=None, **counts):
        self.statuses.append((id, status, error))
        self.docs[id]["status"] = status


Q = ClarifyQuestion(id="altitude", question="Altitude?", why="w", kind="number", options=[],
                    unit="meter", default="0")


class QHandle(FakeHandle):
    async def query(self, name, result_type=None):
        if name == "questions":
            return [Q]
        return await super().query(name, result_type)


@pytest.fixture
def setup(tmp_path):
    client, store, rag = FakeClient(), LocalStore(tmp_path), FakeRag()

    async def factory():
        return client

    with TestClient(create_app(factory, store, rag)) as tc:
        yield tc, client, store, rag


def test_migrate_in_lifespan(setup):
    assert setup[3].migrated


def test_upload_ok(setup):
    tc, client, store, rag = setup
    r = tc.post("/documents", files={"file": ("../my spec!.pdf", b"%PDF-1", "application/pdf")})
    assert r.status_code == 202
    doc_id = r.json()["document_id"]
    key = f"documents/{doc_id}/my_spec_.pdf"
    assert store.get(key) == b"%PDF-1"
    assert rag.docs[doc_id]["filename"] == "my_spec_.pdf"
    name, arg, kw = client.started[0]
    assert name == "DocumentIngest" and kw["id"] == f"ingest-{doc_id}" and kw["task_queue"] == "workflow"
    assert isinstance(arg, IngestInput) and arg.raw_key == key and arg.content_type == "application/pdf"


def test_upload_rejections(setup, monkeypatch):
    tc, client, _, _ = setup
    assert tc.post("/documents", files={"file": ("a.exe", b"x", "application/octet-stream")}).status_code == 415
    assert tc.post("/documents", files={"file": ("a.pdf", b"x", "video/mp4")}).status_code == 415
    assert tc.post("/documents", files={"file": ("a.pdf", b"", "application/pdf")}).status_code == 422
    monkeypatch.setenv("RAG_MAX_UPLOAD_MB", "0.001")
    assert tc.post("/documents", files={"file": ("a.txt", b"x" * 2000, "text/plain")}).status_code == 413
    assert client.started == []


def test_document_crud(setup):
    tc = setup[0]
    doc_id = tc.post("/documents", files={"file": ("a.txt", b"hi", "text/plain")}).json()["document_id"]
    assert tc.get(f"/documents/{doc_id}").json()["status"] == "pending"
    assert [d["id"] for d in tc.get("/documents").json()] == [doc_id]
    assert tc.delete(f"/documents/{doc_id}").status_code == 204
    assert tc.get(f"/documents/{doc_id}").status_code == 404
    assert tc.delete(f"/documents/{doc_id}").status_code == 404
    assert tc.get("/documents/nope").status_code == 404


def test_documents_503_without_rag(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_DATABASE_URL", raising=False)

    async def factory():
        return FakeClient()

    with TestClient(create_app(factory, LocalStore(tmp_path))) as tc:
        assert tc.get("/documents").status_code == 503
        assert tc.post("/documents", files={"file": ("a.txt", b"hi", "text/plain")}).status_code == 503
        assert tc.post("/jobs", json={"prompt": "x", "document_ids": ["a" * 32]}).status_code == 503
        assert tc.post("/jobs", json={"prompt": "x"}).status_code == 200


def test_job_with_documents(setup):
    tc, client, _, rag = setup
    ready, pending = "a" * 32, "b" * 32
    rag.docs[ready] = {"id": ready, "status": "ready"}
    rag.docs[pending] = {"id": pending, "status": "ingesting"}
    assert tc.post("/jobs", json={"prompt": "x", "document_ids": [ready, pending]}).status_code == 422
    assert tc.post("/jobs", json={"prompt": "x", "document_ids": ["c" * 32]}).status_code == 422
    assert client.started == []
    r = tc.post("/jobs", json={"prompt": "x", "document_ids": [ready], "clarify": False,
                               "clarify_timeout_hours": 2})
    assert r.status_code == 200
    req = client.started[0][1]
    assert req.document_ids == [ready] and req.clarify is False and req.clarify_timeout_hours == 2
    tc.post("/jobs", json={"prompt": "y"})
    assert client.started[1][1].clarify is True  # default kept


def test_get_job_questions_and_clarify(setup):
    tc, client, _, _ = setup
    jid = "e" * 32
    client.handle = QHandle(WorkflowExecutionStatus.RUNNING, stage="awaiting_clarification")
    body = tc.get(f"/jobs/{jid}").json()
    assert body["questions"][0]["id"] == "altitude"
    r = tc.post(f"/jobs/{jid}/clarify", json={"answers": [{"id": "altitude", "value": "10"}]})
    assert r.status_code == 202
    name, arg = client.handle.signals[0]
    assert name == "clarify" and isinstance(arg, ClarifyAnswers) and arg.answers[0].value == "10"
    assert tc.post(f"/jobs/{jid}/clarify", json={"use_defaults": True}).status_code == 202


def test_clarify_conflicts(setup):
    tc, client, _, _ = setup
    jid = "f" * 32
    client.handle = QHandle(WorkflowExecutionStatus.RUNNING, stage="researching")
    assert tc.get(f"/jobs/{jid}").json()["questions"] is None
    assert tc.post(f"/jobs/{jid}/clarify", json={"use_defaults": True}).status_code == 409
    client.handle = QHandle(WorkflowExecutionStatus.COMPLETED, stage="awaiting_clarification")
    assert tc.post(f"/jobs/{jid}/clarify", json={"use_defaults": True}).status_code == 409
    assert client.handle.signals == []
    client.handle = QHandle(WorkflowExecutionStatus.RUNNING, stage="awaiting_clarification")
    assert tc.post(f"/jobs/{jid}/clarify", json={"answers": [{"id": "x"}]}).status_code == 422
    assert tc.post("/jobs/nope/clarify", json={"use_defaults": True}).status_code == 404


def test_safe_filename():
    assert safe_filename("C:\\x\\y z.PDF") == "y_z.PDF"
    assert safe_filename("..") == "document"
    assert safe_filename(None) == "document"
    assert JobStatus  # imported for type parity
