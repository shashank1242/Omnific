"""Describe figure blocks with the vision model; each description becomes the text of a figure chunk."""

import asyncio
import logging
import os
from collections.abc import Callable

from omnific_common.llm import LLM, Role

from .parsers import Block

log = logging.getLogger(__name__)

FIGURE_PROMPT = """You are indexing a figure from a technical document so that it can be found and cited later \
by text search. Describe it precisely and factually, in plain text (no preamble).

Cover, in this order, whatever applies:
1. Figure type (line chart, bar chart, scatter plot, schematic/diagram, photo, flow chart, CAD drawing, table image, \
equation, map, ...).
2. Axes: the label, the unit and the range of each axis, and whether the scale is linear or logarithmic.
3. Series/legend entries or the labelled components, and what each represents.
4. Trends and shapes: increasing/decreasing, peaks, crossovers, plateaus, asymptotes, outliers.
5. Specific values read off the figure (point, peak, intercept, final value), with units. Give approximate values \
as approximate ("about 3.2 m/s at t = 4 s").
6. The caption or title and any text printed in the figure, quoted verbatim.

Rules: never invent numbers, labels or units that you cannot see. If something is unreadable or ambiguous, write \
"uncertain" for that item instead of guessing. If the image shows no meaningful figure (a logo, a decorative \
element, a blank area), reply with exactly: NO_CONTENT."""


def max_figures() -> int:
    return int(os.environ.get("RAG_MAX_FIGURES", "40"))


async def describe_figures(
    blocks: list[Block],
    llm: LLM,
    limit: int | None = None,
    on_progress: Callable[[str], None] | None = None,
    concurrency: int = 4,
) -> tuple[list[Block], list[str]]:
    """Replace each figure block's image with a text description. Figures beyond `limit` are
    dropped with a warning. A failed figure is dropped with a warning; if every figure fails the
    last error is raised (so a bad API key is not silently swallowed)."""
    limit = max_figures() if limit is None else limit
    warnings: list[str] = []
    keep: list[Block] = []
    seen = 0
    for bl in blocks:
        if bl.kind == "figure":
            seen += 1
            if seen > limit:
                continue
        keep.append(bl)
    if seen > limit:
        msg = f"{seen - limit} figure(s) skipped: RAG_MAX_FIGURES={limit}"
        log.warning(msg)
        warnings.append(msg)

    sem = asyncio.Semaphore(concurrency)
    errors: list[Exception] = []

    async def one(bl: Block) -> str | None:
        async with sem:
            try:
                assert bl.image is not None and bl.mime is not None
                text = await llm.describe_image(Role.GENERAL, FIGURE_PROMPT, bl.image, bl.mime)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                where = f"page {bl.page}" if bl.page else "document"
                warnings.append(f"figure on {where} could not be described: {type(e).__name__}: {e}")
                return None
            if on_progress:
                on_progress("figure")
            return text

    figs = [bl for bl in keep if bl.kind == "figure"]
    results = await asyncio.gather(*(one(bl) for bl in figs))
    if figs and len(errors) == len(figs):
        raise errors[-1]
    by_id = {id(bl): r for bl, r in zip(figs, results, strict=True)}
    out: list[Block] = []
    for bl in keep:
        if bl.kind != "figure":
            out.append(bl)
            continue
        text = by_id[id(bl)]
        if not text or text.strip() == "NO_CONTENT":
            continue
        out.append(Block("figure", bl.page, bl.section, text.strip()))
    return out, warnings
