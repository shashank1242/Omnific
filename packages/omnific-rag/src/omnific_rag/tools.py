"""LLM function tools over the user's uploaded documents, shared by the researcher and the clarifier."""

from typing import Any, Literal

from omnific_common.llm import Tool
from pydantic import BaseModel, Field

MAX_CHUNK = 2000


class SearchArgs(BaseModel):
    query: str = Field(description="Natural-language search query; reformulate and repeat as needed")
    kinds: list[Literal["text", "table", "figure"]] | None = Field(
        description="Restrict to these chunk kinds, or null for all")


class NeighborArgs(BaseModel):
    document_id: str
    ord: int = Field(description="Chunk ordinal within the document")


async def rag_search(query: str, document_ids: list[str], kinds: list[str] | None) -> list[Any]:
    from omnific_rag import search  # lazy: keeps the tool layer importable/testable without a DB

    return await search.search(query, document_ids, k=8, kinds=kinds)


async def rag_neighbors(document_id: str, ord: int) -> list[Any]:
    from omnific_rag import search

    return await search.neighbors(document_id, ord, span=1)


def hit_dict(h: Any) -> dict[str, Any]:
    d = h.model_dump() if hasattr(h, "model_dump") else dict(h)
    for k in ("embedding",):
        d.pop(k, None)
    for k, v in list(d.items()):
        if isinstance(v, str) and len(v) > MAX_CHUNK:
            d[k] = v[:MAX_CHUNK] + "..."
    return d


def doc_url(document_id: str, page: int | None) -> str:
    return f"omnific://doc/{document_id}" + (f"#page={page}" if page else "")


def document_tools(document_ids: list[str]) -> list[Tool]:
    if not document_ids:
        return []
    allowed = set(document_ids)

    async def search_documents(args: SearchArgs) -> dict:
        hits = await rag_search(args.query, document_ids, list(args.kinds) if args.kinds else None)
        return {"hits": [hit_dict(h) for h in hits]}

    async def read_chunk_neighbors(args: NeighborArgs) -> dict:
        if args.document_id not in allowed:
            return {"error": "document_id is not one of this job's documents"}
        return {"chunks": [hit_dict(h) for h in await rag_neighbors(args.document_id, args.ord)]}

    return [
        Tool("search_documents",
             "Hybrid search over the user's uploaded documents. Returns chunks with document_id, title, "
             "page, ord, kind and content. Use page numbers when citing.",
             SearchArgs, search_documents),
        Tool("read_chunk_neighbors",
             "Read the chunks surrounding chunk `ord` of a document, for more context.",
             NeighborArgs, read_chunk_neighbors),
    ]
