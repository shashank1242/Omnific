"""Hybrid retrieval: vector + full-text, fused with reciprocal-rank fusion, scoped to given documents."""

from collections.abc import Sequence

from omnific_common.llm import LLM
from pgvector import Vector

from . import db
from .models import Hit, citation_url

RRF_K = 60
CANDIDATES = 40

llm = LLM()


def rrf_fuse(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal-rank fusion. Each ranking is ids best-first; score = sum 1/(k + rank), rank from 1.
    Returns (id, score) best-first; ties keep first-appearance order (stable sort)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, id_ in enumerate(ranking, start=1):
            scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1])


_VEC_SQL = (
    "SELECT id FROM chunks WHERE document_id = ANY(%s) AND embedding IS NOT NULL"
    "{kinds} ORDER BY embedding <=> %s LIMIT %s"
)
# websearch_to_tsquery ANDs every term, so long natural-language agent queries would rarely match. OR the lexemes
# instead; ts_rank_cd still ranks chunks matching more terms higher.
_FTS_SQL = (
    "SELECT id FROM chunks, to_tsquery('english', replace(websearch_to_tsquery('english', %s)::text, ' & ', ' | ')) q"
    " WHERE document_id = ANY(%s)"
    " AND tsv @@ q{kinds} ORDER BY ts_rank_cd(tsv, q) DESC, id LIMIT %s"
)
_HIT_COLS = (
    "SELECT c.id, c.document_id, d.title, c.page, c.kind, c.section, c.content, c.ord"
    " FROM chunks c JOIN documents d ON d.id = c.document_id"
)


async def search(
    query: str, document_ids: Sequence[str], k: int = 8, kinds: Sequence[str] | None = None
) -> list[Hit]:
    """Hybrid search restricted to `document_ids`. Empty scope returns [] (no global search)."""
    if not document_ids or k <= 0 or not query.strip():
        return []
    ids = list(document_ids)
    kind_list = list(kinds) if kinds else None
    kind_sql = " AND kind = ANY(%s)" if kind_list else ""
    extra: list = [kind_list] if kind_list else []
    qvec = Vector((await llm.embed([query]))[0])
    p = await db.pool()
    async with p.connection() as conn:
        cur = await conn.execute(
            _VEC_SQL.format(kinds=kind_sql), [ids, *extra, qvec, CANDIDATES]  # type: ignore[arg-type]
        )
        vec_ids = [r["id"] for r in await cur.fetchall()]
        cur = await conn.execute(
            _FTS_SQL.format(kinds=kind_sql), [query, ids, *extra, CANDIDATES]  # type: ignore[arg-type]
        )
        fts_ids = [r["id"] for r in await cur.fetchall()]
        fused = rrf_fuse([vec_ids, fts_ids])[:k]
        if not fused:
            return []
        cur = await conn.execute(_HIT_COLS + " WHERE c.id = ANY(%s)", ([i for i, _ in fused],))
        rows = {r["id"]: r for r in await cur.fetchall()}
    return [_hit(rows[i], score) for i, score in fused if i in rows]


async def neighbors(document_id: str, ord: int, span: int = 1) -> list[Hit]:
    """Chunks ord-span .. ord+span of one document, in order (including the centre chunk)."""
    span = max(0, span)
    p = await db.pool()
    async with p.connection() as conn:
        cur = await conn.execute(
            _HIT_COLS + " WHERE c.document_id = %s AND c.ord BETWEEN %s AND %s ORDER BY c.ord",
            (document_id, ord - span, ord + span),
        )
        return [_hit(r, 0.0) for r in await cur.fetchall()]


def _hit(row: dict, score: float) -> Hit:
    return Hit(
        document_id=row["document_id"], title=row["title"], page=row["page"], kind=row["kind"],
        section=row["section"], content=row["content"], score=score, ord=row["ord"],
        citation_url=citation_url(row["document_id"], row["page"]),
    )
