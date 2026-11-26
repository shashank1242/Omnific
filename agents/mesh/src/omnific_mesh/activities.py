"""Mesh worker (CPU): the MVP `build_scene` plus the SceneBuild (mesh v2) stages that don't need a GPU.

Each stage writes its GLB to `jobs/<job>/assets/<entity>/<stage>-<attempt>.glb` and returns a MeshArtifact plus
its StageEval. GPU stage outputs (generate, ML rig, ML animate) are judged by `evaluate_stage` here.
"""

import asyncio
import difflib
import hashlib
import io
import json
import logging
import os

import numpy as np
from omnific_common.contracts import (
    ACTIVITY,
    AssembleInput,
    AssembleOutput,
    AssetOption,
    AssetPlan,
    CadEvaluateInput,
    CadSkinInput,
    CadWriteInput,
    CadWriteOutput,
    CheckResult,
    CheckSceneInput,
    ClipPlan,
    DressProp,
    DressPropOption,
    EntityChoices,
    EntityChoicesInput,
    EvaluateStageInput,
    LibraryCandidate,
    LibraryFetchInput,
    LibrarySearchOutput,
    MeshArtifact,
    MeshStageInput,
    MeshStageOutput,
    PlanAssetsInput,
    PlanAssetsOutput,
    RouteStatsInput,
    SceneActivity,
    SceneInput,
    SceneOutput,
    ScenePropsInput,
    ScenePropsOutput,
    StageEval,
    TaskQueue,
)
from omnific_common.llm import LLM
from omnific_common.storage import job_key, store_from_env
from temporalio import activity

from . import anim, cad, cad_skin, critic, library, meshops, orient, render, rig
from .asset import Asset, Clip, from_glb, to_glb
from .assets import asset_for_kind
from .gltf import build_gltf, dumps, rest_positions
from .plan import MAX_BUDGET, capabilities_from_env
from .plan import plan_assets as make_plan
from .scene import assemble, evaluate_scene
from .scene import dumps as scene_dumps

log = logging.getLogger(__name__)

get_store = store_from_env
llm = LLM()


def _heartbeat(detail: str) -> None:
    try:
        activity.heartbeat(detail)
    except RuntimeError:  # unit tests
        pass


@activity.defn(name=ACTIVITY[TaskQueue.MESH])
async def build_scene(inp: SceneInput) -> SceneOutput:
    """MVP path (procedural assets only), kept for callers that don't run SceneBuild."""
    ents = [(e.name, asset_for_kind(e.kind), list(e.bound_state)) for e in inp.spec.entities]
    gltf, assets = build_gltf(ents, rest_positions(inp.spec.entities))
    key = job_key(inp.job_id, "scene.gltf")
    get_store().put(key, dumps(gltf), "model/gltf+json")
    return SceneOutput(scene_key=key, entity_assets=assets)


# --- helpers ----------------------------------------------------------------------------------

def scene_size(kind: str) -> float:
    return meshops.NOMINAL_SIZE.get(asset_for_kind(kind), 1.0)


def stage_key(job_id: str, entity: str, stage: str, attempt: int) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in entity)[:60]
    return job_key(job_id, f"assets/{safe}/{stage}-{attempt}.glb")


def model_versions() -> str:
    return "|".join(os.environ.get(k, "") for k in ("MESH_GEN_MODEL", "MESH_RIG_MODEL", "MESH_ANIM_MODEL"))


def cache_key(plan: AssetPlan) -> str:
    payload = json.dumps({"kind": plan.kind, "prompt": plan.gen_prompt, "rig": plan.rig, "routes": plan.routes,
                          "query": plan.search_query, "cad": plan.cad_brief,
                          "clips": [c.model_dump() for c in plan.clips], "budget": plan.poly_budget,
                          "models": model_versions(),
                          # a CAD model gets a generated surface only where a GPU worker runs
                          **({"cad_skin": True} if "cad" in plan.routes and capabilities_from_env().gpu else {})},
                         sort_keys=True)
    return "assets/" + hashlib.sha256(payload.encode()).hexdigest()[:32]


async def load_asset(key: str) -> Asset:
    return from_glb(await asyncio.to_thread(get_store().get, key))


async def save_asset(a: Asset, key: str, name: str) -> None:
    await asyncio.to_thread(get_store().put, key, to_glb(a, name), "model/gltf-binary")


