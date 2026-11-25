"""Public library of published simulations (Postgres). Only the listing lives here: the simulation itself is the
job's snapshot and artifacts in the ArtifactStore, so a published simulation outlives its Temporal history."""

from __future__ import annotations

import psycopg

from .db import database_url, pool
from .models import PublishedSimulation

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS published_simulations (
        job_id text PRIMARY KEY,
        title text NOT NULL,
        description text NOT NULL DEFAULT '',
        prompt text NOT NULL DEFAULT '',
        fidelity text NOT NULL DEFAULT 'heuristic',
        domains text[] NOT NULL DEFAULT '{}',
        has_thumbnail boolean NOT NULL DEFAULT false,
        published_at timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS published_simulations_recent ON published_simulations (published_at DESC, job_id)",
]
MAX_LIMIT = 100


async def migrate(url: str | None = None) -> None:
    async with await psycopg.AsyncConnection.connect(database_url(url), autocommit=True) as conn:
        await conn.execute("SELECT pg_advisory_lock(727275)")
        try:
            for stmt in SCHEMA:
                await conn.execute(stmt)  # type: ignore[arg-type]
        finally:
            await conn.execute("SELECT pg_advisory_unlock(727275)")


def _like(q: str) -> str:
    """ILIKE pattern matching `q` literally (its own % and _ escaped)."""
    return "%" + q.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_") + "%"


async def publish(sim: PublishedSimulation) -> PublishedSimulation:
    """Insert or update the listing; re-publishing keeps the original publish time."""
    p = await pool()
    async with p.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO published_simulations (job_id, title, description, prompt, fidelity, domains, has_thumbnail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (job_id) DO UPDATE SET title = EXCLUDED.title, "
            "description = EXCLUDED.description, prompt = EXCLUDED.prompt, fidelity = EXCLUDED.fidelity, "
            "domains = EXCLUDED.domains, has_thumbnail = EXCLUDED.has_thumbnail RETURNING *",
            (sim.job_id, sim.title, sim.description, sim.prompt, sim.fidelity, sim.domains, sim.has_thumbnail),
        )
        return PublishedSimulation(**await cur.fetchone())  # type: ignore[arg-type]


async def unpublish(job_id: str) -> bool:
    p = await pool()
    async with p.connection() as conn:
        cur = await conn.execute("DELETE FROM published_simulations WHERE job_id = %s", (job_id,))
        return cur.rowcount > 0


async def get(job_id: str) -> PublishedSimulation | None:
    p = await pool()
    async with p.connection() as conn:
        cur = await conn.execute("SELECT * FROM published_simulations WHERE job_id = %s", (job_id,))
        row = await cur.fetchone()
    return PublishedSimulation(**row) if row else None


async def search(q: str = "", limit: int = 24, offset: int = 0) -> list[PublishedSimulation]:
    """Newest first; `q` matches title, description or prompt (case-insensitive substring)."""
    limit, offset = max(1, min(limit, MAX_LIMIT)), max(0, offset)
    where, params = "", []
    if q.strip():
        where = "WHERE title ILIKE %s OR description ILIKE %s OR prompt ILIKE %s"
        params = [_like(q.strip())] * 3
    p = await pool()
    async with p.connection() as conn:
        cur = await conn.execute(
            f"SELECT * FROM published_simulations {where} "  # fixed clause; values parameterised
            "ORDER BY published_at DESC, job_id LIMIT %s OFFSET %s",
            (*params, limit, offset),
        )
        return [PublishedSimulation(**r) for r in await cur.fetchall()]
