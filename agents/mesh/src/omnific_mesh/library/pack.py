"""Pack a multi-file glTF (JSON + .bin + images, e.g. Poly Haven's downloads) into one GLB.

Every file is fetched through the safe downloader (HTTPS, public hosts, size cap shared by all files), only the files
the source listed are used, and the result goes through check_glb and our own GLB reader / writer like any model.
"""

from __future__ import annotations

import base64
import json
import posixpath
import struct
from collections.abc import Awaitable, Callable
from urllib.parse import unquote

from .fetch import UnsafeDownload, max_bytes

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _key(uri: str) -> str:
    return posixpath.normpath(unquote(uri)).lstrip("./")


def _data_uri(uri: str) -> bytes | None:
    if not uri.startswith("data:"):
        return None
    head, _, body = uri.partition(",")
    if not head.endswith(";base64"):
        raise UnsafeDownload("unsupported data URI in glTF")
    return base64.b64decode(body)


def to_glb(doc: dict, files: dict[str, bytes]) -> bytes:
    """`doc` with every external buffer and image replaced by views into one binary chunk. `files` maps each URI
    (relative, as written in the glTF) to its bytes; a URI missing from it is an error, never a fetch."""
    files = {_key(k): v for k, v in files.items()}
    blob = bytearray()

    def add(data: bytes) -> int:
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(data)
        return off

    def load(uri: str) -> bytes:
        data = _data_uri(uri)
        if data is not None:
            return data
        k = _key(uri)
        if k not in files:
            raise UnsafeDownload(f"glTF references {uri!r}, which the source did not provide")
        return files[k]

    offsets = [add(load(b["uri"])) if "uri" in b else None for b in doc.get("buffers", [])]
    if any(o is None for o in offsets):
        raise UnsafeDownload("glTF buffer without a URI")
    for v in doc.get("bufferViews", []):
        v["byteOffset"] = v.get("byteOffset", 0) + offsets[v["buffer"]]
        v["buffer"] = 0
    views = doc.setdefault("bufferViews", [])
    for img in doc.get("images", []):
        uri = img.pop("uri", None)
        if uri is None:
            continue
        data = load(uri)
        mime = img.get("mimeType") or (uri[5:].split(";", 1)[0] if uri.startswith("data:")
                                       else MIME.get(posixpath.splitext(_key(uri))[1].lower()))
        if mime not in MIME.values():
            mime = None
        if mime is None:
            raise UnsafeDownload(f"unsupported image type {uri!r}")
        views.append({"buffer": 0, "byteOffset": add(data), "byteLength": len(data)})
        img["bufferView"], img["mimeType"] = len(views) - 1, mime
    while len(blob) % 4:
        blob.append(0)
    doc["buffers"] = [{"byteLength": len(blob)}]
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob))


async def fetch_and_pack(gltf_url: str, includes: dict[str, str],
                         download: Callable[[str, int], Awaitable[bytes]]) -> bytes:
    """Download a glTF and the files it includes (one shared size budget), then pack them into a GLB."""
    budget = max_bytes()
    doc_bytes = await download(gltf_url, budget)
    budget -= len(doc_bytes)
    try:
        doc = json.loads(doc_bytes)
    except ValueError as e:
        raise UnsafeDownload(f"not a glTF JSON file: {e}") from None
    files: dict[str, bytes] = {}
    for rel, url in includes.items():
        if budget <= 0:
            raise UnsafeDownload(f"model files exceed the {max_bytes() / 1e6:.0f} MB limit")
        data = await download(url, budget)
        budget -= len(data)
        files[rel] = data
    return to_glb(doc, files)