def describe(a: Asset, art: MeshArtifact, **upd) -> MeshArtifact:
    lo, hi = a.bbox()
    return art.model_copy(update={"n_tris": a.n_tris, "bbox_min": lo.astype(float).tolist(),
                                  "bbox_max": hi.astype(float).tolist(), "bones": [b.name for b in a.bones],
                                  **upd})


def stage_eval(stage, attempt, checks: list[CheckResult], critique: str = "") -> StageEval:
    passed = all(c.passed for c in checks)
    if not passed and not critique:
        critique = "; ".join(f"{c.name}: {c.detail}" for c in checks if not c.passed)
    return StageEval(stage=stage, attempt=attempt, passed=passed, checks=checks, critique=critique)


# --- SceneBuild stages --------------------------------------------------------------------------

@activity.defn(name=SceneActivity.PLAN.value)
async def plan_assets(inp: PlanAssetsInput) -> PlanAssetsOutput:
    caps = inp.capabilities or capabilities_from_env()
    stats = await route_stats_text([e.kind for e in inp.spec.entities]) if inp.backend == "generated" else ""
    plans, ev = await make_plan(llm, inp.spec, inp.prompt, inp.backend, caps, stats)
    log.info("mesh routes: %s", {p.entity: p.routes for p in plans})
    cached = []
    store = get_store()
    for p in plans:
        if p.routes == ["procedural"]:
            continue
        meta = f"{cache_key(p)}/meta.json"
        if await asyncio.to_thread(store.exists, meta):
            cached.append(MeshArtifact.model_validate_json(await asyncio.to_thread(store.get, meta)))
    return PlanAssetsOutput(assets=plans, eval=ev, cached=cached, capabilities=caps)


