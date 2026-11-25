"""Model-library sources behind one interface: search(query, k) -> licence-filtered candidates.

  objaverse  Objaverse 1.0 (allenai, ODC-By index; each object keeps its creator's CC licence). Searched through the
             local pgvector index built by scripts/index_objaverse.py (needs RAG_DATABASE_URL); GLBs download from
             Hugging Face on demand.
  polypizza  Poly Pizza (CC0 / CC-BY low-poly models), live API search (needs POLYPIZZA_API_KEY).
  sketchfab  Sketchfab (the largest CC catalogue), live search; downloads use ONE personal API token
             (SKETCHFAB_API_TOKEN), so this is for a single-user deployment. A public deployment needs per-user
             sign-in instead (future.md). The token is sent only to api.sketchfab.com, never to a download host.
  polyhaven  Poly Haven (CC0, curated), no key; the catalogue is searched locally. Its models are multi-file glTF,
             packed into one GLB (pack.py) with every file through the safe downloader.

A source either gives a direct GLB `download_url` in its search results, or implements `fetch_glb(candidate)`.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Protocol
from urllib.parse import quote

from omnific_common.contracts import LibraryCandidate

from . import fetch as _fetch  # looked up at call time (tests patch fetch.download)
from . import licence
from .fetch import UnsafeDownload, client, max_bytes
from .pack import fetch_and_pack

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


# --- Sketchfab -------------------------------------------------------------------------------------------------

SKETCHFAB_API = "https://api.sketchfab.com/v3"
# Sketchfab licences -> our normalised labels, by label (what search returns: {"uid", "label"}) or slug. Anything
# else (ShareAlike, NoDerivs, NonCommercial, Standard, Editorial) is rejected.
SKETCHFAB_LICENCES = {"cc attribution": "CC-BY 4.0", "cc0 public domain": "CC0 1.0", "by": "CC-BY 4.0",
                      "cc0": "CC0 1.0"}


def _sketchfab_thumb(m: dict, want: int = 256) -> str:
    imgs = [i for i in ((m.get("thumbnails") or {}).get("images") or []) if isinstance(i, dict) and i.get("url")]
    if not imgs:
        return ""
    return min(imgs, key=lambda i: abs(int(i.get("width") or 0) - want))["url"]


def parse_sketchfab(m: dict) -> LibraryCandidate | None:
    lic = m.get("license")
    keys = [lic.get("label"), lic.get("slug")] if isinstance(lic, dict) else [lic]
    label = next((SKETCHFAB_LICENCES[k] for k in (str(x or "").strip().lower() for x in keys)
                  if k in SKETCHFAB_LICENCES), None)
    uid = str(m.get("uid") or "")
    if not uid or label is None:
        return None
    glb = ((m.get("archives") or {}).get("glb") or {})
    if glb.get("size") and int(glb["size"]) > max_bytes():
        return None  # too big to download: don't offer it
    user = m.get("user") or {}
    return LibraryCandidate(
        source="sketchfab", id=uid, name=str(m.get("name") or uid), license=label,
        author=str(user.get("displayName") or user.get("username") or ""),
        url=str(m.get("viewerUrl") or f"https://sketchfab.com/3d-models/{uid}"),
        thumb_url=_sketchfab_thumb(m), tris=int(glb.get("faceCount") or m.get("faceCount") or 0),
        animated=bool(m.get("animationCount")))


class Sketchfab:
    name = "sketchfab"

    def available(self) -> bool:
        return bool(os.environ.get("SKETCHFAB_API_TOKEN", "").strip())

    async def search(self, query: str, k: int) -> list[LibraryCandidate]:
        # search is public: the token is not sent here
        async with client() as http:
            r = await http.get(f"{SKETCHFAB_API}/search", params={
                "type": "models", "q": query, "downloadable": "true", "count": min(max(k, 1) * 2, 24),
                "archives_flavours": "false"})
            r.raise_for_status()
            results = r.json().get("results") or []
        return _allowed([c for c in (parse_sketchfab(m) for m in results if isinstance(m, dict)) if c])[:k]

    async def download_url(self, c: LibraryCandidate) -> str:
        """The model's short-lived GLB link. The personal token goes only to api.sketchfab.com, with redirects off,
        so it can never reach the storage host the link points at."""
        if not re.fullmatch(r"[0-9a-f]{32}", c.id):
            raise UnsafeDownload(f"not a Sketchfab model id: {c.id!r}")
        token = os.environ.get("SKETCHFAB_API_TOKEN", "").strip()
        if not token:
            raise UnsafeDownload("SKETCHFAB_API_TOKEN is not set")
        async with client() as http:
            r = await http.get(f"{SKETCHFAB_API}/models/{c.id}/download",
                               headers={"Authorization": f"Token {token}"}, follow_redirects=False)
            if r.status_code in (401, 403):
                raise UnsafeDownload(f"Sketchfab refused the download (HTTP {r.status_code}): check the token")
            r.raise_for_status()
            glb = (r.json() or {}).get("glb") or {}
        if not glb.get("url"):
            raise UnsafeDownload("Sketchfab offers no GLB for this model")
        if glb.get("size") and int(glb["size"]) > max_bytes():
            raise UnsafeDownload(f"{int(glb['size']) / 1e6:.1f} MB exceeds the {max_bytes() / 1e6:.0f} MB limit")
        return str(glb["url"])

    async def fetch_glb(self, c: LibraryCandidate) -> bytes:
        return await _fetch.download(await self.download_url(c))


# --- Poly Haven ------------------------------------------------------------------------------------------------

POLYHAVEN_API = "https://api.polyhaven.com"
CATALOGUE_TTL_S = 6 * 3600
_catalogue: dict = {"at": 0.0, "assets": {}}


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 1}


def polyhaven_rank(assets: dict, query: str, k: int) -> list[LibraryCandidate]:
    """Local search: query words matched against each model's id, name, tags and categories."""
    q = _words(query)
    if not q:
        return []
    scored = []
    for aid, a in assets.items():
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or aid)
        hay_name = _words(f"{aid} {name}")
        hay_tags = _words(" ".join(map(str, (a.get("tags") or []) + (a.get("categories") or []))))
        score = 3 * len(q & hay_name) + len(q & hay_tags)
        if score:
            scored.append((score, int(a.get("download_count") or 0), aid, name, a))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    out = []
    for _, _, aid, name, a in scored[:k]:
        authors = ", ".join((a.get("authors") or {}).keys())
        out.append(LibraryCandidate(
            source="polyhaven", id=aid, name=name, license="CC0 1.0", author=authors,
            url=f"https://polyhaven.com/a/{aid}",
            thumb_url=str(a.get("thumbnail_url") or f"https://cdn.polyhaven.com/asset_img/thumbs/{aid}.png?width=256"),
            tris=int(a.get("polycount") or 0), animated=False))
    return out


