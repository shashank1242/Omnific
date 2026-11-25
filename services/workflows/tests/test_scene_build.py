import uuid

import pytest
from omnific_common.contracts import (
    AssembleOutput,
    AssetPlan,
    CheckResult,
    ClipPlan,
    MeshArtifact,
    MeshCapabilities,
    MeshStageOutput,
    PlanAssetsOutput,
    SceneActivity,
    SceneBuildInput,
    SceneOutput,
    StageEval,
    TaskQueue,
)
from omnific_common.temporal import workflow_runner
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, TimeSpan
from omnific_workflows.scene import SceneBuild
from temporalio import activity
from temporalio.worker import Worker
from test_simulation import env  # noqa: F401

SPEC = SimSpec(title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
               parameters=[], state=[], equations=[], time=TimeSpan(end=1.0),
               entities=[Entity(name="dog", kind="dog"), Entity(name="bob", kind="ball", pivot=[0, 0, 0],
                                                                 arm=[0, -1, 0])])
DOG = AssetPlan(entity="dog", kind="dog", routes=["generated", "procedural"],
                route_reason="", search_query="", cad_brief="", gen_prompt="a beagle", rig="ml",
                clips=[ClipPlan(name="walk", kind="locomotion", description="trot", drive_state="")],
                poly_budget=5000)
BOB = AssetPlan(entity="bob", kind="ball", routes=["procedural"],
                route_reason="", search_query="", cad_brief="", gen_prompt="", rig="physics", clips=[], poly_budget=0)


def ev(stage, ok, attempt=0, critique=""):
    return StageEval(stage=stage, attempt=attempt, passed=ok,
                     checks=[CheckResult(name=f"{stage}.x", passed=ok, detail="d")], critique=critique)


class Script:
    """Scripted stage outcomes; records every call."""

    def __init__(self, gen_evals=(True,), post_ok=True, rig_evals=(True,), anim_evals=(True,), proc_clips=True,
                 cached=(), assemble_ok=(True,), gpu=True):
        self.gen_evals, self.rig_evals, self.anim_evals = list(gen_evals), list(rig_evals), list(anim_evals)
        self.post_ok, self.proc_clips, self.cached = post_ok, proc_clips, list(cached)
        self.assemble_ok, self.gpu = list(assemble_ok), gpu
        self.calls: list[tuple[str, dict]] = []

    def names(self, prefix=""):
        return [n for n, _ in self.calls if n.startswith(prefix)]

    def activities(self):
        s = self

        def art(inp, **upd):
            base = inp.get("artifact") or {"entity": inp["plan"]["entity"], "source": "generated", "asset": "box"}
            return MeshArtifact.model_validate({**base, **upd})

        @activity.defn(name=SceneActivity.PLAN.value)
        async def plan(inp: dict) -> PlanAssetsOutput:
            s.calls.append(("plan", inp))
            return PlanAssetsOutput(assets=[DOG, BOB], eval=ev("plan", True), cached=s.cached,
                                    capabilities=MeshCapabilities(gpu=s.gpu))

        @activity.defn(name=SceneActivity.GENERATE.value)
        async def generate(inp: dict) -> MeshArtifact:
            s.calls.append(("gpu.generate", inp))
            return art(inp, glb_key=f"raw-{inp['attempt']}")

        @activity.defn(name=SceneActivity.ML_RIG.value)
        async def ml_rig(inp: dict) -> MeshArtifact:
            s.calls.append(("gpu.rig", inp))
            return art(inp, glb_key="rigged", bones=["root", "leg"])

        @activity.defn(name=SceneActivity.ML_ANIMATE.value)
        async def ml_animate(inp: dict) -> MeshArtifact:
            s.calls.append(("gpu.animate", inp))
            return art(inp, glb_key="animated", clips=[c for c in inp["plan"]["clips"]])

        @activity.defn(name=SceneActivity.EVALUATE.value)
        async def evaluate(inp: dict) -> StageEval:
            s.calls.append((f"eval.{inp['stage']}", inp))
            seq = {"generate": s.gen_evals, "ml_rig": s.rig_evals, "animate": s.anim_evals}[inp["stage"]]
            ok = seq.pop(0) if len(seq) > 1 else seq[0]
            return ev(inp["stage"], ok, inp["attempt"], critique="" if ok else f"fix {inp['stage']}")

        @activity.defn(name=SceneActivity.POSTPROCESS.value)
        async def post(inp: dict) -> MeshStageOutput:
            s.calls.append(("post", inp))
            return MeshStageOutput(artifact=art(inp, glb_key="clean"), eval=ev("postprocess", s.post_ok))

        @activity.defn(name=SceneActivity.PROCEDURAL.value)
        async def procedural(inp: dict) -> MeshStageOutput:
            s.calls.append(("procedural", inp))
            a = MeshArtifact(entity=inp["plan"]["entity"], source="procedural", asset="sphere")
            return MeshStageOutput(artifact=a, eval=ev("generate", True))

        @activity.defn(name=SceneActivity.PHYSICS_RIG.value)
        async def physics(inp: dict) -> MeshStageOutput:
            s.calls.append(("physics", inp))
            return MeshStageOutput(artifact=art(inp, joint=[0, 0, 0, 0, -10, 0]), eval=ev("physics_rig", True))

        @activity.defn(name=SceneActivity.PROCEDURAL_ANIMATE.value)
        async def proc_anim(inp: dict) -> MeshStageOutput:
            s.calls.append(("proc_anim", inp))
            clips = inp["plan"]["clips"] if s.proc_clips else []
            return MeshStageOutput(artifact=art(inp, clips=clips), eval=ev("animate", s.proc_clips))

        @activity.defn(name=SceneActivity.CACHE.value)
        async def cache(inp: dict) -> MeshArtifact:
            s.calls.append(("cache", inp))
            return art(inp, glb_key="assets/abc/asset.glb")

        @activity.defn(name=SceneActivity.ASSEMBLE.value)
        async def assemble(inp: dict) -> AssembleOutput:
            s.calls.append(("assemble", inp))
            ok = s.assemble_ok.pop(0) if len(s.assemble_ok) > 1 else s.assemble_ok[0]
            return AssembleOutput(scene_key="scene.gltf", entity_assets={}, eval=ev("assemble", ok))

        @activity.defn(name=SceneActivity.ROUTE_STATS.value)
        async def route_stats(inp: dict) -> int:
            s.calls.append(("route_stats", inp))
            return len(inp["outcomes"])

        cpu = [plan, evaluate, post, procedural, physics, proc_anim, cache, assemble, route_stats]
        return cpu, ([generate, ml_rig, ml_animate] if s.gpu else [])


