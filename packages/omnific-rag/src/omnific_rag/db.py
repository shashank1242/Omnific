"""Async Postgres/pgvector access. All SQL is parameterised; column names come from fixed whitelists."""

import asyncio
import os
from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .models import ChunkIn, Document

EMBEDDING_DIM = 1536
_COUNT_FIELDS = ("title", "pages", "n_chunks", "n_figures", "n_tables")
_STATUSES = ("pending", "ingesting", "ready", "failed")

SCHEMA = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """CREATE TABLE IF NOT EXISTS documents (
        id text PRIMARY KEY,
        filename text NOT NULL,
        content_type text NOT NULL,
        title text,
        pages int,
        status text NOT NULL DEFAULT 'pending',
        error text,
        n_chunks int NOT NULL DEFAULT 0,
        n_figures int NOT NULL DEFAULT 0,
        n_tables int NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        meta jsonb NOT NULL DEFAULT '{}'::jsonb
    )""",
    f"""CREATE TABLE IF NOT EXISTS chunks (
        id bigserial PRIMARY KEY,
        document_id text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        ord int NOT NULL,
        kind text NOT NULL DEFAULT 'text',
        page int,
        section text,
        context text,
        content text NOT NULL,
        embedding vector({EMBEDDING_DIM}),
        tsv tsvector GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(section,'') || ' ' || coalesce(context,'') || ' ' || content)
        ) STORED
    )""",
    "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv)",
    "CREATE INDEX IF NOT EXISTS chunks_document_id ON chunks (document_id)",
]

_pool: AsyncConnectionPool | None = None
_pool_lock: asyncio.Lock | None = None


def database_url(url: str | None = None) -> str:
    url = url or os.environ.get("RAG_DATABASE_URL")
    if not url:
        raise RuntimeError("RAG_DATABASE_URL is not set")
    return url


async def _configure(conn: psycopg.AsyncConnection) -> None:
    try:
        await register_vector_async(conn)
    except psycopg.ProgrammingError:
        # extension not created yet (before migrate()); migrate() resets the pool afterwards
        await conn.rollback()


async def pool(url: str | None = None) -> AsyncConnectionPool:
    """The shared per-process pool (created on first use)."""
    global _pool, _pool_lock
    if _pool is not None:
        return _pool
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    async with _pool_lock:
        if _pool is None:
            p = AsyncConnectionPool(
                database_url(url), min_size=1, max_size=int(os.environ.get("RAG_POOL_MAX", "10")),
                configure=_configure, kwargs={"row_factory": dict_row}, open=False,
            )
            await p.open(wait=True)
            _pool = p
    return _pool


async def close_pool() -> None:
    global _pool, _pool_lock
    p, _pool, _pool_lock = _pool, None, None
    if p is not None:
        await p.close()


async def migrate(url: str | None = None) -> None:
    """Idempotently create the extension, tables and indexes."""
    await close_pool()
    async with await psycopg.AsyncConnection.connect(database_url(url), autocommit=True) as conn:
        # serialise concurrent migrations (several services call this at startup)
        await conn.execute("SELECT pg_advisory_lock(727274)")
        try:
            for stmt in SCHEMA:
                await conn.execute(stmt)  # type: ignore[arg-type]
        finally:
            await conn.execute("SELECT pg_advisory_unlock(727274)")


def _doc(row: dict[str, Any]) -> Document:
    return Document(**row)


async def create_document(
    id: str, filename: str, content_type: str, meta: dict[str, Any] | None = None
) -> Document:
    p = await pool()
    async with p.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO documents (id, filename, content_type, meta) VALUES (%s, %s, %s, %s) "
            "RETURNING *",
            (id, filename, content_type, Jsonb(meta or {})),
        )
        return _doc(await cur.fetchone())  # type: ignore[arg-type]


async def get_document(id: str) -> Document | None:
    p = await pool()
    async with p.connection() as conn:
        cur = await conn.execute("SELECT * FROM documents WHERE id = %s", (id,))
        row = await cur.fetchone()
    return _doc(row) if row else None


async def list_documents(limit: int = 100) -> list[Document]:
    p = await pool()
    async with p.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM documents ORDER BY created_at DESC, id LIMIT %s", (max(1, limit),)
        )
        return [_doc(r) for r in await cur.fetchall()]


async def delete_document(id: str) -> bool:
    p = await pool()
    async with p.connection() as conn:
        cur = await conn.execute("DELETE FROM documents WHERE id = %s", (id,))
        return cur.rowcount > 0


async def set_status(id: str, status: str, error: str | None = None, **counts: Any) -> None:
    if status not in _STATUSES:
        raise ValueError(f"invalid status {status!r}")
    unknown = set(counts) - set(_COUNT_FIELDS)
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    sets = ["status = %s", "error = %s"]
    params: list[Any] = [status, error]
    for name in _COUNT_FIELDS:  # fixed whitelist order; names never come from the caller
        if name in counts:
            sets.append(f"{name} = %s")
            params.append(counts[name])
    params.append(id)
    p = await pool()
    async with p.connection() as conn:
        await conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id = %s", params)


async def replace_chunks(document_id: str, chunks: list[ChunkIn]) -> int:
    """Delete the document's chunks and insert the new ones in a single transaction."""
    for c in chunks:
        if len(c.embedding) != EMBEDDING_DIM:
            raise ValueError(f"embedding has {len(c.embedding)} dims, expected {EMBEDDING_DIM}")
    p = await pool()
    async with p.connection() as conn, conn.transaction():
        await conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
        if chunks:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO chunks (document_id, ord, kind, page, section, context, content,"
                    " embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [(document_id, c.ord, c.kind, c.page, c.section, c.context, c.content,
                      Vector(c.embedding)) for c in chunks],
                )
    return len(chunks)
