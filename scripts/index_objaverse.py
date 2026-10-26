"""Build the Objaverse search index used by the mesh router's library route (source "objaverse").

Indexes the LVIS-annotated subset of Objaverse 1.0 (~46k objects in ~1.1k categories, allenai, ODC-By): only
downloadable, non-age-restricted objects whose creator licence is CC0 or CC-BY (NC/ND/SA are skipped). Stores
metadata (name, LVIS category, tags, licence, author, model page, Hugging Face GLB URL, thumbnail) in pgvector;
GLBs are downloaded later, on demand, per job.

  uv run --no-sync python scripts/index_objaverse.py                  # all LVIS categories (embeds: OpenAI cost)
  uv run --no-sync python scripts/index_objaverse.py --categories car,dog,chair --no-embed
  uv run --no-sync python scripts/index_objaverse.py --dry-run --categories car   # print what would be indexed

Needs RAG_DATABASE_URL (.env; `docker compose up -d postgres`). Downloads ~20 MB of index files plus the metadata
shards that hold the chosen objects (~3.6 MB each, 160 in total), cached under ~/.cache/omnific/objaverse.
Embedding ~46k short texts with text-embedding-3-small costs a few cents; --no-embed keeps full-text search only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import httpx
from omnific_mesh.library.licence import normalise

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from e2e_local import load_env

HF = "https://huggingface.co/datasets/allenai/objaverse/resolve/main"
CACHE = Path.home() / ".cache" / "omnific" / "objaverse"  # OBJAVERSE_CACHE (shell or .env) overrides, see __main__
MAX_FACES = 500_000
BATCH = 256


def cached(rel: str) -> Path:
    path = CACHE / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {rel} ...", flush=True)
        with httpx.stream("GET", f"{HF}/{rel}", follow_redirects=True, timeout=300) as r:
            r.raise_for_status()
            tmp = path.with_suffix(path.suffix + ".part")
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
            tmp.replace(path)
    return path


def load_gz(rel: str) -> dict:
    with gzip.open(cached(rel), "rt", encoding="utf-8") as f:
        return json.load(f)


def thumbnail(meta: dict) -> str:
    images = sorted((meta.get("thumbnails") or {}).get("images") or [], key=lambda i: i.get("width", 0))
    for img in images:  # smallest at least 256 px wide keeps the shortlist grid cheap
        if img.get("width", 0) >= 256:
            return img.get("url", "")
    return images[-1].get("url", "") if images else ""


def row(uid: str, meta: dict, category: str, path: str) -> dict | None:
    lic = normalise(meta.get("license"))
    if lic is None or not meta.get("isDownloadable", True) or meta.get("isAgeRestricted"):
        return None
    if (meta.get("faceCount") or 0) > MAX_FACES:
        return None
    user = meta.get("user") or {}
    return {"source": "objaverse", "id": uid, "name": (meta.get("name") or category)[:200],
            "category": category.replace("_", " "),
            "tags": " ".join(t.get("name", "") for t in meta.get("tags") or [])[:500],
            "license": lic, "author": user.get("displayName") or user.get("username") or "",
            "url": meta.get("viewerUrl") or f"https://sketchfab.com/3d-models/{uid}",
            "download_url": f"{HF}/{path}", "thumb_url": thumbnail(meta),
            "tris": int(meta.get("faceCount") or 0), "animated": bool(meta.get("animationCount"))}


async def main(args) -> None:
    from omnific_common.llm import LLM
    from omnific_rag import model_library
    from omnific_rag.model_library import index_text

    lvis: dict[str, list[str]] = load_gz("lvis-annotations.json.gz")
    if args.categories:
        wanted = {c.strip().lower().replace(" ", "_") for c in args.categories.split(",")}
        missing = wanted - set(lvis)
        if missing:
            near = [c for c in lvis if any(m.split("_")[0] in c for m in missing)][:20]
            print(f"unknown LVIS categories {sorted(missing)}; similar: {near}")
        lvis = {c: u for c, u in lvis.items() if c in wanted}
    category = {uid: c for c, uids in lvis.items() for uid in uids[: args.per_category or None]}
    paths: dict[str, str] = load_gz("object-paths.json.gz")
    by_shard: dict[str, list[str]] = defaultdict(list)
    for uid in category:
        if uid in paths:
            by_shard[paths[uid].split("/")[1]].append(uid)  # glbs/<shard>/<uid>.glb
    print(f"{len(category)} objects in {len(lvis)} categories across {len(by_shard)} metadata shards")

    rows: list[dict] = []
    skipped = 0
    for i, (shard, uids) in enumerate(sorted(by_shard.items()), 1):
        meta = load_gz(f"metadata/{shard}.json.gz")
        for uid in uids:
            r = row(uid, meta.get(uid) or {}, category[uid], paths[uid])
            if r is None:
                skipped += 1
            else:
                rows.append(r)
        print(f"  shard {i}/{len(by_shard)}: {len(rows)} kept, {skipped} skipped (licence/size/downloadable)",
              flush=True)
    if args.dry_run:
        for r in rows[:20]:
            print(json.dumps({k: r[k] for k in ("id", "name", "category", "license", "author", "tris")}))
        print(f"dry run: {len(rows)} rows would be indexed")
        return

    llm = LLM() if args.embed else None
    done = 0
    for start in range(0, len(rows), BATCH):
        batch = rows[start: start + BATCH]
        vectors = await llm.embed([index_text(r) for r in batch]) if llm else None
        done += await model_library.upsert(batch, vectors)
        print(f"  indexed {done}/{len(rows)}", flush=True)
    print(f"done: {await model_library.count('objaverse')} objaverse models in the index")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--categories", default="", help="comma-separated LVIS categories (default: all)")
    ap.add_argument("--per-category", type=int, default=0, help="cap objects per category (0 = all)")
    ap.add_argument("--no-embed", dest="embed", action="store_false", help="full-text search only (no OpenAI)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be indexed; write nothing")
    a = ap.parse_args()
    load_env(ROOT / ".env")
    CACHE = Path(os.environ.get("OBJAVERSE_CACHE") or CACHE)
    if os.environ.get("RAG_DATABASE_URL", "").find("@postgres:") >= 0:  # compose hostname -> host port
        os.environ["RAG_DATABASE_URL"] = os.environ["RAG_DATABASE_URL"].replace("@postgres:", "@localhost:")
    from omnific_common.aio import run

    run(main(a))
