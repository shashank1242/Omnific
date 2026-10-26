"""Mesh route outcomes per entity kind (Postgres), so the mesh router can prefer routes that worked before.

Best-effort by design: the mesh worker calls these only when RAG_DATABASE_URL is set, and callers treat any error
as "no stats". Short-lived connections (a few calls per job) instead of the document pool."""

from __future__ import annotations

from collections import defaultdict

import psycopg
from psycopg.types.json import Jsonb

from .db import database_url

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS mesh_route_stats (
        id bigserial PRIMARY KEY,
        job_id text NOT NULL,
        kind text NOT NULL,
        route text NOT NULL,
        passed boolean NOT NULL,
        attempts int NOT NULL,
        failed_checks jsonb NOT NULL DEFAULT '[]'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS mesh_route_stats_kind ON mesh_route_stats (kind, created_at DESC)",
]
RECENT = 50  # outcomes per kind that count


def norm_kind(kind: str) -> str:
    return " ".join(kind.lower().split())[:120]


async def _connect(url: str | None) -> psycopg.AsyncConnection:
    conn = await psycopg.AsyncConnection.connect(database_url(url), autocommit=True)
    try:
        for stmt in SCHEMA:
            await conn.execute(stmt)  # type: ignore[arg-type]
    except BaseException:
        await conn.close()
        raise
    return conn


async def record(job_id: str, outcomes: list[tuple[str, str, bool, int, list[str]]], url: str | None = None) -> int:
    """outcomes: (kind, route, passed, attempts, failed check names)."""
    if not outcomes:
        return 0
    async with await _connect(url) as conn, conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO mesh_route_stats (job_id, kind, route, passed, attempts, failed_checks)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [(job_id, norm_kind(k), r, p, a, Jsonb(f[:20])) for k, r, p, a, f in outcomes])
    return len(outcomes)


async def summary(kinds: list[str], url: str | None = None) -> dict[str, dict[str, tuple[int, int]]]:
    """{kind: {route: (passed, tried)}} over each kind's most recent outcomes."""
    wanted = sorted({norm_kind(k) for k in kinds})
    if not wanted:
        return {}
    async with await _connect(url) as conn:
        cur = await conn.execute(
            "SELECT kind, route, passed FROM ("
            "  SELECT kind, route, passed, row_number() OVER (PARTITION BY kind ORDER BY created_at DESC) AS n"
            "  FROM mesh_route_stats WHERE kind = ANY(%s)) t WHERE n <= %s",
            (wanted, RECENT))
        rows = await cur.fetchall()
    out: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for kind, route, passed in rows:
        out[kind][route][0] += int(passed)
        out[kind][route][1] += 1
    return {k: {r: (v[0], v[1]) for r, v in rs.items()} for k, rs in out.items()}


def render(stats: dict[str, dict[str, tuple[int, int]]]) -> str:
    return "\n".join(f"- {kind}: " + ", ".join(f"{r} {p}/{t}" for r, (p, t) in sorted(rs.items()))
                     for kind, rs in sorted(stats.items()))
