"""Library route without network or LLM: licence policy, download safety, GLB handling of library-style models
(several materials, own skin and clips), the search/fetch/rig activities with fake sources, and a SceneBuild run
through the real CPU stages that ends with attribution in scene.gltf."""

import json
import uuid

import httpx
import numpy as np
import pytest
from omnific_common.contracts import (
    AssetPlan,
    ClipPlan,
    LibraryCandidate,
    LibraryFetchInput,
    MeshStageInput,
    SceneBuildInput,
    SceneOutput,
    TaskQueue,
)
from omnific_common.storage import LocalStore
from omnific_mesh import activities, library, meshops
from omnific_mesh.asset import Asset, _Writer, add_asset, from_glb, pack_glb, to_glb
from omnific_mesh.library import fetch, licence
from omnific_mesh.library.sources import parse_polypizza
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, StateVariable, TimeSpan
from test_mesh_v2 import creature, icosphere

SPEC = SimSpec(title="car", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
               parameters=[], equations=[], time=TimeSpan(end=1.0),
               state=[StateVariable(name="v", unit="m/s", initial_value=0.0, description="speed")],
               entities=[Entity(name="car", kind="car", bound_state=["v"]), Entity(name="floor", kind="ground plane")])


def plan(**kw) -> AssetPlan:
    base = {"entity": "car", "kind": "car", "routes": ["library", "procedural"],
            "route_reason": "a car is easy to find", "search_query": "red car", "cad_brief": "",
            "gen_prompt": "a red hatchback", "rig": "none", "clips": [], "poly_budget": 4000}
    return AssetPlan(**{**base, **kw})


def cand(i="1", **kw) -> LibraryCandidate:
    base = {"source": "fake", "id": i, "name": f"Car {i}", "license": "CC-BY 4.0", "author": "ann",
            "url": f"https://x.test/{i}", "download_url": f"https://cdn.test/{i}.glb", "thumb_url": ""}
    return LibraryCandidate(**{**base, **kw})


def two_material_glb() -> bytes:
    """A library-style model: two meshes, two flat-coloured materials, no textures, no vertex colours."""
    w = _Writer()
    gltf: dict = {"asset": {"version": "2.0"}, "scene": 0, "nodes": []}
    roots = []
    for k, rgba in enumerate([(1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)]):
        a = icosphere(radius=0.3)
        a.positions = a.positions + np.array([k * 0.8, 0, 0], np.float32)
        a.colors, a.base_color = None, rgba
        roots.append(add_asset(w, gltf, a, f"part{k}"))
    gltf["scenes"] = [{"nodes": roots}]
    return pack_glb(gltf, bytes(w.blob), w)


# --- licence ----------------------------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "want"), [
    ("cc0", "CC0 1.0"), ("CC0 1.0 Universal", "CC0 1.0"), ("by", "CC-BY 4.0"), ("CC-BY 3.0", "CC-BY 3.0"),
    ("Creative Commons Attribution", "CC-BY 4.0"), ("by-nc", None), ("CC-BY-NC-SA 4.0", None), ("by-sa", None),
    ("by-nd", None), ("Creative Commons Attribution-NoDerivs 3.0 Unported", None), ("", None), ("All rights reserved", None), (None, None)])
def test_licence_policy(raw, want):
    assert licence.normalise(raw) == want


def test_polypizza_parsing_is_defensive():
    c = parse_polypizza({"ID": "abc", "Title": "Car", "Creator": {"Username": "quaternius"}, "Licence": "CC0 1.0",
                         "Download": "https://static.poly.pizza/abc.glb", "Thumbnail": "https://t/abc.jpg",
                         "TriangleCount": 900, "Animated": False})
    assert (c.id, c.author, c.tris, c.url) == ("abc", "quaternius", 900, "https://poly.pizza/m/abc")
    assert parse_polypizza({"ID": 7, "Download": "u"}).name == "7"


async def test_search_all_merges_filters_and_reports_errors(monkeypatch):
    class Src:
        def __init__(self, name, items=None, error=None):
            self.name, self.items, self.error = name, items or [], error

        def available(self):
            return True

        async def search(self, query, k):
            if self.error:
                raise self.error
            return library.sources._allowed(self.items)

    monkeypatch.setattr(library, "SOURCES", {
        "a": Src("a", [cand("1"), cand("2", license="by-nc"), cand("3")]),
        "b": Src("b", [cand("1", source="b"), cand("1")]),  # same id from another source is a different model
        "c": Src("c", error=RuntimeError("down"))})
    got, notes = await library.search_all(["a", "b", "c", "unknown"], "car")
    assert [(c.source, c.id) for c in got] == [("fake", "1"), ("b", "1"), ("fake", "3")]
    assert all(c.license == "CC-BY 4.0" for c in got) and notes == ["c: search failed (RuntimeError: down)"]


