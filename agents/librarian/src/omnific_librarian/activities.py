import asyncio
import logging
import os

from omnific_common.contracts import ACTIVITY, IngestInput, IngestOutput, TaskQueue
from omnific_common.llm import LLM
from omnific_common.storage import store_from_env
from omnific_rag import db
from omnific_rag.models import ChunkIn
from temporalio import activity
from temporalio.exceptions import ApplicationError

from . import chunking, figures, parsers

log = logging.getLogger(__name__)

llm = LLM()


def _heartbeat(stage: str) -> None:
    try:
        activity.heartbeat(stage)
    except RuntimeError:  # not running inside an activity (unit tests)
        pass


def _embed_text(c: chunking.Chunk) -> str:
    return "\n".join(x for x in (c.section, c.context, c.content) if x)


async def _run(inp: IngestInput) -> IngestOutput:
    store = store_from_env()
    raw = await asyncio.to_thread(store.get, inp.raw_key)
    limit_mb = float(os.environ.get("RAG_MAX_UPLOAD_MB", "50"))
    if len(raw) > limit_mb * 1024 * 1024:
        raise parsers.UnsupportedFile(f"file exceeds RAG_MAX_UPLOAD_MB={limit_mb:g}")
    _heartbeat("downloaded")

    parsed = await asyncio.to_thread(parsers.parse, raw, inp.filename, inp.content_type)
    _heartbeat("parsed")
    if not parsed.blocks:
        raise parsers.CorruptFile("no extractable content in the document")

    blocks, warnings = await figures.describe_figures(parsed.blocks, llm, on_progress=_heartbeat)
    _heartbeat("figures")

    chunks = chunking.chunk_blocks(blocks)
    if not chunks:
        raise parsers.CorruptFile("no extractable content in the document")
    title = parsed.title or await chunking.propose_title(inp.filename, chunks, llm)
    _heartbeat("chunked")

    await chunking.add_contexts(chunks, title, llm)
    _heartbeat("contextualised")

    vectors = await llm.embed([_embed_text(c) for c in chunks])
    _heartbeat("embedded")

    await db.replace_chunks(inp.document_id, [
        ChunkIn(ord=c.ord, kind=c.kind, page=c.page, section=c.section,  # type: ignore[arg-type]
                context=c.context, content=c.content, embedding=v)
        for c, v in zip(chunks, vectors, strict=True)
    ])
    n_fig = sum(c.kind == "figure" for c in chunks)
    n_tab = sum(c.kind == "table" for c in chunks)
    await db.set_status(inp.document_id, "ready", title=title, pages=parsed.pages,
                        n_chunks=len(chunks), n_figures=n_fig, n_tables=n_tab)
    return IngestOutput(document_id=inp.document_id, title=title, pages=parsed.pages,
                        n_chunks=len(chunks), n_figures=n_fig, n_tables=n_tab, warnings=warnings)


@activity.defn(name=ACTIVITY[TaskQueue.LIBRARIAN])
async def ingest_document(inp: IngestInput) -> IngestOutput:
    if await db.get_document(inp.document_id) is None:
        await db.create_document(inp.document_id, inp.filename, inp.content_type)
    await db.set_status(inp.document_id, "ingesting")
    try:
        return await _run(inp)
    except (parsers.UnsupportedFile, parsers.CorruptFile) as e:
        await db.set_status(inp.document_id, "failed", error=str(e)[:2000])
        raise ApplicationError(str(e), type=type(e).__name__, non_retryable=True) from e
    except Exception as e:
        # transient (network, rate limit, DB): Temporal retries; the next attempt resets the status
        await db.set_status(inp.document_id, "failed", error=f"{type(e).__name__}: {e}"[:2000])
        raise
