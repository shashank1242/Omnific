"""Local search index over open 3D-model libraries (Objaverse metadata), for the mesh router's library route.

Rows hold metadata only (name, category, tags, licence, author, URLs); models are downloaded on demand. Only
models whose licence passed the indexer's filter (CC0 / CC-BY) are stored. Hybrid search like the document library:
vector + full-text, fused with reciprocal-rank fusion. Short-lived connections: a few searches per job.
"""

from __future__ import annotations

from typing import Any

import psycopg
from omnific_common.llm import LLM
from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row

from .db import EMBEDDING_DIM, database_url
from .search import rrf_fuse

SCHEMA = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    f"""CREATE TABLE IF NOT EXISTS library_models (
        source text NOT NULL,
        id text NOT NULL,
        name text NOT NULL,
        category text NOT NULL DEFAULT '',
        tags text NOT NULL DEFAULT '',
        license text NOT NULL,
        author text NOT NULL DEFAULT '',
        url text NOT NULL,
        download_url text NOT NULL,
        thumb_url text NOT NULL DEFAULT '',
        tris int NOT NULL DEFAULT 0,
        animated boolean NOT NULL DEFAULT false,
        embedding vector({EMBEDDING_DIM}),
        tsv tsvector GENERATED ALWAYS AS (
            to_tsvector('english', name || ' ' || category || ' ' || category || ' ' || tags)
        ) STORED,
        PRIMARY KEY (source, id)
    )""",
    "CREATE INDEX IF NOT EXISTS library_models_embedding ON library_models USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX IF NOT EXISTS library_models_tsv ON library_models USING gin (tsv)",
]
COLUMNS = ("source", "id", "name", "category", "tags", "license", "author", "url", "download_url", "thumb_url",
           "tris", "animated")
CANDIDATES = 40

llm = LLM()


def index_text(row: dict[str, Any]) -> str:
    """What gets embedded for a model."""
    return f"{row['name']}. {row.get('category', '')}. {row.get('tags', '')}".strip()


async def _connect(url: str | None) -> psycopg.AsyncConnection:
    conn = await psycopg.AsyncConnection.connect(database_url(url), autocommit=True, row_factory=dict_row)
    try:
        for stmt in SCHEMA:
            await conn.execute(stmt)  # type: ignore[arg-type]
        await register_vector_async(conn)
    except BaseException:
        await conn.close()
        raise
    return conn


DEFAULTS = {"tris": 0, "animated": False}


async def upsert(rows: list[dict[str, Any]], embeddings: list[list[float]] | None = None,
                 url: str | None = None) -> int:
    if not rows:
        return 0
    embeddings = embeddings or [None] * len(rows)  # type: ignore[list-item]
    cols = ", ".join(COLUMNS)
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS[2:])
    async with await _connect(url) as conn, conn.cursor() as cur:
        await cur.executemany(
            f"INSERT INTO library_models ({cols}, embedding) VALUES ({', '.join(['%s'] * (len(COLUMNS) + 1))})"
            # a metadata-only upsert (no new vector) keeps the stored embedding
            f" ON CONFLICT (source, id) DO UPDATE SET {sets},"
            " embedding = COALESCE(EXCLUDED.embedding, library_models.embedding)",
            [(*[r.get(c, DEFAULTS.get(c, "")) for c in COLUMNS],
              Vector(e) if e is not None else None) for r, e in zip(rows, embeddings, strict=True)])
    return len(rows)


async def count(source: str, url: str | None = None) -> int:
    async with await _connect(url) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM library_models WHERE source = %s", (source,))
        return int((await cur.fetchone())["n"])  # type: ignore[index]


async def search(query: str, k: int = 12, source: str = "objaverse", url: str | None = None,
                 embed: bool = True) -> list[dict[str, Any]]:
    """Best-first rows for `query` from one source. `embed=False` skips the vector half (no OpenAI call)."""
    if not query.strip() or k <= 0:
        return []
    async with await _connect(url) as conn:
        rankings = []
        if embed:
            qvec = Vector((await llm.embed([query]))[0])
            cur = await conn.execute(
                "SELECT id FROM library_models WHERE source = %s AND embedding IS NOT NULL"
                " ORDER BY embedding <=> %s LIMIT %s", (source, qvec, CANDIDATES))
            rankings.append([r["id"] for r in await cur.fetchall()])
        cur = await conn.execute(
            "SELECT id FROM library_models,"
            " to_tsquery('english', replace(websearch_to_tsquery('english', %s)::text, ' & ', ' | ')) q"
            " WHERE source = %s AND tsv @@ q ORDER BY ts_rank_cd(tsv, q) DESC, id LIMIT %s",
            (query, source, CANDIDATES))
        rankings.append([r["id"] for r in await cur.fetchall()])
        fused = [i for i, _ in rrf_fuse(rankings)][:k]
        if not fused:
            return []
        cur = await conn.execute(f"SELECT {', '.join(COLUMNS)} FROM library_models WHERE source = %s AND id = ANY(%s)",
                                 (source, fused))
        rows = {r["id"]: r for r in await cur.fetchall()}
    return [rows[i] for i in fused if i in rows]
