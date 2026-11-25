"""Sketchfab and Poly Haven sources, from recorded API responses (no network)."""

import json
import struct

import httpx
import numpy as np
import pytest
from omnific_common.contracts import LibraryCandidate
from omnific_mesh.asset import from_glb
from omnific_mesh.library import fetch, pack, sources

UID = "a" * 32
SKETCHFAB_SEARCH = {"results": [
    {"uid": UID, "name": "Red Car", "license": {"uid": "322a749bcfa841b29dff1e8a1bb74b0b", "label": "CC Attribution"},
     "user": {"username": "ann", "displayName": "Ann"}, "viewerUrl": f"https://sketchfab.com/3d-models/car-{UID}",
     "thumbnails": {"images": [{"url": "https://media.sketchfab.com/t/1024.jpeg", "width": 1024},
                               {"url": "https://media.sketchfab.com/t/256.jpeg", "width": 256}]},
     "archives": {"glb": {"size": 1_000_000, "faceCount": 12000}}, "animationCount": 0},
    {"uid": "b" * 32, "name": "Share-alike car", "license": {"label": "CC Attribution-ShareAlike"}},
    {"uid": "c" * 32, "name": "Standard licence car", "license": {"label": "Standard"}},
    {"uid": "d" * 32, "name": "Huge car", "license": {"slug": "cc0"}, "archives": {"glb": {"size": 10**10}}},
    {"uid": "e" * 32, "name": "Public domain car", "license": {"label": "CC0 Public Domain"}, "animationCount": 2},
]}


def mock_client(handler):
    def factory(timeout: float = 30.0):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)
    return factory


@pytest.fixture
def requests_seen(monkeypatch):
    seen: list[httpx.Request] = []
    downloads: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/v3/search":
            return httpx.Response(200, json=SKETCHFAB_SEARCH)
        if req.url.path == f"/v3/models/{UID}/download":
            return httpx.Response(200, json={"glb": {"url": "https://sketchfab-prod-media.s3.amazonaws.com/x.glb",
                                                     "size": 1000}})
        return httpx.Response(404)

    async def fake_download(url, limit=None, timeout=120.0):
        downloads.append(url)
        return b"glTF-bytes"

    monkeypatch.setattr(sources, "client", mock_client(handler))
    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setenv("SKETCHFAB_API_TOKEN", "tok-123")
    return seen, downloads


async def test_sketchfab_search_keeps_only_cc0_and_cc_by(requests_seen):
    seen, _ = requests_seen
    out = await sources.Sketchfab().search("red car", 6)
    assert [(c.id, c.license) for c in out] == [(UID, "CC-BY 4.0"), ("e" * 32, "CC0 1.0")]
    car = out[0]
    assert car.author == "Ann" and car.tris == 12000 and car.thumb_url.endswith("/256.jpeg")
    assert car.download_url == "" and out[1].animated
    assert "authorization" not in seen[0].headers  # search is public: no token sent


async def test_sketchfab_token_only_goes_to_the_api(requests_seen):
    seen, downloads = requests_seen
    car = LibraryCandidate(source="sketchfab", id=UID, name="Red Car", license="CC-BY 4.0", author="Ann", url="u")
    assert await sources.fetch_candidate(car) == b"glTF-bytes"
    api = [r for r in seen if r.url.path.endswith("/download")]
    assert len(api) == 1 and api[0].url.host == "api.sketchfab.com"
    assert api[0].headers["authorization"] == "Token tok-123"
    assert downloads == ["https://sketchfab-prod-media.s3.amazonaws.com/x.glb"]  # fetched without any header


async def test_sketchfab_refuses_odd_ids_and_missing_token(requests_seen, monkeypatch):
    bad = LibraryCandidate(source="sketchfab", id="../../me", name="x", license="CC0 1.0", author="", url="u")
    with pytest.raises(fetch.UnsafeDownload, match="model id"):
        await sources.fetch_candidate(bad)
    monkeypatch.setenv("SKETCHFAB_API_TOKEN", "")
    assert not sources.Sketchfab().available()
    ok = LibraryCandidate(source="sketchfab", id=UID, name="x", license="CC0 1.0", author="", url="u")
    with pytest.raises(fetch.UnsafeDownload, match="SKETCHFAB_API_TOKEN"):
        await sources.fetch_candidate(ok)