# --- downloads ------------------------------------------------------------------------------------

async def test_downloads_refuse_http_private_hosts_and_oversize(monkeypatch):
    with pytest.raises(fetch.UnsafeDownload, match="non-HTTPS"):
        await fetch.download("http://example.com/a.glb")
    with pytest.raises(fetch.UnsafeDownload, match="non-public"):
        await fetch.download("https://localhost/a.glb")
    with pytest.raises(fetch.UnsafeDownload, match="non-public"):
        await fetch.download("https://169.254.169.254/latest/meta-data")

    def handler(request):
        return httpx.Response(200, content=b"x" * 2000)

    def fake_client(timeout=30.0):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(fetch, "client", fake_client)
    assert len(await fetch.download("https://cdn.test/a.glb", limit=4000)) == 2000
    with pytest.raises(fetch.UnsafeDownload, match="limit"):
        await fetch.download("https://cdn.test/a.glb", limit=1000)
    with pytest.raises(fetch.UnsafeDownload, match="magic"):
        fetch.check_glb(b"<html>not a model</html>")
    fetch.check_glb(to_glb(icosphere()))


# --- GLB handling ----------------------------------------------------------------------------------

def test_several_materials_are_baked_to_vertex_colours():
    a = from_glb(two_material_glb())
    assert a.texture is None and a.colors is not None
    n = len(a.positions) // 2
    assert np.allclose(a.colors[:n], [1, 0, 0]) and np.allclose(a.colors[n:], [0, 0, 1])


