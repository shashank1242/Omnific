"""Model-library sources behind one interface: search(query, k) -> licence-filtered candidates.

  objaverse  Objaverse 1.0 (allenai, ODC-By index; each object keeps its creator's CC licence). Searched through the
             local pgvector index built by scripts/index_objaverse.py (needs RAG_DATABASE_URL); GLBs download from
             Hugging Face on demand.
  polypizza  Poly Pizza (CC0 / CC-BY low-poly models), live API search (needs POLYPIZZA_API_KEY).
Sketchfab is deliberately absent: its Download API requires every end user to sign in with their own account.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol
from urllib.parse import quote

from omnific_common.contracts import LibraryCandidate

from . import licence
from .fetch import client

log = logging.getLogger(__name__)


class ModelSource(Protocol):
    name: str

    def available(self) -> bool: ...

    async def search(self, query: str, k: int) -> list[LibraryCandidate]: ...


def _allowed(cands: list[LibraryCandidate]) -> list[LibraryCandidate]:
    out = []
    for c in cands:
        lic = licence.normalise(c.license)
        if lic is None:
            log.info("skipping %s/%s: licence %r not allowed", c.source, c.id, c.license)
            continue
        out.append(c.model_copy(update={"license": lic}))
    return out


class Objaverse:
    name = "objaverse"

    def available(self) -> bool:
        return bool(os.environ.get("RAG_DATABASE_URL"))

    async def search(self, query: str, k: int) -> list[LibraryCandidate]:
        from omnific_rag import model_library

        rows = await model_library.search(query, k=k, source=self.name)
        return _allowed([LibraryCandidate(source=self.name, id=r["id"], name=r["name"], license=r["license"],
                                          author=r["author"], url=r["url"], download_url=r["download_url"],
                                          thumb_url=r["thumb_url"], tris=r["tris"], animated=r["animated"])
                         for r in rows])


class PolyPizza:
    name = "polypizza"
    base = "https://api.poly.pizza/v1"

    def available(self) -> bool:
        return bool(os.environ.get("POLYPIZZA_API_KEY"))

    async def search(self, query: str, k: int) -> list[LibraryCandidate]:
        async with client() as http:
            r = await http.get(f"{self.base}/search/{quote(query, safe='')}",  # a "/" must not add path segments
                               params={"limit": min(k, 32), "format": "glb"},
                               headers={"X-Auth-Token": os.environ["POLYPIZZA_API_KEY"]})
            r.raise_for_status()
            results = r.json().get("results") or []
        return _allowed([parse_polypizza(m) for m in results if isinstance(m, dict) and m.get("Download")])


def parse_polypizza(m: dict) -> LibraryCandidate:
    creator = m.get("Creator") or {}
    mid = str(m.get("ID") or m.get("Id") or "")
    return LibraryCandidate(
        source="polypizza", id=mid, name=str(m.get("Title") or mid), license=str(m.get("Licence") or ""),
        author=str(creator.get("Username") or "") if isinstance(creator, dict) else str(creator),
        url=f"https://poly.pizza/m/{mid}", download_url=str(m.get("Download") or ""),
        thumb_url=str(m.get("Thumbnail") or ""), tris=int(m.get("TriangleCount") or m.get("Tri Count") or 0),
        animated=bool(m.get("Animated")))


SOURCES: dict[str, ModelSource] = {s.name: s for s in (Objaverse(), PolyPizza())}


def available_sources(names: list[str]) -> list[str]:
    """The configured sources that can actually run here (known, with their credentials / index)."""
    out = []
    for n in names:
        s = SOURCES.get(n)
        if s is None:
            log.warning("unknown model library %r (known: %s)", n, ", ".join(SOURCES))
        elif s.available():
            out.append(n)
        else:
            log.warning("model library %r is configured but not available (credentials / index missing)", n)
    return out