CATALOGUE = {
    "wooden_table_02": {"name": "Wooden Table 02", "tags": ["table", "wood"], "categories": ["furniture"],
                        "authors": {"Jane": "All"}, "download_count": 50, "polycount": 3000},
    "park_bench": {"name": "Park Bench", "tags": ["bench", "park", "wood"], "categories": ["furniture"],
                   "authors": {"Joe": "All"}, "download_count": 90},
    "rock_01": {"name": "Rock 01", "tags": ["rock"], "categories": ["nature"], "authors": {"Ann": "All"}},
}


def test_polyhaven_local_search_ranks_name_over_tags():
    out = sources.polyhaven_rank(CATALOGUE, "wooden park bench", 5)
    assert [c.id for c in out] == ["park_bench", "wooden_table_02"]
    bench = out[0]
    assert bench.license == "CC0 1.0" and bench.author == "Joe" and bench.url == "https://polyhaven.com/a/park_bench"
    assert bench.thumb_url.startswith("https://cdn.polyhaven.com/") and out[1].tris == 3000
    assert sources.polyhaven_rank(CATALOGUE, "spaceship", 5) == []


def triangle_gltf() -> tuple[dict, dict[str, bytes]]:
    pos = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                          "min": [0, 0, 0], "max": [1, 1, 0]}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}],
           "buffers": [{"byteLength": 36, "uri": "tri%20angle.bin"}],
           "images": [{"uri": "textures/diff_1k.png"}]}
    return doc, {"tri angle.bin": pos, "textures/diff_1k.png": png}


def test_pack_embeds_buffers_and_images():
    doc, files = triangle_gltf()
    glb = pack.to_glb(json.loads(json.dumps(doc)), files)
    fetch.check_glb(glb)
    a = from_glb(glb)
    assert np.allclose(a.positions.max(0), [1, 1, 0]) and len(a.positions) == 3
    with pytest.raises(fetch.UnsafeDownload, match="did not provide"):
        pack.to_glb(json.loads(json.dumps(doc)), {"tri angle.bin": files["tri angle.bin"]})


async def test_polyhaven_fetch_packs_the_listed_files(monkeypatch):
    doc, files = triangle_gltf()
    urls = {"https://dl.polyhaven.org/m.gltf": json.dumps(doc).encode(),
            "https://dl.polyhaven.org/tri angle.bin": files["tri angle.bin"],
            "https://dl.polyhaven.org/textures/diff_1k.png": files["textures/diff_1k.png"]}
    fetched: list[str] = []

    async def fake_download(url, limit=None, timeout=120.0):
        fetched.append(url)
        return urls[url]

    def handler(req):
        return httpx.Response(200, json={"gltf": {"1k": {"gltf": {"url": "https://dl.polyhaven.org/m.gltf", "include": {
            "tri angle.bin": {"url": "https://dl.polyhaven.org/tri angle.bin"},
            "textures/diff_1k.png": {"url": "https://dl.polyhaven.org/textures/diff_1k.png"}}}}}})

    monkeypatch.setattr(sources, "client", mock_client(handler))
    monkeypatch.setattr(fetch, "download", fake_download)
    c = LibraryCandidate(source="polyhaven", id="park_bench", name="Park Bench", license="CC0 1.0", author="Joe",
                         url="u")
    glb = await sources.fetch_candidate(c)
    fetch.check_glb(glb)
    assert fetched[0].endswith("m.gltf") and len(fetched) == 3
    with pytest.raises(fetch.UnsafeDownload, match="asset id"):
        await sources.fetch_candidate(c.model_copy(update={"id": "../x"}))


def test_new_sources_are_registered():
    assert {"sketchfab", "polyhaven"} <= set(sources.SOURCES)
    assert sources.PolyHaven().available()
