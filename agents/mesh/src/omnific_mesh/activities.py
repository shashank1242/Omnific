"""Mesh worker (CPU): the MVP `build_scene` plus the SceneBuild (mesh v2) stages that don't need a GPU.

Each stage writes its GLB to `jobs/<job>/assets/<entity>/<stage>-<attempt>.glb` and returns a MeshArtifact plus
its StageEval. GPU stage outputs (generate, ML rig, ML animate) are judged by `evaluate_stage` here.
"""

import asyncio
import hashlib
import json
import logging
import os

import numpy as np
from omnific_common.contracts import (
    ACTIVITY,
    AssembleInput,
    AssembleOutput,
    AssetPlan,
    CheckResult,
    EvaluateStageInput,
    MeshArtifact,
    MeshStageInput,
    MeshStageOutput,
    PlanAssetsInput,
    PlanAssetsOutput,
    SceneActivity,
    SceneInput,
    SceneOutput,
    StageEval,
    TaskQueue,
)
from omnific_common.llm import LLM
from omnific_common.storage import job_key, store_from_env
from temporalio import activity

from . import anim, critic, meshops, render, rig
from .asset import Asset, from_glb, to_glb
from .assets import asset_for_kind
from .gltf import build_gltf, dumps, rest_positions
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
    payload = json.dumps({"kind": plan.kind, "prompt": plan.gen_prompt, "rig": plan.rig,
                          "clips": [c.model_dump() for c in plan.clips], "budget": plan.poly_budget,
                          "models": model_versions()}, sort_keys=True)
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
    plans, ev = await make_plan(llm, inp.spec, inp.prompt, inp.backend)
    cached = []
    store = get_store()
    for p in plans:
        if p.mode != "generated":
            continue
        meta = f"{cache_key(p)}/meta.json"
        if await asyncio.to_thread(store.exists, meta):
            cached.append(MeshArtifact.model_validate_json(await asyncio.to_thread(store.get, meta)))
    return PlanAssetsOutput(assets=plans, eval=ev, cached=cached)


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
    try:
        clean, notes = await asyncio.to_thread(meshops.postprocess, Asset(**vars(raw)), size, inp.plan.poly_budget)
    except Exception as e:  # noqa: BLE001 - a broken mesh is an evaluation failure, not a crash
        return MeshStageOutput(artifact=inp.artifact, eval=stage_eval("postprocess", inp.attempt, [CheckResult(
            name="post.run", passed=False, detail=f"{type(e).__name__}: {e}")]))
    checks = await asyncio.to_thread(meshops.evaluate_postprocessed, raw, clean, size, inp.plan.poly_budget)
    key = stage_key(inp.job_id, inp.plan.entity, "post", inp.attempt)
    await save_asset(clean, key, inp.plan.entity)
    if notes:
        checks.append(CheckResult(name="post.notes", passed=True, detail="; ".join(notes)))
    return MeshStageOutput(artifact=describe(clean, inp.artifact, glb_key=key),
                           eval=stage_eval("postprocess", inp.attempt, checks))


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
    what = f"{inp.plan.kind}: {inp.plan.gen_prompt}"
    if inp.stage == "generate":
        checks = await asyncio.to_thread(meshops.evaluate_generated, a)
        if all(c.passed for c in checks):
            _heartbeat("rendering")
            sheet = await asyncio.to_thread(render.turntable, a)
            c, crit = await critic.critique(llm, "generate", sheet, what)
            checks.append(c)
            return stage_eval("generate", inp.attempt, checks, crit if not c.passed else "")
        return stage_eval("generate", inp.attempt, checks)
    if inp.stage == "ml_rig":
        checks = rig.evaluate_skin(a)
        if all(c.passed for c in checks):
            checks += await asyncio.to_thread(rig.evaluate_deformation, a)
        if all(c.passed for c in checks):
            _heartbeat("rendering")
            sheet = await asyncio.to_thread(_posed_sheet, a)
            c, crit = await critic.critique(llm, "ml_rig", sheet, what)
            checks.append(c)
            return stage_eval("ml_rig", inp.attempt, checks, crit if not c.passed else "")
        return stage_eval("ml_rig", inp.attempt, checks)
    if inp.stage == "animate":
        checks: list[CheckResult] = []
        kinds = {c.name: c for c in inp.plan.clips}
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
                c, cr = await critic.critique(llm, "animate", sheet, what, motion)
                checks.append(c.model_copy(update={"name": f"{clip.name}.{c.name}"}))
                crit = crit or cr if not c.passed else crit
        return stage_eval("animate", inp.attempt, checks, crit)
    return stage_eval(inp.stage, inp.attempt, [CheckResult(name="stage", passed=False,
                                                            detail=f"no evaluator for stage {inp.stage!r}")])


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
    gltf, assets = await asyncio.to_thread(assemble, inp.spec, inp.artifacts, store.get)
    data = scene_dumps(gltf)
    checks = evaluate_scene(inp.spec, gltf, len(data))
    key = job_key(inp.job_id, "scene.gltf")
    await asyncio.to_thread(store.put, key, data, "model/gltf+json")
    return AssembleOutput(scene_key=key, entity_assets=assets, eval=stage_eval("assemble", 0, checks))


async def cache_put(plan: AssetPlan, art: MeshArtifact) -> MeshArtifact:
    """Copy a finished generated asset into the cross-job cache and return the cached artifact."""
    store = get_store()
    base = cache_key(plan)
    data = await asyncio.to_thread(store.get, art.glb_key)
    cached = art.model_copy(update={"glb_key": f"{base}/asset.glb"})
    await asyncio.to_thread(store.put, cached.glb_key, data, "model/gltf-binary")
    await asyncio.to_thread(store.put, f"{base}/meta.json", cached.model_dump_json().encode(), "application/json")
    return cached


@activity.defn(name=SceneActivity.CACHE.value)
async def cache_asset(inp: MeshStageInput) -> MeshArtifact:
    return await cache_put(inp.plan, inp.artifact)


SCENE_ACTIVITIES = [plan_assets, procedural_mesh, postprocess_mesh, physics_rig, procedural_animate, evaluate_stage,
                    assemble_scene, cache_asset]