class PolyHaven:
    name = "polyhaven"

    def available(self) -> bool:
        return True  # public API, no key

    async def catalogue(self) -> dict:
        if time.monotonic() - _catalogue["at"] > CATALOGUE_TTL_S or not _catalogue["assets"]:
            async with client() as http:
                r = await http.get(f"{POLYHAVEN_API}/assets", params={"t": "models"})
                r.raise_for_status()
                _catalogue.update(at=time.monotonic(), assets=r.json() or {})
        return _catalogue["assets"]

    async def search(self, query: str, k: int) -> list[LibraryCandidate]:
        return polyhaven_rank(await self.catalogue(), query, k)

    async def fetch_glb(self, c: LibraryCandidate) -> bytes:
        if not re.fullmatch(r"[a-z0-9_]+", c.id):
            raise UnsafeDownload(f"not a Poly Haven asset id: {c.id!r}")
        async with client() as http:
            r = await http.get(f"{POLYHAVEN_API}/files/{c.id}")
            r.raise_for_status()
            files = r.json() or {}
        res = files.get("gltf") or {}
        for size in ("1k", "2k"):  # the smallest textures: the viewer caps them at 1024 anyway
            entry = (res.get(size) or {}).get("gltf") or {}
            if entry.get("url"):
                includes = {rel: inc["url"] for rel, inc in (entry.get("include") or {}).items()
                            if isinstance(inc, dict) and inc.get("url")}
                return await fetch_and_pack(entry["url"], includes, _fetch.download)
        raise UnsafeDownload("Poly Haven offers no glTF for this asset")


SOURCES: dict[str, ModelSource] = {s.name: s for s in (Objaverse(), PolyPizza(), Sketchfab(), PolyHaven())}


async def fetch_candidate(c: LibraryCandidate) -> bytes:
    """GLB bytes for a candidate: the source's own fetch (resolved links, packed glTF) or its direct download URL."""
    src = SOURCES.get(c.source)
    if src is not None and hasattr(src, "fetch_glb"):
        return await src.fetch_glb(c)
    if not c.download_url:
        raise UnsafeDownload("the source gave no download URL")
    return await _fetch.download(c.download_url)


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
