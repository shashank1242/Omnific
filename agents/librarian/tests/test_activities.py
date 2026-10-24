import pytest
from omnific_common.contracts import IngestInput
from omnific_common.llm import Role
from omnific_common.storage import LocalStore
from omnific_librarian import activities, chunking
from omnific_rag.models import Document
from temporalio.exceptions import ApplicationError

from . import fixtures as fx


class FakeDB:
    def __init__(self):
        self.docs = {}
        self.chunks = {}
        self.statuses = []

    async def get_document(self, id):
        return self.docs.get(id)

    async def create_document(self, id, filename, content_type, meta=None):
        self.docs[id] = Document(id=id, filename=filename, content_type=content_type)
        return self.docs[id]

    async def set_status(self, id, status, error=None, **counts):
        self.statuses.append((status, error, counts))

    async def replace_chunks(self, document_id, chunks):
        self.chunks[document_id] = chunks
        return len(chunks)


class FakeLLM:
    def __init__(self, fail_embed=False):
        self.fail_embed = fail_embed
        self.embedded = []
        self.roles = []

    async def structured(self, role, instructions, input, schema, tools=None):
        self.roles.append(role)
        if schema is chunking.Contexts:
            n = sum(1 for line in input.splitlines() if line.startswith("[") and "kind=" in line)
            return chunking.Contexts(contexts=[f"Context {i}." for i in range(n)])
        return chunking.TitleProposal(title="Proposed Title")

    async def describe_image(self, role, prompt, image, mime):
        return "Line chart of drag vs speed. Axes uncertain."

    async def embed(self, texts):
        if self.fail_embed:
            raise ConnectionError("embedding service unavailable")
        self.embedded = texts
        return [[0.1] * 1536 for _ in texts]


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    fdb, fllm = FakeDB(), FakeLLM()
    monkeypatch.setattr(activities, "store_from_env", lambda: store)
    monkeypatch.setattr(activities, "db", fdb)
    monkeypatch.setattr(activities, "llm", fllm)
    return store, fdb, fllm


def inp(name, ctype="application/octet-stream", key="raw/x"):
    return IngestInput(document_id="d1", filename=name, content_type=ctype, raw_key=key)


async def test_pdf_end_to_end(env):
    store, fdb, fllm = env
    store.put("raw/x", fx.make_pdf())
    out = await activities.ingest_document(inp("report.pdf", "application/pdf"))
    assert out.title == "Wind Tunnel Report" and out.pages == 2
    assert out.n_tables == 1 and out.n_figures == 1 and out.n_chunks == len(fdb.chunks["d1"])
    assert [s[0] for s in fdb.statuses] == ["ingesting", "ready"]
    ready = fdb.statuses[-1][2]
    assert ready["n_chunks"] == out.n_chunks and ready["pages"] == 2 and ready["title"] == out.title
    chunks = fdb.chunks["d1"]
    assert [c.ord for c in chunks] == list(range(len(chunks)))
    assert {c.kind for c in chunks} == {"text", "table", "figure"}
    assert all(c.context and len(c.embedding) == 1536 for c in chunks)
    # embedded text = section + context + content
    c0 = chunks[0]
    assert fllm.embedded[0] == "\n".join(x for x in (c0.section, c0.context, c0.content) if x)
    assert set(fllm.roles) == {Role.FAST}


async def test_title_proposed_when_missing(env):
    store, _, _ = env
    store.put("raw/x", b"Just a plain paragraph of notes.\n\nAnother one.")
    out = await activities.ingest_document(inp("notes.txt", "text/plain"))
    assert out.title == "Proposed Title" and out.n_chunks == 1


async def test_creates_missing_document_row(env):
    store, fdb, _ = env
    store.put("raw/x", fx.MD)
    await activities.ingest_document(inp("n.md"))
    assert "d1" in fdb.docs


async def test_unsupported_is_non_retryable_and_marks_failed(env):
    store, fdb, _ = env
    store.put("raw/x", b"MZ")
    with pytest.raises(ApplicationError) as ei:
        await activities.ingest_document(inp("tool.exe"))
    assert ei.value.non_retryable
    status, error, _ = fdb.statuses[-1]
    assert status == "failed" and "unsupported" in error
    assert "d1" not in fdb.chunks


async def test_corrupt_is_non_retryable(env):
    store, fdb, _ = env
    store.put("raw/x", b"%PDF-1.4 garbage")
    with pytest.raises(ApplicationError) as ei:
        await activities.ingest_document(inp("bad.pdf"))
    assert ei.value.non_retryable and fdb.statuses[-1][0] == "failed"


async def test_empty_document_is_non_retryable(env):
    store, fdb, _ = env
    store.put("raw/x", b"   \n\n  ")
    with pytest.raises(ApplicationError) as ei:
        await activities.ingest_document(inp("empty.txt"))
    assert ei.value.non_retryable and "no extractable content" in fdb.statuses[-1][1]


async def test_too_large_is_non_retryable(env, monkeypatch):
    store, fdb, _ = env
    monkeypatch.setenv("RAG_MAX_UPLOAD_MB", "0.000001")
    store.put("raw/x", b"x" * 100)
    with pytest.raises(ApplicationError) as ei:
        await activities.ingest_document(inp("big.txt"))
    assert ei.value.non_retryable and "RAG_MAX_UPLOAD_MB" in fdb.statuses[-1][1]


async def test_transient_error_is_retryable_and_marks_failed(env, monkeypatch):
    store, fdb, _ = env
    store.put("raw/x", fx.MD)
    monkeypatch.setattr(activities, "llm", FakeLLM(fail_embed=True))
    with pytest.raises(ConnectionError):
        await activities.ingest_document(inp("n.md"))
    status, error, _ = fdb.statuses[-1]
    assert status == "failed" and "ConnectionError" in error
    assert not isinstance(ConnectionError(), ApplicationError)


async def test_missing_raw_file_fails(env):
    _, fdb, _ = env
    with pytest.raises(FileNotFoundError):
        await activities.ingest_document(inp("n.md", key="raw/missing"))
    assert fdb.statuses[-1][0] == "failed"


async def test_figure_cap_reported_in_warnings(env, monkeypatch):
    store, _, _ = env
    monkeypatch.setenv("RAG_MAX_FIGURES", "0")
    store.put("raw/x", fx.make_docx())
    out = await activities.ingest_document(inp("h.docx"))
    assert out.n_figures == 0 and any("skipped" in w for w in out.warnings)
    assert out.n_tables == 1