async def build(env, script, max_attempts=3, gpu_wait_minutes=15.0) -> SceneOutput:  # noqa: F811
    cpu, gpu = script.activities()
    workers = [Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[SceneBuild],
                      workflow_runner=workflow_runner()),
               Worker(env.client, task_queue=TaskQueue.MESH.value, activities=cpu)]
    if gpu:
        workers.append(Worker(env.client, task_queue=TaskQueue.MESH_GPU.value, activities=gpu))
    for w in workers:
        await w.__aenter__()
    try:
        return await env.client.execute_workflow(
            "SceneBuild", SceneBuildInput(job_id="j", prompt="p", spec=SPEC, backend="generated",
                                          max_attempts=max_attempts, gpu_wait_minutes=gpu_wait_minutes),
            id=uuid.uuid4().hex, task_queue=TaskQueue.WORKFLOW.value, result_type=SceneOutput)
    finally:
        for w in reversed(workers):
            await w.__aexit__(None, None, None)


def report(out, entity):
    return next(r for r in out.report.entities if r.entity == entity)


async def test_full_chain_with_retry(env):  # noqa: F811
    s = Script(gen_evals=(False, True))
    out = await build(env, s)
    dog = report(out, "dog")
    assert dog.final == "generated_rigged"
    assert s.names("gpu.") == ["gpu.generate", "gpu.generate", "gpu.rig", "gpu.animate"]
    # the critique of the failed attempt reaches the second attempt
    gens = [i for n, i in s.calls if n == "gpu.generate"]
    assert gens[1]["critique"] == "fix generate" and gens[1]["attempt"] == 1
    assert [e.stage for e in dog.evals] == ["generate", "generate", "postprocess", "ml_rig", "animate"]
    assert dog.attempts == 4 and dog.routes_tried == ["generated"] and dog.route_used == "generated"
    bob = report(out, "bob")
    assert bob.final == "procedural" and ("physics", ) == tuple(n for n, i in s.calls
                                                                if n == "physics" and i["plan"]["entity"] == "bob")
    assert [n for n, i in s.calls if n == "cache"] == ["cache"]  # only the newly generated dog
    assembled = next(i for n, i in s.calls if n == "assemble")["artifacts"]
    assert {a["entity"]: a["glb_key"] for a in assembled}["dog"] == "animated"


