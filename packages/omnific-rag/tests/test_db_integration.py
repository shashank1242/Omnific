"""Integration tests against a real Postgres+pgvector. Set RAG_TEST_DATABASE_URL to run."""

import os
from urllib.parse import urlsplit

import pytest
from omnific_rag import db, search
from omnific_rag.models import ChunkIn

URL = os.environ.get("RAG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="RAG_TEST_DATABASE_URL not set")
# These tests TRUNCATE tables: refuse anything that isn't clearly a throwaway test database.
if URL and not urlsplit(URL).path.rstrip("/").endswith("_test"):
    raise RuntimeError(f"RAG_TEST_DATABASE_URL must name a database ending in _test (got {urlsplit(URL).path!r})")

DIM = db.EMBEDDING_DIM


def vec(*hot: int) -> list[float]:
    v = [0.0] * DIM
    for h in hot:
        v[h] = 1.0
    return v


class FakeLLM:
    """Embeds a query as the one-hot vector named by the first word 'v<number>'."""

    async def embed(self, texts):
        out = []
        for t in texts:
            hot = [int(w[1:]) for w in t.split() if w.startswith("v") and w[1:].isdigit()]
            out.append(vec(*hot) if hot else vec(DIM - 1))
        return out


@pytest.fixture
async def rag(monkeypatch):
    monkeypatch.setenv("RAG_DATABASE_URL", URL)
    monkeypatch.setattr(search, "llm", FakeLLM())
    await db.migrate()
    p = await db.pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE documents CASCADE")
    yield
    await db.close_pool()


def chunk(ord_, content, hot, kind="text", page=1, section=None, context=None):
    return ChunkIn(ord=ord_, kind=kind, page=page, section=section, context=context,
                   content=content, embedding=vec(hot))


async def test_migrate_idempotent(rag):
    await db.migrate()
    await db.migrate()
    p = await db.pool()
    async with p.connection() as conn:
        cur = await conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'chunks' ORDER BY 1")
        names = {r["indexname"] for r in await cur.fetchall()}
    assert {"chunks_embedding_hnsw", "chunks_tsv_gin", "chunks_document_id"} <= names


async def test_document_crud(rag):
    d = await db.create_document("d1", "a.pdf", "application/pdf", meta={"k": 1})
    assert d.status == "pending" and d.meta == {"k": 1} and d.created_at is not None
    assert (await db.get_document("d1")).filename == "a.pdf"
    assert await db.get_document("nope") is None
    await db.create_document("d2", "b.pdf", "application/pdf")
    assert {x.id for x in await db.list_documents()} == {"d1", "d2"}
    assert len(await db.list_documents(limit=1)) == 1
    await db.set_status("d1", "ready", title="T", pages=3, n_chunks=2, n_figures=1, n_tables=0)
    d = await db.get_document("d1")
    assert (d.status, d.title, d.pages, d.n_chunks, d.n_figures) == ("ready", "T", 3, 2, 1)
    await db.set_status("d1", "failed", error="boom")
    d = await db.get_document("d1")
    assert d.status == "failed" and d.error == "boom" and d.title == "T"
    with pytest.raises(ValueError):
        await db.set_status("d1", "weird")
    with pytest.raises(ValueError):
        await db.set_status("d1", "ready", **{"title = 'x', status": "y"})
    assert await db.delete_document("d1") is True
    assert await db.delete_document("d1") is False


async def test_replace_chunks_and_cascade(rag):
    await db.create_document("d1", "a.txt", "text/plain")
    assert await db.replace_chunks("d1", [chunk(0, "alpha", 1), chunk(1, "beta", 2)]) == 2
    assert await db.replace_chunks("d1", [chunk(0, "gamma", 3)]) == 1
    hits = await search.neighbors("d1", 0, span=5)
    assert [h.content for h in hits] == ["gamma"]
    with pytest.raises(ValueError):
        await db.replace_chunks("d1", [ChunkIn(ord=0, content="x", embedding=[0.1])])
    assert [h.content for h in await search.neighbors("d1", 0)] == ["gamma"]  # unchanged
    await db.delete_document("d1")
    p = await db.pool()
    async with p.connection() as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM chunks")
        assert (await cur.fetchone())["n"] == 0


async def test_search_scoping_never_leaks(rag):
    for i in ("a", "b"):
        await db.create_document(i, f"{i}.txt", "text/plain")
    await db.replace_chunks("a", [chunk(0, "turbine blade fatigue", 1)])
    await db.replace_chunks("b", [chunk(0, "turbine blade secret", 1)])
    assert await search.search("v1 turbine", []) == []
    only_a = await search.search("v1 turbine blade", ["a"])
    assert [h.document_id for h in only_a] == ["a"]
    only_b = await search.search("v1 turbine blade", ["b"])
    assert [h.document_id for h in only_b] == ["b"]
    both = await search.search("v1 turbine blade", ["a", "b"])
    assert {h.document_id for h in both} == {"a", "b"}
    assert await search.search("v1 turbine", ["missing"]) == []


async def test_hybrid_finds_keyword_and_vector_matches(rag):
    await db.create_document("a", "a.txt", "text/plain")
    await db.set_status("a", "ready", title="Doc A")
    await db.replace_chunks("a", [
        chunk(0, "unrelated filler", 1),
        chunk(1, "the zirconium alloy yield strength", 2, page=4),   # keyword-only match
        chunk(2, "another filler", 3),
    ])
    hits = await search.search("v3 zirconium", ["a"], k=3)
    ids = [h.ord for h in hits]
    assert set(ids[:2]) == {1, 2}  # one from FTS, one from vector
    top = next(h for h in hits if h.ord == 1)
    assert top.citation_url == "omnific://doc/a#page=4" and top.title == "Doc A"
    assert len(await search.search("v3 zirconium", ["a"], k=1)) == 1


async def test_kinds_filter(rag):
    await db.create_document("a", "a.txt", "text/plain")
    await db.replace_chunks("a", [
        chunk(0, "pressure curve", 1, kind="text"),
        chunk(1, "pressure table", 1, kind="table"),
        chunk(2, "pressure figure", 1, kind="figure"),
    ])
    h = await search.search("v1 pressure", ["a"], kinds=["figure"])
    assert [x.kind for x in h] == ["figure"]
    h = await search.search("v1 pressure", ["a"], kinds=["table", "figure"])
    assert {x.kind for x in h} == {"table", "figure"}
    assert len(await search.search("v1 pressure", ["a"])) == 3


async def test_neighbors(rag):
    await db.create_document("a", "a.txt", "text/plain")
    await db.create_document("b", "b.txt", "text/plain")
    await db.replace_chunks("a", [chunk(i, f"c{i}", i) for i in range(5)])
    await db.replace_chunks("b", [chunk(i, f"other{i}", i) for i in range(5)])
    assert [h.content for h in await search.neighbors("a", 2)] == ["c1", "c2", "c3"]
    assert [h.content for h in await search.neighbors("a", 0)] == ["c0", "c1"]
    assert [h.content for h in await search.neighbors("a", 4, span=2)] == ["c2", "c3", "c4"]
    assert [h.content for h in await search.neighbors("a", 2, span=0)] == ["c2"]
    assert await search.neighbors("zzz", 0) == []


async def test_sql_injection_safe(rag):
    await db.create_document("a", "a.txt", "text/plain")
    await db.replace_chunks("a", [chunk(0, "hello world", 1)])
    nasty = "x'); DROP TABLE chunks; --"
    await search.search(nasty, ["a"])
    await search.search("v1", ["a'; DROP TABLE documents; --"], kinds=["text'; --"])
    assert len(await search.neighbors("a", 0)) == 1