@activity.defn(name=SceneActivity.PROCEDURAL.value)
async def procedural_mesh(inp: MeshStageInput) -> MeshStageOutput:
    asset_id = asset_for_kind(inp.plan.kind)
    art = MeshArtifact(entity=inp.plan.entity, source="procedural", asset=asset_id)
    from .assets import GENERATORS

    pos, _, idx = GENERATORS[asset_id]()
    ok = bool(np.isfinite(pos).all()) and len(idx) % 3 == 0
    return MeshStageOutput(artifact=art.model_copy(update={"n_tris": len(idx) // 3}),
                           eval=stage_eval("generate", 0, [CheckResult(
                               name="procedural.asset", passed=ok, detail=f"procedural {asset_id!r}")]))


@activity.defn(name=SceneActivity.POSTPROCESS.value)
async def postprocess_mesh(inp: MeshStageInput) -> MeshStageOutput:
    raw = await load_asset(inp.artifact.glb_key)
    size = scene_size(inp.plan.kind)
    generated = inp.artifact.source == "generated"
    # Authored models (library, CAD) are already optimised and decimating their many separate parts mangles them
    # (measured on Objaverse cars): only the hard cap applies to them. Generated meshes go to the planned budget.
    budget = inp.plan.poly_budget if generated else max(inp.plan.poly_budget, MAX_BUDGET)
    try:
        clean, notes = await asyncio.to_thread(meshops.postprocess, Asset(**vars(raw)), size, budget, generated)
    except Exception as e:  # noqa: BLE001 - a broken mesh is an evaluation failure, not a crash
        return MeshStageOutput(artifact=inp.artifact, eval=stage_eval("postprocess", inp.attempt, [CheckResult(
            name="post.run", passed=False, detail=f"{type(e).__name__}: {e}")]))
    checks = await asyncio.to_thread(meshops.evaluate_postprocessed, raw, clean, size, budget, not generated)
    # per source: one entity can postprocess a CAD model and its generated surface, and needs both afterwards
    key = stage_key(inp.job_id, inp.plan.entity, f"post-{inp.artifact.source}", inp.attempt)
    await save_asset(clean, key, inp.plan.entity)
    if notes:
        checks.append(CheckResult(name="post.notes", passed=True, detail="; ".join(notes)))
    art = describe(clean, inp.artifact, glb_key=key)
    if inp.artifact.source in ("library", "generated"):  # authored elsewhere: find its front and top (CAD builds +X/+Y)
        _heartbeat("finding which way it faces")
        turn, c = await orient.facing(llm, clean, _what(inp.plan))
        checks.append(c)
        art = art.model_copy(update={"orientation": turn})
    return MeshStageOutput(artifact=art, eval=stage_eval("postprocess", inp.attempt, checks))


@activity.defn(name=SceneActivity.PHYSICS_RIG.value)
async def physics_rig(inp: MeshStageInput) -> MeshStageOutput:
    joint = rig.physics_joint(inp.spec, inp.plan.entity)
    rest = rest_positions(inp.spec.entities).get(inp.plan.entity)
    checks = rig.evaluate_physics_joint(inp.spec, inp.plan.entity, joint, rest)
    return MeshStageOutput(artifact=inp.artifact.model_copy(update={"joint": joint}),
                           eval=stage_eval("physics_rig", inp.attempt, checks))


@activity.defn(name=SceneActivity.PROCEDURAL_ANIMATE.value)
async def procedural_animate(inp: MeshStageInput) -> MeshStageOutput:
    """Procedural clips for a skinned asset. Clips that fail their checks are dropped (reported), not retried:
    procedural motion is deterministic."""
    a = await load_asset(inp.artifact.glb_key)
    if not a.bones:
        return MeshStageOutput(artifact=inp.artifact, eval=stage_eval("animate", inp.attempt, [CheckResult(
            name="anim.skeleton", passed=False, detail="no skeleton to animate")]))
    checks, kept_clips, kept_plans = [], [], []
    for cp in inp.plan.clips:
        clip = anim.procedural_clip(a, cp)
        cc = [c.model_copy(update={"name": f"{cp.name}.{c.name}"}) for c in anim.evaluate_clip(a, clip, cp.kind)]
        checks += cc
        if all(c.passed for c in cc):
            kept_clips.append(clip)
            kept_plans.append(cp)
    a.clips = kept_clips
    key = stage_key(inp.job_id, inp.plan.entity, "anim-procedural", inp.attempt)
    await save_asset(a, key, inp.plan.entity)
    return MeshStageOutput(artifact=describe(a, inp.artifact, glb_key=key, clips=kept_plans),
                           eval=stage_eval("animate", inp.attempt, checks))


@activity.defn(name=SceneActivity.EVALUATE.value)
async def evaluate_stage(inp: EvaluateStageInput) -> StageEval:
    """Judge a GPU stage's output: deterministic checks first, then (only if those pass) the vision critic."""
    try:
        a = await load_asset(inp.artifact.glb_key)
    except Exception as e:  # noqa: BLE001
        return stage_eval(inp.stage, inp.attempt, [CheckResult(name="glb.load", passed=False,
                                                                detail=f"unreadable GLB: {type(e).__name__}: {e}")])
    if inp.stage in ("generate", "library"):
        checks = await asyncio.to_thread(meshops.evaluate_generated, a)
        if all(c.passed for c in checks):
            _heartbeat("rendering")
            sheet = await asyncio.to_thread(render.turntable, a)
            c, crit = await critic.critique(llm, inp.stage, sheet, _what(inp.plan))
            checks.append(c)
            return stage_eval(inp.stage, inp.attempt, checks, crit if not c.passed else "")
        return stage_eval(inp.stage, inp.attempt, checks)
    if inp.stage == "ml_rig":
        return await judge_rig(a, inp.plan, inp.attempt)
    if inp.stage == "animate":
        return await judge_clips(a, inp.plan, inp.attempt)
    return stage_eval(inp.stage, inp.attempt, [CheckResult(name="stage", passed=False,
                                                            detail=f"no evaluator for stage {inp.stage!r}")])


def _what(plan: AssetPlan) -> str:
    return f"{plan.kind}: {plan.gen_prompt or plan.search_query}"


async def judge_rig(a: Asset, plan: AssetPlan, attempt: int) -> StageEval:
    """Skin + deformation checks, then (only if those pass) the vision critic on a posed contact sheet."""
    checks = rig.evaluate_skin(a)
    if all(c.passed for c in checks):
        checks += await asyncio.to_thread(rig.evaluate_deformation, a)
    if all(c.passed for c in checks):
        _heartbeat("rendering")
        sheet = await asyncio.to_thread(_posed_sheet, a)
        c, crit = await critic.critique(llm, "ml_rig", sheet, _what(plan))
        checks.append(c)
        return stage_eval("ml_rig", attempt, checks, crit if not c.passed else "")
    return stage_eval("ml_rig", attempt, checks)


async def judge_clips(a: Asset, plan: AssetPlan, attempt: int) -> StageEval:
    checks: list[CheckResult] = []
    kinds = {c.name: c for c in plan.clips}
    if not a.clips:
        checks.append(CheckResult(name="anim.clips", passed=False, detail="no clips in the output"))
    for clip in a.clips:
        cp = kinds.get(clip.name)
        cc = anim.evaluate_clip(a, clip, cp.kind if cp else "custom")
        checks += [c.model_copy(update={"name": f"{clip.name}.{c.name}"}) for c in cc]
    crit = ""
    if all(c.passed for c in checks):
        for clip in a.clips:
            _heartbeat(f"rendering {clip.name}")
            poses = anim.sample_poses(clip)
            sheet = await asyncio.to_thread(
                render.frame_strip, a, [rig.skin(a, p) for _, p in poses], [f"t={t:.2f}s" for t, _ in poses])
            motion = kinds[clip.name].description if clip.name in kinds else clip.name
            c, cr = await critic.critique(llm, "animate", sheet, _what(plan), motion)
            checks.append(c.model_copy(update={"name": f"{clip.name}.{c.name}"}))
            crit = crit or cr if not c.passed else crit
    return stage_eval("animate", attempt, checks, crit)


def _posed_sheet(a: Asset) -> bytes:
    from .asset import axis_angle_quat

    movable = [b for b in a.bones if b.parent >= 0][:3]
    posed = [rig.skin(a)] + [rig.skin(a, {b.name: axis_angle_quat((1, 0, 0), sgn * rig.DEFORM_ANGLE)})
                             for b in movable for sgn in (1,)]
    labels = ["rest"] + [f"{b.name} +30°" for b in movable]
    return render.frame_strip(a, posed, labels)


@activity.defn(name=SceneActivity.ASSEMBLE.value)
async def assemble_scene(inp: AssembleInput) -> AssembleOutput:
    store = get_store()
    gltf, assets = await asyncio.to_thread(assemble, inp.spec, inp.artifacts, store.get, inp.length_scale)
    data = scene_dumps(gltf)
    checks = evaluate_scene(inp.spec, gltf, len(data))
    key = job_key(inp.job_id, "scene.gltf")
    await asyncio.to_thread(store.put, key, data, "model/gltf+json")
    return AssembleOutput(scene_key=key, entity_assets=assets, eval=stage_eval("assemble", 0, checks))


@activity.defn(name=SceneActivity.CHECK_SCENE.value)
async def check_scene(inp: CheckSceneInput) -> StageEval:
    """The scene.gltf contract (entity roots, glTF structure, size, declared clips) on any stored scene: scene
    dressing uses it so the dressed file meets exactly the bar SceneBuild's own assembly does."""
    data = await asyncio.to_thread(get_store().get, inp.scene_key)
    try:
        gltf = json.loads(data)
    except ValueError as e:
        return stage_eval("dress", 0, [CheckResult(name="scene.json", passed=False, detail=f"not JSON: {e}")])
    if not isinstance(gltf, dict):
        return stage_eval("dress", 0, [CheckResult(name="scene.json", passed=False,
                                                   detail=f"not a glTF object ({type(gltf).__name__})")])
    return stage_eval("dress", 0, evaluate_scene(inp.spec, gltf, len(data)))


# --- library route --------------------------------------------------------------------------------

@activity.defn(name=SceneActivity.LIBRARY_SEARCH.value)
async def library_search(inp: MeshStageInput) -> LibrarySearchOutput:
    """Attempt n searches with the n-th query variant; candidates are licence-filtered, then shortlisted by a vision
    pick on their thumbnails."""
    variants = library.query_variants(inp.plan)
    if inp.attempt >= len(variants):
        return LibrarySearchOutput(candidates=[], exhausted=True, eval=stage_eval("library", inp.attempt, [
            CheckResult(name="library.queries", passed=False, detail=f"no query variant left after {variants}")]))
    query = variants[inp.attempt]
    sources = capabilities_from_env().library_sources
    _heartbeat(f"searching {sources} for {query!r}")
    cands, notes = await library.search_all(sources, query)
    detail = f"{len(cands)} CC0/CC-BY candidates for {query!r} from {sources}"
    checks = [CheckResult(name="library.search", passed=bool(cands),
                          detail=detail + (f" ({'; '.join(notes)})" if notes else ""))]
    picked: list = []
    if cands:
        _heartbeat("shortlisting thumbnails")
        picked, reason = await library.shortlist(llm, inp.plan, cands)
        checks.append(CheckResult(name="library.shortlist", passed=bool(picked),
                                  detail=f"{len(picked)} of {len(cands)} picked: {reason}"))
    return LibrarySearchOutput(candidates=picked, eval=stage_eval("library", inp.attempt, checks),
                               exhausted=inp.attempt + 1 >= len(variants))


CHOICE_OPTIONS = 6
HEAVY_TRIS = 100_000  # library models above this are decimated in postprocess


def _thumb_png(data: bytes, size: int = 256) -> bytes | None:
    """A thumbnail from a model library, re-encoded by us as a small PNG (never served as the bytes we fetched)."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((size, size))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - an unreadable preview just means no thumbnail
        return None


def option_detail(c, plan) -> str:
    parts = [f"{c.tris:,} triangles" if c.tris else "triangle count unknown"]
    if c.tris > HEAVY_TRIS:
        parts.append(f"heavy: simplified to about {min(plan.poly_budget or HEAVY_TRIS, HEAVY_TRIS):,}")
    if c.animated:
        parts.append("comes animated")
    return ", ".join(parts)


@activity.defn(name=SceneActivity.ASSET_CHOICES.value)
async def asset_choices(inp: EntityChoicesInput) -> EntityChoices:
    """Candidates the user picks from for one entity: every enabled library searched (licence-filtered to CC0 / CC-BY),
    ranked by the vision shortlist with a score each, thumbnails stored with the job. The full evaluation of the
    options (download, geometry, vision critic, postprocess) is the usual library stages, run by the workflow."""
    plan = inp.plan
    out = EntityChoices(entity=plan.entity, kind=plan.kind, default_route=plan.mode,
                        routes_available=inp.routes_available)
    sources = capabilities_from_env().library_sources
    if "library" not in inp.routes_available or not sources:
        out.note = "no model library is configured"
        return out
    query = " ".join((plan.search_query or plan.kind.replace("_", " ")).split())
    out.query = query
    _heartbeat(f"searching {sources} for {query!r}")
    cands, notes = await library.search_all(sources, query)
    if not cands:
        out.note = f"no CC0 / CC-BY models found for {query!r}" + (f" ({'; '.join(notes)})" if notes else "")
        return out
    _heartbeat("ranking thumbnails")
    ranked, reason = await library.rank(llm, plan.model_copy(update={"search_query": query}), cands, CHOICE_OPTIONS)
    if not ranked:
        out.note = f"none of {len(cands)} models for {query!r} showed the object: {reason}"
        return out
    store = get_store()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in plan.entity)[:60]
    for i, (c, score, thumb) in enumerate(ranked):
        key = ""
        png_bytes = await asyncio.to_thread(_thumb_png, thumb) if thumb else None
        if png_bytes:
            key = job_key(inp.job_id, f"choices/{safe}/{i}.png")
            await asyncio.to_thread(store.put, key, png_bytes, "image/png")
        out.options.append(AssetOption(candidate=c, thumb_key=key, score=score, reason=reason,
                                       detail=option_detail(c, plan)))
    return out


# --- scenery props for scene dressing ---------------------------------------------------------------------

PROP_OPTIONS = 30
MAX_PROPS = 8


def prop_source_enabled() -> bool:
    """Ready-made scenery props come from Poly Haven (CC0, no key). DRESS_PROP_SOURCE=none turns them off."""
    return os.environ.get("DRESS_PROP_SOURCE", "polyhaven").strip().lower() == "polyhaven"


@activity.defn(name=SceneActivity.SCENERY_PROPS.value)
async def scenery_props(inp: ScenePropsInput) -> ScenePropsOutput:
    """Catalogue mode (`query`): CC0 props that fit the setting, for the dress writer to choose from. Fetch mode
    (`ids`): download those props (safe downloader, packed into one GLB), re-encode them with our own reader / writer
    and store them for the Blender runner, which has no network."""
    if not prop_source_enabled():
        return ScenePropsOutput(notes=["ready-made props are turned off (DRESS_PROP_SOURCE)"])
    src = library.SOURCES["polyhaven"]
    try:
        cat = await src.catalogue()
    except Exception as e:  # noqa: BLE001 - props are optional: the scenery can be built from primitives
        return ScenePropsOutput(notes=f"Poly Haven catalogue unavailable: {type(e).__name__}: {e}".split("\n")[:1])
    if not inp.ids:
        out = []
        for c in library.sources.polyhaven_rank(cat, inp.query, PROP_OPTIONS):
            info = cat.get(c.id) or {}
            dims = [round(float(d) / 1000.0, 3) for d in (info.get("dimensions") or [])][:3]
            out.append(DressPropOption(id=c.id, name=c.name, tags=[str(x) for x in (info.get("tags") or [])][:8],
                                       size=dims))
        return ScenePropsOutput(options=out)
    store, props, notes = get_store(), [], []
    for pid in list(dict.fromkeys(inp.ids))[:MAX_PROPS]:
        info = cat.get(pid)
        if not isinstance(info, dict):
            notes.append(f"{pid}: not in the Poly Haven catalogue")
            continue
        c = LibraryCandidate(source="polyhaven", id=pid, name=str(info.get("name") or pid), license="CC0 1.0",
                             author=", ".join((info.get("authors") or {}).keys()), url=f"https://polyhaven.com/a/{pid}")
        try:
            _heartbeat(f"fetching prop {pid}")
            data = await library.fetch_candidate(c)
            library.check_glb(data)
            a = await asyncio.to_thread(from_glb, data)
            glb = await asyncio.to_thread(to_glb, a, pid)
        except Exception as e:  # noqa: BLE001 - a prop that fails is just left out
            notes.append(f"{pid}: {type(e).__name__}: {e}")
            continue
        key = job_key(inp.job_id, f"props/{pid}.glb")
        await asyncio.to_thread(store.put, key, glb, "model/gltf-binary")
        props.append(DressProp(id=pid, name=c.name, key=key,
                               credit=library.licence.credit(c.name, c.author, c.license, c.url, "polyhaven")))
    return ScenePropsOutput(props=props, notes=notes)


@activity.defn(name=SceneActivity.LIBRARY_FETCH.value)
async def library_fetch(inp: LibraryFetchInput) -> MeshStageOutput:
    """Download one candidate (HTTPS, public hosts, size cap, GLB only), parse it with our reader and re-encode it
    with our writer, so only geometry, colours and a skin we understood reach later stages."""
    c, plan = inp.candidate, inp.plan
    art = MeshArtifact(entity=plan.entity, source="library", asset=asset_for_kind(plan.kind),
                       attribution=c.attribution())
    try:
        _heartbeat(f"downloading {c.source}/{c.id}")
        data = await library.fetch_candidate(c)
        library.check_glb(data)
        a = await asyncio.to_thread(from_glb, data)
    except Exception as e:  # noqa: BLE001 - a bad candidate is an evaluation failure; the next one is tried
        return MeshStageOutput(artifact=art, eval=stage_eval("library", inp.attempt, [CheckResult(
            name="library.fetch", passed=False, detail=f"{c.source}/{c.id}: {type(e).__name__}: {e}")]))
    key = stage_key(inp.job_id, plan.entity, f"library-{c.source}", inp.attempt)
    await save_asset(a, key, plan.entity)
    rigged = f", rigged ({len(a.bones)} bones, {len(a.clips)} clips)" if a.bones else ""
    return MeshStageOutput(artifact=describe(a, art, glb_key=key), eval=stage_eval("library", inp.attempt, [
        CheckResult(name="library.fetch", passed=True,
                    detail=f"{c.source}/{c.id} {c.name!r} by {c.author or 'unknown'} ({c.license}), "
                           f"{len(data) / 1e6:.1f} MB, {a.n_tris} triangles{rigged}")]))


CLIP_WORDS = {"idle": ("idle", "breath", "stand", "rest"),
              "locomotion": ("walk", "run", "trot", "gallop", "jog", "move", "locomotion", "fly", "swim"),
              "flex": ("flex", "bend", "wave", "wag", "flap")}


def match_clips(clips: list[Clip], plans: list[ClipPlan]) -> list[tuple[Clip, ClipPlan]]:
    """Pair the model's own clips with planned clips: by name, then by the planned kind's keywords."""
    out, used = [], set()
    for cp in plans:
        def score(c: Clip, cp=cp) -> float:
            n = c.name.lower()
            if cp.name.lower() in n:
                return 2.0
            if any(w in n for w in CLIP_WORDS.get(cp.kind, ())):
                return 1.5
            return difflib.SequenceMatcher(None, cp.name.lower(), n).ratio()
        best = max((c for c in clips if id(c) not in used), key=score, default=None)
        if best is not None and score(best) >= 0.6:
            used.add(id(best))
            out.append((best, cp))
    return out


@activity.defn(name=SceneActivity.LIBRARY_RIG.value)
async def library_rig(inp: MeshStageInput) -> MeshStageOutput:
    """A library model that arrived rigged keeps its skeleton if it passes the ML-rig checks; its own clips that
    match planned clips are kept (root motion removed: the solver owns position) if they pass the clip checks.
    A failing skin is stripped, so the workflow can rig the mesh another way."""
    a = await load_asset(inp.artifact.glb_key)
    ev = await judge_rig(a, inp.plan, inp.attempt)
    if not ev.passed:
        a.bones, a.joints, a.weights, a.clips = [], None, None, []
        key = stage_key(inp.job_id, inp.plan.entity, "library-unrigged", inp.attempt)
        await save_asset(a, key, inp.plan.entity)
        return MeshStageOutput(artifact=describe(a, inp.artifact, glb_key=key, clips=[]), eval=ev)
    pairs = match_clips(a.clips, inp.plan.clips)
    a.clips = [Clip(name=cp.name, times=c.times - c.times[0], rotations=c.rotations) for c, cp in pairs]
    clip_eval = await judge_clips(a, inp.plan, inp.attempt) if a.clips else None
    kept: list[ClipPlan] = []
    if clip_eval is not None:
        bad = {c.name.split(".")[0] for c in clip_eval.checks if not c.passed}
        kept = [cp for _, cp in pairs if cp.name not in bad]
        a.clips = [c for c in a.clips if c.name not in bad]
        ev = ev.model_copy(update={"checks": ev.checks + clip_eval.checks})
    key = stage_key(inp.job_id, inp.plan.entity, "library-rigged", inp.attempt)
    await save_asset(a, key, inp.plan.entity)
    return MeshStageOutput(artifact=describe(a, inp.artifact, glb_key=key, clips=kept),
                           eval=ev.model_copy(update={"passed": True}))


# --- CAD route ------------------------------------------------------------------------------------------

@activity.defn(name=SceneActivity.CAD_WRITE.value)
async def cad_write(inp: CadWriteInput) -> CadWriteOutput:
    """Only WRITES the code: it runs in the CAD sandbox runner (run_cad), never in this worker."""
    return await cad.write(llm, inp)


@activity.defn(name=SceneActivity.CAD_EVALUATE.value)
async def cad_evaluate(inp: CadEvaluateInput) -> MeshStageOutput:
    """Judge one CAD attempt: the sandbox run, parts, interference at rest and across every joint's range, the mesh,
    the joint-driven rig and its kinematic clips, then (only if all pass) the vision critic."""
    plan, run = inp.plan, inp.run
    art = MeshArtifact(entity=plan.entity, source="cad", asset=asset_for_kind(plan.kind), step_key=run.step_key or "")
    if not run.ok or not run.result_key:
        err = (run.stderr or "no output").strip()
        return MeshStageOutput(artifact=art, eval=stage_eval("cad", inp.attempt, [CheckResult(
            name="cad.run", passed=False, detail=f"the script failed in the CAD sandbox: {err[-1500:]}")]))
    try:
        report, meshes = cad.load_result(await asyncio.to_thread(get_store().get, run.result_key))
        a = await asyncio.to_thread(cad.build_asset, report, meshes)
    except Exception as e:  # noqa: BLE001
        return MeshStageOutput(artifact=art, eval=stage_eval("cad", inp.attempt, [CheckResult(
            name="cad.output", passed=False, detail=f"unreadable CAD output: {type(e).__name__}: {e}")]))
    clips, notes = cad.kinematic_clips(report, plan.clips)
    a.clips = [c for c, _, _ in clips]
    checks = [CheckResult(name="cad.run", passed=True, detail=f"ran in {run.duration_s:.1f} s")]
    checks += await asyncio.to_thread(cad.evaluate, report, a, plan, inp.parts, clips)
    if notes:
        checks.append(CheckResult(name="cad.notes", passed=True, detail="; ".join(notes)))
    critique = ""
    if all(c.passed for c in checks):
        _heartbeat("rendering")
        sheet = await asyncio.to_thread(render.turntable, a)
        c, critique = await critic.critique(llm, "cad", sheet, _what(plan))
        checks.append(c)
    key = stage_key(inp.job_id, plan.entity, "cad", inp.attempt)
    await save_asset(a, key, plan.entity)
    return MeshStageOutput(artifact=describe(a, art, glb_key=key, clips=[cp for _, cp, _ in clips]),
                           eval=stage_eval("cad", inp.attempt, checks, critique if critique else ""))


@activity.defn(name=SceneActivity.CAD_SKIN.value)
async def cad_skin_mesh(inp: CadSkinInput) -> MeshStageOutput:
    """Fit the generated surface onto the CAD model and judge it; on failure the workflow keeps the plain CAD model."""
    gen, model = await load_asset(inp.generated.glb_key), await load_asset(inp.cad.glb_key)
    a, checks = await asyncio.to_thread(cad_skin.apply_skin, gen, model)
    if a is None or not all(c.passed for c in checks):
        return MeshStageOutput(artifact=inp.cad, eval=stage_eval("cad_skin", inp.attempt, checks))
    _heartbeat("rendering")
    sheet = await asyncio.to_thread(render.turntable, a)
    c, critique = await critic.critique(llm, "generate", sheet, _what(inp.plan))
    checks.append(c)
    if not c.passed:
        return MeshStageOutput(artifact=inp.cad, eval=stage_eval("cad_skin", inp.attempt, checks, critique))
    key = stage_key(inp.job_id, inp.plan.entity, "cad-skin", inp.attempt)
    await save_asset(a, key, inp.plan.entity)
    return MeshStageOutput(artifact=describe(a, inp.cad, glb_key=key), eval=stage_eval("cad_skin", inp.attempt, checks))


async def cache_put(plan: AssetPlan, art: MeshArtifact) -> MeshArtifact:
    """Copy a finished generated asset into the cross-job cache and return the cached artifact."""
    store = get_store()
    base = cache_key(plan)
    data = await asyncio.to_thread(store.get, art.glb_key)
    cached = art.model_copy(update={"glb_key": f"{base}/asset.glb"})
    await asyncio.to_thread(store.put, cached.glb_key, data, "model/gltf-binary")
    if art.step_key and await asyncio.to_thread(store.exists, art.step_key):  # CAD: the STEP download too
        cached.step_key = f"{base}/model.step"
        await asyncio.to_thread(store.put, cached.step_key, await asyncio.to_thread(store.get, art.step_key),
                                "model/step")
    await asyncio.to_thread(store.put, f"{base}/meta.json", cached.model_dump_json().encode(), "application/json")
    return cached


@activity.defn(name=SceneActivity.CACHE.value)
async def cache_asset(inp: MeshStageInput) -> MeshArtifact:
    return await cache_put(inp.plan, inp.artifact)


async def route_stats_text(kinds: list[str]) -> str:
    """Past route outcomes for these kinds, for the router prompt; "" without a database or on any error."""
    if not os.environ.get("RAG_DATABASE_URL"):
        return ""
    try:
        from omnific_rag import route_stats
        return route_stats.render(await route_stats.summary(kinds))
    except Exception as e:  # noqa: BLE001 - stats only inform routing
        log.warning("route stats unavailable: %s", e)
        return ""


@activity.defn(name=SceneActivity.ROUTE_STATS.value)
async def record_route_stats(inp: RouteStatsInput) -> int:
    if not os.environ.get("RAG_DATABASE_URL"):
        return 0
    try:
        from omnific_rag import route_stats
        return await route_stats.record(inp.job_id, [(o.kind, o.route, o.passed, o.attempts, o.failed_checks)
                                                     for o in inp.outcomes])
    except Exception as e:  # noqa: BLE001 - best-effort bookkeeping never fails a scene
        log.warning("could not record route stats: %s", e)
        return 0


SCENE_ACTIVITIES = [plan_assets, procedural_mesh, postprocess_mesh, physics_rig, procedural_animate, evaluate_stage,
                    assemble_scene, cache_asset, record_route_stats, library_search, library_fetch, library_rig,
                    cad_write, cad_evaluate, cad_skin_mesh, check_scene, asset_choices, scenery_props]