async def test_generation_never_passes_falls_back_to_procedural(env):  # noqa: F811
    s = Script(gen_evals=(False,))
    out = await build(env, s, max_attempts=2)
    dog = report(out, "dog")
    assert dog.final == "procedural" and any("generation never passed" in n for n in dog.notes)
    assert s.names("gpu.") == ["gpu.generate", "gpu.generate"]
    assert "gpu.rig" not in s.names() and "cache" not in s.names()
    # the router's outcome is recorded: the generated route failed for kind "dog"
    assert dog.routes_tried == ["generated", "procedural"] and dog.route_used == "procedural"
    (stats,) = [i for n, i in s.calls if n == "route_stats"]
    assert [(o["kind"], o["route"], o["passed"], o["attempts"]) for o in stats["outcomes"]] == [
        ("dog", "generated", False, 2)]


async def test_postprocess_failure_regenerates(env):  # noqa: F811
    s = Script(post_ok=False)
    out = await build(env, s, max_attempts=2)
    assert report(out, "dog").final == "procedural"
    assert s.names("gpu.generate") == ["gpu.generate", "gpu.generate"]
    second = [i for n, i in s.calls if n == "gpu.generate"][1]
    assert "Avoid:" in second["plan"]["gen_prompt"]


async def test_rig_failure_keeps_unrigged_mesh(env):  # noqa: F811
    s = Script(rig_evals=(False,))
    out = await build(env, s, max_attempts=2)
    dog = report(out, "dog")
    assert dog.final == "generated" and "gpu.animate" not in s.names()
    assert any("unrigged" in n for n in dog.notes)


async def test_animation_falls_back_to_procedural_clips(env):  # noqa: F811
    s = Script(anim_evals=(False,))
    out = await build(env, s, max_attempts=2)
    dog = report(out, "dog")
    assert dog.final == "generated_rigged" and "proc_anim" in s.names()
    assembled = next(i for n, i in s.calls if n == "assemble")["artifacts"]
    assert [c["name"] for c in next(a for a in assembled if a["entity"] == "dog")["clips"]] == ["walk"]


async def test_cache_hit_skips_gpu(env):  # noqa: F811
    hit = MeshArtifact(entity="dog", source="generated", asset="box", glb_key="assets/abc/asset.glb", bones=["r"])
    s = Script(cached=[hit])
    out = await build(env, s)
    assert s.names("gpu.") == [] and "cache" not in s.names()
    assert report(out, "dog").final == "generated_rigged"


async def test_no_gpu_worker_falls_back(env):  # noqa: F811
    s = Script(gpu=False)
    out = await build(env, s, gpu_wait_minutes=0.05)  # the test server doesn't time-skip schedule-to-start
    dog = report(out, "dog")
    assert dog.final == "procedural" and dog.attempts == 1  # schedule-to-start timeout: no pointless retries
    assert "schedule" in dog.evals[0].checks[0].detail.lower() or "timeout" in dog.evals[0].checks[0].detail.lower()


async def test_broken_scene_falls_back_to_procedural(env):  # noqa: F811
    s = Script(assemble_ok=(False, True))
    out = await build(env, s)
    assert [n for n in s.names() if n == "assemble"] == ["assemble", "assemble"]
    second = [i for n, i in s.calls if n == "assemble"][1]["artifacts"]
    assert all(a["source"] == "procedural" for a in second)
    assert all(r.final == "procedural" for r in out.report.entities)
    assert any(c.name.startswith("fallback:") for c in out.report.scene_checks)


@pytest.mark.parametrize("name", list(SceneActivity))
def test_every_scene_activity_has_a_queue(name):
    from omnific_common.contracts import GPU_SCENE_ACTIVITIES

    assert name.value  # CPU unless listed as GPU; the workflow routes on this set
    assert (name in GPU_SCENE_ACTIVITIES) == (name.value in {"generate_mesh", "ml_rig", "ml_animate"})


async def test_an_unrigged_cache_hit_is_rigged_and_cached_again(env):  # noqa: F811
    # cached before its rig passed: the mesh is reused (no new generation), the rig is retried, and the rigged result
    # replaces the cache entry
    hit = MeshArtifact(entity="dog", source="generated", asset="box", glb_key="assets/abc/asset.glb")
    s = Script(cached=[hit])
    out = await build(env, s)
    assert "gpu.generate" not in s.names() and s.names("gpu.") == ["gpu.rig", "gpu.animate"]
    assert "cache" in s.names() and report(out, "dog").final == "generated_rigged"
