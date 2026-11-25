"""Public library against a real Postgres. Set RAG_TEST_DATABASE_URL (a database ending in _test) to run."""

import os
from urllib.parse import urlsplit

import pytest
from omnific_rag import db, gallery
from omnific_rag.models import PublishedSimulation

URL = os.environ.get("RAG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="RAG_TEST_DATABASE_URL not set")
if URL and not urlsplit(URL).path.rstrip("/").endswith("_test"):
    raise RuntimeError(f"RAG_TEST_DATABASE_URL must name a database ending in _test (got {urlsplit(URL).path!r})")


@pytest.fixture
async def lib(monkeypatch):
    monkeypatch.setenv("RAG_DATABASE_URL", URL)
    await db.migrate()
    await gallery.migrate()
    p = await db.pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE published_simulations")
    yield
    await db.close_pool()


def sim(i: int, **kw) -> PublishedSimulation:
    return PublishedSimulation(job_id=f"{i:032x}", title=kw.pop("title", f"Sim {i}"), **kw)


async def test_publish_get_search_unpublish(lib):
    first = await gallery.publish(sim(1, prompt="a pendulum swinging", domains=["mechanics"], fidelity="analytic"))
    assert first.published_at is not None and first.domains == ["mechanics"]
    await gallery.publish(sim(2, title="100% heat_flow", description="Cooling coffee"))

    # republish updates the listing but keeps the original publish time
    again = await gallery.publish(sim(1, title="Pendulum", has_thumbnail=True))
    assert again.title == "Pendulum" and again.has_thumbnail and again.published_at == first.published_at

    assert [s.job_id for s in await gallery.search()] == [f"{2:032x}", f"{1:032x}"]  # newest first
    assert [s.title for s in await gallery.search("PENDULUM")] == ["Pendulum"]
    assert [s.title for s in await gallery.search("coffee")] == ["100% heat_flow"]
    # % and _ are matched literally, not as wildcards
    assert [s.title for s in await gallery.search("0% h")] == ["100% heat_flow"]
    assert await gallery.search("0_ h") == []  # would match "0% h" if _ were a wildcard
    assert len(await gallery.search(limit=1)) == 1 and len(await gallery.search(offset=1)) == 1

    assert (await gallery.get(f"{1:032x}")).title == "Pendulum"
    assert await gallery.unpublish(f"{1:032x}") and not await gallery.unpublish(f"{1:032x}")
    assert await gallery.get(f"{1:032x}") is None
