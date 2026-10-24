"""Structure-aware chunking and LLM-written contextual headers."""

import logging
import re
from dataclasses import dataclass

from omnific_common.llm import LLM, Role
from pydantic import BaseModel

from .parsers import Block

log = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
MAX_TOKENS = 700
CONTEXT_BATCH = 10


def est_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


@dataclass
class Chunk:
    kind: str  # text | table | figure
    page: int | None
    section: str | None
    content: str
    context: str | None = None
    ord: int = 0


def _split_long(text: str, max_chars: int) -> list[str]:
    """Split an over-long paragraph at sentence boundaries, hard-splitting as a last resort."""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    cur = ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        while len(sent) > max_chars:
            if cur:
                pieces.append(cur)
                cur = ""
            cut = sent.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            pieces.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if cur and len(cur) + 1 + len(sent) > max_chars:
            pieces.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def chunk_blocks(blocks: list[Block], max_tokens: int = MAX_TOKENS) -> list[Chunk]:
    """Split on headings; keep tables and figures whole; pack paragraphs up to `max_tokens` (~700),
    carrying the last paragraph of a full chunk into the next one as overlap."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    chunks: list[Chunk] = []
    buf: list[tuple[str, int | None]] = []  # (paragraph, page)
    buf_section: str | None = None
    fresh = 0  # paragraphs in buf that are new (not overlap)

    def size() -> int:
        return sum(len(p) for p, _ in buf) + 2 * max(0, len(buf) - 1)

    def flush(carry: bool) -> None:
        nonlocal buf, fresh
        if fresh:
            chunks.append(Chunk("text", buf[0][1], buf_section, "\n\n".join(p for p, _ in buf)))
        buf = [buf[-1]] if carry and fresh and len(buf[-1][0]) < max_chars // 3 else []
        fresh = 0

    for bl in blocks:
        if bl.kind == "heading":
            flush(False)
            continue
        text = (bl.text or "").strip()
        if not text:
            continue
        if bl.kind in ("table", "figure"):
            flush(False)
            chunks.append(Chunk(bl.kind, bl.page, bl.section, text))
            continue
        if bl.section != buf_section:
            flush(False)
            buf_section = bl.section
        for piece in _split_long(text, max_chars):
            if buf and fresh and size() + 2 + len(piece) > max_chars:
                flush(True)
                if buf and size() + 2 + len(piece) > max_chars:
                    buf = []  # overlap would overflow
            buf.append((piece, bl.page))
            fresh += 1
    flush(False)
    for i, c in enumerate(chunks):
        c.ord = i
    return chunks


# --- contextual headers -----------------------------------------------------------------------

class Contexts(BaseModel):
    contexts: list[str]


class TitleProposal(BaseModel):
    title: str


CONTEXT_INSTRUCTIONS = """You write retrieval context for chunks of one document. For each numbered chunk, \
write 1-2 sentences that situate it in the document, for example "From section 3.2 of <title>, discussing how the \
hull's drag coefficient was measured." State what the chunk is about and where it sits (section/page), using only \
the chunk and the outline. Do not repeat the chunk's content and do not invent facts. \
Return exactly one context per chunk, in the same order as the chunks."""

TITLE_INSTRUCTIONS = (
    "Propose a short, descriptive title (under 100 characters) for this document, given its file name and opening "
    "text. Reply with the title only; do not invent details."
)


def _outline(chunks: list[Chunk], limit: int = 60) -> str:
    seen: list[str] = []
    for c in chunks:
        if c.section and c.section not in seen:
            seen.append(c.section)
    return "\n".join(f"- {s[:100]}" for s in seen[:limit]) or "(no headings)"


def fallback_context(chunk: Chunk, title: str) -> str:
    where = f"section '{chunk.section}' of " if chunk.section else ""
    page = f" (page {chunk.page})" if chunk.page else ""
    return f"From {where}{title}{page}."


async def add_contexts(chunks: list[Chunk], title: str, llm: LLM, batch: int = CONTEXT_BATCH) -> None:
    """Fill `context` on every chunk, `batch` chunks per FAST-model call."""
    outline = _outline(chunks)
    for start in range(0, len(chunks), batch):
        group = chunks[start:start + batch]
        parts = [f"Document title: {title}\n\nOutline:\n{outline}\n"]
        for i, c in enumerate(group, start=1):
            head = f"[{i}] kind={c.kind} section={c.section or '-'} page={c.page or '-'}"
            parts.append(f"{head}\n{c.content[:1200]}")
        result = await llm.structured(Role.FAST, CONTEXT_INSTRUCTIONS, "\n\n".join(parts), Contexts)
        got = [x.strip() for x in result.contexts]
        if len(got) != len(group):
            log.warning("context count mismatch: wanted %d got %d", len(group), len(got))
        for i, c in enumerate(group):
            c.context = got[i] if i < len(got) and got[i] else fallback_context(c, title)


async def propose_title(filename: str, chunks: list[Chunk], llm: LLM) -> str:
    opening = "\n\n".join(c.content for c in chunks[:3])[:3000]
    try:
        t = await llm.structured(
            Role.FAST, TITLE_INSTRUCTIONS, f"File name: {filename}\n\nOpening text:\n{opening}", TitleProposal
        )
        title = " ".join(t.title.split())[:200]
    except Exception as e:  # noqa: BLE001 - the title is cosmetic; the filename is a fine fallback
        log.warning("title proposal failed: %s", e)
        title = ""
    return title or filename
