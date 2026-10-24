from omnific_common.contracts import ACTIVITY, SceneInput, SceneOutput, TaskQueue
from omnific_common.storage import job_key, store_from_env
from temporalio import activity

from .assets import asset_for_kind
from .gltf import build_gltf, dumps, rest_positions

get_store = store_from_env


@activity.defn(name=ACTIVITY[TaskQueue.MESH])
async def build_scene(inp: SceneInput) -> SceneOutput:
    ents = [(e.name, asset_for_kind(e.kind), list(e.bound_state)) for e in inp.spec.entities]
    gltf, assets = build_gltf(ents, rest_positions(inp.spec.entities))
    key = job_key(inp.job_id, "scene.gltf")
    get_store().put(key, dumps(gltf), "model/gltf+json")
    return SceneOutput(scene_key=key, entity_assets=assets)
