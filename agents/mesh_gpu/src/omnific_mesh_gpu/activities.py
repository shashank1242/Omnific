"""GPU worker activities for SceneBuild: generate (OpenAI reference image -> TRELLIS.2), ML rig, ML animate.

Outputs are judged by the CPU worker's `evaluate_stage`; these activities only produce. A stage with no backend
raises a non-retryable StageUnavailable so the workflow falls back immediately.
"""

import asyncio
import os

from omnific_common.contracts import MeshArtifact, MeshStageInput, SceneActivity
from omnific_common.llm import LLM
from omnific_common.storage import job_key, store_from_env
from omnific_mesh.asset import from_glb, png, to_glb
from omnific_mesh.assets import asset_for_kind
from temporalio import activity
from temporalio.exceptions import ApplicationError

from . import backends

get_store = store_from_env
llm = LLM()

TEXTURE_SIZE = 1024

IMAGE_PROMPT = ("{what}. A single object, centred and fully in frame, three-quarter view, neutral pose, plain white "
                "background, soft even lighting, no text, no shadow, no other objects.")


def _heartbeat(detail: str) -> None:
    try:
        activity.heartbeat(detail)
    except RuntimeError:  # unit tests
        pass


def _key(inp: MeshStageInput, name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in inp.plan.entity)[:60]
    return job_key(inp.job_id, f"assets/{safe}/{name}")


async def _keep_alive(coro):
    """Heartbeat every 30 s while a long model call runs (the workflow's heartbeat timeout is 2 min)."""
    task = asyncio.ensure_future(coro)
    while True:
        done, _ = await asyncio.wait({task}, timeout=30)
        if done:
            return task.result()
        _heartbeat("working")


def _unavailable(e: Exception):
    return ApplicationError(str(e), type="StageUnavailable", non_retryable=True)


def _describe(data: bytes, art: MeshArtifact, **upd) -> MeshArtifact:
    a = from_glb(data)
    lo, hi = a.bbox()
    return art.model_copy(update={"n_tris": a.n_tris, "bbox_min": lo.astype(float).tolist(),
                                  "bbox_max": hi.astype(float).tolist(), "bones": [b.name for b in a.bones], **upd})


async def reference_image(inp: MeshStageInput) -> bytes:
    if os.environ.get("MESH_GEN_BACKEND") == "fake":  # the fake generator ignores the image: don't pay for one
        from PIL import Image

        return png(Image.new("RGB", (64, 64), (255, 255, 255)))
    what = inp.plan.gen_prompt
    if inp.critique:
        what += f". Fix these problems from the previous attempt: {inp.critique}"
    return await llm.generate_image(IMAGE_PROMPT.format(what=what))


@activity.defn(name=SceneActivity.GENERATE.value)
async def generate_mesh(inp: MeshStageInput) -> MeshArtifact:
    store = get_store()
    image = await reference_image(inp)
    await asyncio.to_thread(store.put, _key(inp, f"ref-{inp.attempt}.png"), image, "image/png")
    _heartbeat("reference image")
    try:
        glb = await _keep_alive(backends.generator().generate(
            image, seed=inp.attempt, decimation_target=max(inp.plan.poly_budget, 1000), texture_size=TEXTURE_SIZE))
    except backends.Unavailable as e:
        raise _unavailable(e) from e
    key = _key(inp, f"gen-{inp.attempt}.glb")
    await asyncio.to_thread(store.put, key, glb, "model/gltf-binary")
    return _describe(glb, MeshArtifact(entity=inp.plan.entity, source="generated",
                                       asset=asset_for_kind(inp.plan.kind)), glb_key=key)


@activity.defn(name=SceneActivity.ML_RIG.value)
async def ml_rig(inp: MeshStageInput) -> MeshArtifact:
    store = get_store()
    src = await asyncio.to_thread(store.get, inp.artifact.glb_key)
    try:
        rigged = await _keep_alive(backends.rigger().rig(src))
    except backends.Unavailable as e:
        raise _unavailable(e) from e
    # re-encode in our normalised form (one mesh, one skin, rest rotations identity)
    out = to_glb(from_glb(rigged), inp.plan.entity)
    key = _key(inp, f"rig-{inp.attempt}.glb")
    await asyncio.to_thread(store.put, key, out, "model/gltf-binary")
    return _describe(out, inp.artifact, glb_key=key)


@activity.defn(name=SceneActivity.ML_ANIMATE.value)
async def ml_animate(inp: MeshStageInput) -> MeshArtifact:
    store = get_store()
    src = await asyncio.to_thread(store.get, inp.artifact.glb_key)
    try:
        animated = await _keep_alive(backends.animator().animate(src, [c.model_dump() for c in inp.plan.clips]))
    except backends.Unavailable as e:
        raise _unavailable(e) from e
    a = from_glb(animated)
    wanted = {c.name for c in inp.plan.clips}
    a.clips = [c for c in a.clips if c.name in wanted]
    for c in a.clips:  # the solver owns position: zero any root translation the model produced
        c.root_translation = None
    out = to_glb(a, inp.plan.entity)
    key = _key(inp, f"anim-{inp.attempt}.glb")
    await asyncio.to_thread(store.put, key, out, "model/gltf-binary")
    names = {c.name for c in a.clips}
    return _describe(out, inp.artifact, glb_key=key, clips=[c for c in inp.plan.clips if c.name in names])


GPU_ACTIVITIES = [generate_mesh, ml_rig, ml_animate]


