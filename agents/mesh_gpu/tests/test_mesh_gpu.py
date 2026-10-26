"""GPU worker with fake model backends, and a full SceneBuild run through the REAL CPU stages and evaluators."""

import json
import uuid

import numpy as np
import pytest
from omnific_common.contracts import (
    AssetPlan,
    ClipPlan,
    MeshArtifact,
    MeshStageInput,
    SceneBuildInput,
    SceneOutput,
    StageEval,
    TaskQueue,
)
from omnific_common.storage import LocalStore
from omnific_mesh import activities as cpu
from omnific_mesh.asset import from_glb
from omnific_mesh_gpu import activities as gpu
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, StateVariable, TimeSpan
from temporalio.exceptions import ApplicationError

SPEC = SimSpec(title="dog", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
               parameters=[], equations=[], time=TimeSpan(end=1.0),
               state=[StateVariable(name="v", unit="m/s", initial_value=0.0, description="speed")],
               entities=[Entity(name="dog", kind="dog", bound_state=["v"]), Entity(name="floor", kind="ground plane")])
DOG = AssetPlan(entity="dog", kind="dog", mode="generated", gen_prompt="a beagle", rig="ml",
                clips=[ClipPlan(name="walk", kind="locomotion", description="trot", drive_state="v")],
                poly_budget=4000)


class FakeLLM:
    """Vision critic that always approves; image generation is never reached with MESH_GEN_BACKEND=fake."""

    calls = 0

    async def structured_images(self, role, instructions, text, images, schema):
        FakeLLM.calls += 1
        return schema(matches=True, score=8, defects=[], critique="")

    async def generate_image(self, prompt, size="1024x1024"):
        raise AssertionError("no image generation with the fake generator")

    async def structured(self, role, instructions, input, schema):
        from omnific_common.contracts import AssetPlanOutput
        assert schema is AssetPlanOutput
        floor = AssetPlan(entity="floor", kind="ground plane", mode="procedural", gen_prompt="", rig="none",
                          clips=[], poly_budget=0)
        return AssetPlanOutput(assets=[DOG, floor])


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = LocalStore(tmp_path)
    for mod in (cpu, gpu):
        monkeypatch.setattr(mod, "get_store", lambda: s)
        monkeypatch.setattr(mod, "llm", FakeLLM())
    for k in ("MESH_GEN_BACKEND", "MESH_RIG_BACKEND", "MESH_ANIM_BACKEND"):
        monkeypatch.setenv(k, "fake")
    return s


def stage(art=None, attempt=0):
    return MeshStageInput(job_id="j", plan=DOG, spec=SPEC, artifact=art, attempt=attempt)


async def test_gpu_stages_with_fakes_pass_the_real_evaluators(store):
    from omnific_common.contracts import EvaluateStageInput

    async def judged(stage_name, art):
        return await cpu.evaluate_stage(EvaluateStageInput(job_id="j", stage=stage_name, plan=DOG, artifact=art))

    raw = await gpu.generate_mesh(stage())
    assert raw.source == "generated" and store.exists(raw.glb_key) and raw.n_tris > 100
    assert store.exists("jobs/j/assets/dog/ref-0.png")
    ev: StageEval = await judged("generate", raw)
    assert ev.passed, ev
    post = await cpu.postprocess_mesh(stage(raw))
    assert post.eval.passed, post.eval
    rigged = await gpu.ml_rig(stage(post.artifact))
    assert rigged.bones[0] == "root" and len(rigged.bones) == 5
    ev = await judged("ml_rig", rigged)
    assert ev.passed, [c for c in ev.checks if not c.passed]
    animated = await gpu.ml_animate(stage(rigged))
    assert [c.name for c in animated.clips] == ["walk"]
    assert all(c.root_translation is None for c in from_glb(store.get(animated.glb_key)).clips)
    ev = await judged("animate", animated)
    assert ev.passed, [c for c in ev.checks if not c.passed]


async def test_disabled_backend_is_stage_unavailable(store, monkeypatch):
    monkeypatch.setenv("MESH_ANIM_BACKEND", "none")
    rigged = await gpu.ml_rig(stage((await cpu.postprocess_mesh(stage(await gpu.generate_mesh(stage())))).artifact))
    with pytest.raises(ApplicationError) as e:
        await gpu.ml_animate(stage(rigged))
    assert e.value.type == "StageUnavailable" and e.value.non_retryable


async def test_scene_build_end_to_end_with_fake_models(store):
    from omnific_workflows.scene import SceneBuild
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    try:
        env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot start Temporal test server: {exc}")
    async with env:
        workers = [Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[SceneBuild]),
                   Worker(env.client, task_queue=TaskQueue.MESH.value, activities=cpu.SCENE_ACTIVITIES),
                   Worker(env.client, task_queue=TaskQueue.MESH_GPU.value, activities=gpu.GPU_ACTIVITIES,
                          max_concurrent_activities=1)]
        for w in workers:
            await w.__aenter__()
        try:
            out = await env.client.execute_workflow(
                "SceneBuild", SceneBuildInput(job_id="j", prompt="a dog trots", spec=SPEC, backend="generated"),
                id=uuid.uuid4().hex, task_queue=TaskQueue.WORKFLOW.value, result_type=SceneOutput)
            # second job, same plan: served from the cache, no GPU work
            out2 = await env.client.execute_workflow(
                "SceneBuild", SceneBuildInput(job_id="j2", prompt="a dog trots", spec=SPEC, backend="generated"),
                id=uuid.uuid4().hex, task_queue=TaskQueue.WORKFLOW.value, result_type=SceneOutput)
        finally:
            for w in reversed(workers):
                await w.__aexit__(None, None, None)

    dog = next(r for r in out.report.entities if r.entity == "dog")
    assert dog.final == "generated_rigged", dog
    assert [e.stage for e in dog.evals] == ["generate", "postprocess", "ml_rig", "animate"]
    assert all(c.passed for c in out.report.scene_checks), out.report.scene_checks
    gltf = json.loads(store.get(out.scene_key))
    names = [n["name"] for n in gltf["nodes"]]
    assert {"dog", "floor", "mesh:dog"} <= set(names) and any(n.startswith("bone:dog:") for n in names)
    assert [a["name"] for a in gltf["animations"]] == ["dog:walk"]
    clips = gltf["nodes"][names.index("dog")]["extras"]["omnific"]["clips"]
    assert clips == [{"name": "walk", "kind": "locomotion", "drive_state": "v"}]
    dog2 = next(r for r in out2.report.entities if r.entity == "dog")
    assert dog2.attempts == 0 and "cached" in " ".join(dog2.notes) and dog2.final == "generated_rigged"
    assert np.isfinite(np.array(gltf["nodes"][names.index("dog")]["translation"])).all()


def test_cache_entries_are_reusable(store):
    # meta.json written by the CPU cache stage round-trips into MeshArtifact
    meta = MeshArtifact(entity="dog", source="generated", asset="box", glb_key="assets/x/asset.glb")
    store.put("assets/x/meta.json", meta.model_dump_json().encode())
    assert MeshArtifact.model_validate_json(store.get("assets/x/meta.json")) == meta