def test_required_extensions_are_rejected():
    data = to_glb(icosphere())
    from omnific_mesh.asset import unpack_glb

    gltf, blob = unpack_glb(data)
    gltf["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    with pytest.raises(ValueError, match="draco"):
        from_glb(pack_glb(gltf, blob))


def test_postprocess_keeps_a_skin_through_decimation():
    a = creature()
    dense = Asset(**vars(a))
    import trimesh

    # subdivide so decimation has something to do; carry the skin by nearest vertex like a real model would have
    tm = trimesh.Trimesh(a.positions, a.indices, process=False).subdivide().subdivide()
    from scipy.spatial import cKDTree

    _, near = cKDTree(a.positions).query(tm.vertices)
    dense.positions, dense.indices = tm.vertices.astype(np.float32), tm.faces.astype(np.uint32)
    dense.colors, dense.joints, dense.weights = a.colors[near], a.joints[near], a.weights[near]
    out, notes = meshops.postprocess(dense, 1.0, 300)
    assert out.n_tris <= 300 and len(out.bones) == 3 and out.joints is not None and len(out.joints) == len(
        out.positions)
    assert any("skin weights" in n for n in notes)
    lo, hi = out.bbox()
    assert abs(float((hi - lo).max()) - 1.0) < 1e-4
    # bones moved with the mesh: the root head is still inside the (rescaled) body
    assert np.all(out.bones[0].head >= lo) and np.all(out.bones[0].head <= hi)


def test_match_clips():
    from omnific_mesh.asset import Clip

    t = np.linspace(0, 1, 5)
    clips = [Clip("Armature|Idle_Loop", t, {}), Clip("Armature|Run", t, {}), Clip("Death", t, {})]
    plans = [ClipPlan(name="walk", kind="locomotion", description="", drive_state="v"),
             ClipPlan(name="idle", kind="idle", description="", drive_state=""),
             ClipPlan(name="wave", kind="flex", description="", drive_state="")]
    got = [(c.name, p.name) for c, p in activities.match_clips(clips, plans)]
    assert got == [("Armature|Run", "walk"), ("Armature|Idle_Loop", "idle")]


# --- activities with fake sources --------------------------------------------------------------------

class FakeLLM:
    """Vision critic: approves anything but `reject` calls; shortlist picks thumbnails in order."""

    def __init__(self, reject_after=None):
        self.critic_calls = 0
        self.reject_after = reject_after

    async def structured_images(self, role, instructions, text, images, schema):
        if schema.__name__ == "LibraryShortlist":
            return schema(picks=[2, 1], scores=[9, 7], reason="both are cars")
        self.critic_calls += 1
        ok = self.reject_after is None or self.critic_calls <= self.reject_after
        return schema(matches=ok, score=8 if ok else 2, defects=[] if ok else ["a boat"], critique="")

    async def structured(self, role, instructions, input, schema):
        from omnific_common.contracts import AssetPlanOutput

        floor = AssetPlan(entity="floor", kind="ground plane", routes=["procedural"], route_reason="",
                          search_query="", cad_brief="", gen_prompt="", rig="none", clips=[], poly_budget=0)
        return AssetPlanOutput(assets=[plan(), floor])


@pytest.fixture
def lib(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    monkeypatch.setattr(activities, "get_store", lambda: store)
    monkeypatch.setattr(activities, "llm", FakeLLM())
    files = {"https://cdn.test/1.glb": to_glb(icosphere()), "https://cdn.test/2.glb": two_material_glb(),
             "https://cdn.test/html.glb": b"<html></html>", "https://cdn.test/rigged.glb": rigged_glb(),
             "https://t.test/1.png": png_bytes(), "https://t.test/2.png": png_bytes()}

    async def fake_download(url, limit=None, timeout=120.0):
        if url not in files:
            raise fetch.UnsafeDownload(f"404 {url}")
        return files[url]

    class Src:
        name = "fake"

        def __init__(self):
            self.items = [cand("1", thumb_url="https://t.test/1.png"), cand("2", thumb_url="https://t.test/2.png")]

        def available(self):
            return True

        async def search(self, query, k):
            return list(self.items)

    monkeypatch.setattr(library, "download", fake_download)
    monkeypatch.setattr(library.fetch, "download", fake_download)
    monkeypatch.setattr(library, "SOURCES", {"fake": Src()})
    monkeypatch.setattr(library.sources, "SOURCES", {"fake": Src()})
    monkeypatch.setenv("MESH_LIBRARY_SOURCES", "fake")
    monkeypatch.delenv("MESH_GPU_ENABLED", raising=False)
    monkeypatch.delenv("RAG_DATABASE_URL", raising=False)
    return store, Src


def png_bytes() -> bytes:
    from omnific_mesh.asset import png
    from PIL import Image

    return png(Image.new("RGB", (64, 64), (200, 30, 30)))


def rigged_glb() -> bytes:
    from omnific_mesh import anim

    a = creature()
    a.clips = [anim.procedural_clip(a, ClipPlan(name="Walk_Cycle", kind="locomotion", description="", drive_state=""))]
    return to_glb(a, "dog")


async def test_search_shortlists_by_thumbnail_and_walks_query_variants(lib):
    out = await activities.library_search(MeshStageInput(job_id="j", plan=plan(), spec=SPEC))
    assert out.eval.passed and [c.id for c in out.candidates] == ["2", "1"] and not out.exhausted
    assert "red car" in out.eval.checks[0].detail
    second = await activities.library_search(MeshStageInput(job_id="j", plan=plan(), spec=SPEC, attempt=1))
    assert "'car'" in second.eval.checks[0].detail and second.exhausted
    done = await activities.library_search(MeshStageInput(job_id="j", plan=plan(), spec=SPEC, attempt=2))
    assert done.exhausted and not done.candidates and not done.eval.passed


async def test_fetch_accepts_a_glb_and_rejects_html(lib):
    store, _ = lib
    ok = await activities.library_fetch(LibraryFetchInput(job_id="j", plan=plan(), candidate=cand("2")))
    assert ok.eval.passed and ok.artifact.source == "library" and store.exists(ok.artifact.glb_key)
    assert ok.artifact.attribution.author == "ann" and ok.artifact.attribution.license == "CC-BY 4.0"
    bad = await activities.library_fetch(LibraryFetchInput(job_id="j", plan=plan(),
                                                           candidate=cand("3", download_url="https://cdn.test/html.glb")))
    assert not bad.eval.passed and "bad magic" in bad.eval.checks[0].detail


async def test_library_rig_keeps_a_good_skin_and_matching_clips(lib):
    store, _ = lib
    dog = plan(entity="dog", kind="dog", rig="ml", clips=[
        ClipPlan(name="walk", kind="locomotion", description="trot", drive_state="v"),
        ClipPlan(name="idle", kind="idle", description="breathe", drive_state="")])
    fetched = await activities.library_fetch(LibraryFetchInput(
        job_id="j", plan=dog, candidate=cand("r", download_url="https://cdn.test/rigged.glb")))
    assert fetched.artifact.bones == ["root", "leg_l", "leg_r"]
    out = await activities.library_rig(MeshStageInput(job_id="j", plan=dog, spec=SPEC, artifact=fetched.artifact))
    assert out.eval.passed and out.artifact.bones and [c.name for c in out.artifact.clips] == ["walk"]
    a = from_glb(store.get(out.artifact.glb_key))
    assert [c.name for c in a.clips] == ["walk"] and a.clips[0].root_translation is None


# --- the whole route through SceneBuild ----------------------------------------------------------------

async def run_scene(store, llm) -> SceneOutput:
    from omnific_common.temporal import workflow_runner
    from omnific_workflows.scene import SceneBuild
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    activities.llm = llm
    try:
        env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot start Temporal test server: {exc}")
    async with env, \
            Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[SceneBuild],
                   workflow_runner=workflow_runner()), \
            Worker(env.client, task_queue=TaskQueue.MESH.value, activities=activities.SCENE_ACTIVITIES):
        return await env.client.execute_workflow(
            "SceneBuild", SceneBuildInput(job_id=uuid.uuid4().hex[:8], prompt="a car rolls", spec=SPEC,
                                          backend="generated"),
            id=uuid.uuid4().hex, task_queue=TaskQueue.WORKFLOW.value, result_type=SceneOutput)


async def test_scene_build_uses_a_library_model_with_attribution(lib, monkeypatch):
    store, _ = lib
    out = await run_scene(store, FakeLLM())
    car = next(r for r in out.report.entities if r.entity == "car")
    assert car.final == "library" and car.routes_tried == ["library"] and car.route_used == "library"
    assert car.attribution is not None and car.attribution.url == "https://x.test/2"  # the shortlist's first pick
    assert [e.stage for e in car.evals] == ["library", "library", "library", "postprocess"]
    gltf = json.loads(store.get(out.scene_key))
    assert "Car 2" in gltf["asset"]["copyright"] and "CC-BY 4.0" in gltf["asset"]["copyright"]
    node = next(n for n in gltf["nodes"] if n["name"] == "car")
    assert node["extras"]["omnific"]["source"] == "library"
    assert node["extras"]["omnific"]["attribution"]["author"] == "ann"


async def test_scene_build_falls_back_when_every_candidate_is_rejected(lib):
    store, _ = lib
    out = await run_scene(store, FakeLLM(reject_after=0))  # the vision critic rejects every model
    car = next(r for r in out.report.entities if r.entity == "car")
    assert car.final == "procedural" and car.routes_tried == ["library", "procedural"]
    assert car.attribution is None and any("no library model" in n for n in car.notes)
    assert "copyright" not in json.loads(store.get(out.scene_key))["asset"]


# --- asset picker ----------------------------------------------------------------------------------------

async def test_asset_choices_ranks_scores_and_stores_thumbnails(lib):
    from omnific_common.contracts import EntityChoicesInput

    store, _ = lib
    out = await activities.asset_choices(EntityChoicesInput(job_id="j", plan=plan(),
                                                            routes_available=["library", "procedural"]))
    assert out.entity == "car" and out.default_route == "library" and out.query == "red car"
    assert [o.candidate.id for o in out.options] == ["2", "1"] and [o.score for o in out.options] == [9, 7]
    assert all(o.status == "shortlisted" and o.thumb_key.startswith("jobs/j/choices/car/") for o in out.options)
    assert store.get(out.options[0].thumb_key)[:4] == b"\x89PNG"
    assert "triangle" in out.options[0].detail


async def test_asset_choices_for_a_primitive_searches_by_kind(lib):
    from omnific_common.contracts import EntityChoicesInput

    ball = plan().model_copy(update={"entity": "ball", "kind": "bowling_ball", "routes": ["procedural"],
                                     "search_query": ""})
    out = await activities.asset_choices(EntityChoicesInput(job_id="j", plan=ball,
                                                            routes_available=["library", "procedural"]))
    assert out.default_route == "procedural" and out.query == "bowling ball" and len(out.options) == 2


async def test_asset_choices_without_libraries(lib, monkeypatch):
    from omnific_common.contracts import EntityChoicesInput

    monkeypatch.setenv("MESH_LIBRARY_SOURCES", "")
    out = await activities.asset_choices(EntityChoicesInput(job_id="j", plan=plan(), routes_available=["procedural"]))
    assert out.options == [] and "no model library" in out.note
