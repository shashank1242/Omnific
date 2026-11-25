"""Library route: reuse an existing, openly licensed 3D model instead of making one.

search (every enabled source, licence-filtered) → shortlist (vision pick on numbered thumbnails) → fetch one
candidate (safe download, GLB only) → the usual evaluate / postprocess / rig stages. Attribution travels with the
asset into the scene file and the report.
"""

from __future__ import annotations

import asyncio
import io
import logging

from omnific_common.contracts import AssetPlan, LibraryCandidate, LibraryShortlist
from omnific_common.llm import LLM, Role

from .fetch import UnsafeDownload, check_glb, download
from .licence import normalise as normalise_licence
from .sources import SOURCES, available_sources, fetch_candidate

__all__ = ["SOURCES", "UnsafeDownload", "available_sources", "check_glb", "download", "fetch_candidate",
           "normalise_licence",
           "query_variants", "rank", "search_all", "shortlist"]

log = logging.getLogger(__name__)

MAX_CANDIDATES = 12
THUMB_BYTES = 2 * 1024 * 1024
CELL = 256

SHORTLIST_INSTRUCTIONS = """\
You pick 3D models for a physics-simulation viewer from a numbered grid of library thumbnails. Choose only
thumbnails that clearly show ONE object matching the request (not a scene, room, collection, character sheet or
unrelated object). Prefer clean, complete, single objects in a neutral pose. Return their numbers best first
(at most 6) with a 0-10 score each (10 = exactly the requested object, clean and complete); return none if nothing
matches."""


def query_variants(plan: AssetPlan) -> list[str]:
    out: list[str] = []
    for q in (plan.search_query, plan.kind):
        q = " ".join(q.split())
        if q and q.lower() not in (x.lower() for x in out):
            out.append(q)
    return out


async def search_all(sources: list[str], query: str, k: int = MAX_CANDIDATES) -> tuple[list[LibraryCandidate],
                                                                                     list[str]]:
    """Merged, de-duplicated candidates (round-robin across sources) and per-source error notes."""
    names = [n for n in sources if n in SOURCES]
    results = await asyncio.gather(*(SOURCES[n].search(query, k) for n in names), return_exceptions=True)
    lists, notes = [], []
    for n, r in zip(names, results, strict=True):
        if isinstance(r, BaseException):
            notes.append(f"{n}: search failed ({type(r).__name__}: {r})")
        else:
            lists.append(r)
    merged: list[LibraryCandidate] = []
    seen: set[tuple[str, str]] = set()
    for i in range(max((len(x) for x in lists), default=0)):
        for lst in lists:
            if i < len(lst) and (lst[i].source, lst[i].id) not in seen:
                seen.add((lst[i].source, lst[i].id))
                merged.append(lst[i])
    return merged[:k], notes


async def _thumb(c: LibraryCandidate) -> bytes | None:
    if not c.thumb_url:
        return None
    try:
        return await download(c.thumb_url, THUMB_BYTES, timeout=20)
    except Exception as e:  # noqa: BLE001 - a missing preview only removes the candidate from the grid
        log.info("no thumbnail for %s/%s: %s", c.source, c.id, e)
        return None


def contact_grid(images: list[bytes]) -> bytes:
    from PIL import Image, ImageDraw

    cols = min(4, len(images))
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * CELL, rows * CELL), "white")
    draw = ImageDraw.Draw(sheet)
    for i, data in enumerate(images):
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((CELL - 8, CELL - 8))
        x, y = (i % cols) * CELL, (i // cols) * CELL
        sheet.paste(img, (x + (CELL - img.width) // 2, y + (CELL - img.height) // 2))
        draw.rectangle((x, y, x + 34, y + 26), fill="black")
        draw.text((x + 6, y + 6), str(i + 1), fill="white")
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


async def rank(llm: LLM, plan: AssetPlan, cands: list[LibraryCandidate], n: int = 3,
               ) -> tuple[list[tuple[LibraryCandidate, int, bytes | None]], str]:
    """The best `n` candidates by a vision pick on their thumbnails, each with its score (0-10) and thumbnail bytes,
    and a note. Fails open (source order, score 0) when there are no thumbnails or the vision call errors; returns []
    only when the critic saw them and rejected all."""
    thumbs = await asyncio.gather(*(_thumb(c) for c in cands))
    shown = [(c, t) for c, t in zip(cands, thumbs, strict=True) if t is not None]
    if not shown:
        return [(c, 0, None) for c in cands[:n]], "no thumbnails: kept source order"
    try:
        grid = await asyncio.to_thread(contact_grid, [t for _, t in shown])
        text = f"Request: {plan.kind}: {plan.gen_prompt or plan.search_query}\n{len(shown)} numbered thumbnails."
        pick = await llm.structured_images(Role.GENERAL, SHORTLIST_INSTRUCTIONS, text, [(grid, "image/png")],
                                           LibraryShortlist)
    except Exception as e:  # noqa: BLE001
        log.warning("thumbnail shortlist unavailable: %s", e)
        note = f"shortlist critic unavailable ({type(e).__name__}): kept source order"
        return [(c, 0, t) for c, t in shown][:n], note
    picked, seen = [], set()
    for k, i in enumerate(pick.picks):
        if 1 <= i <= len(shown) and i not in seen:
            seen.add(i)
            score = pick.scores[k] if k < len(pick.scores) else 0
            picked.append((shown[i - 1][0], max(0, min(10, int(score))), shown[i - 1][1]))
    return picked[:n], pick.reason


async def shortlist(llm: LLM, plan: AssetPlan, cands: list[LibraryCandidate],
                    n: int = 3) -> tuple[list[LibraryCandidate], str]:
    """The best `n` candidates by a vision pick on their thumbnails, and a note (see `rank`)."""
    ranked, reason = await rank(llm, plan, cands, n)
    return [c for c, _, _ in ranked], reason
