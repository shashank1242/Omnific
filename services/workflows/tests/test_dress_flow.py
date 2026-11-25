"""SceneDress: write -> Blender run -> scene contract -> evaluate, retries with the critique, and the fallbacks."""

import uuid

from omnific_common.contracts import (
    BLENDER_RUN_ACTIVITY,
    BlenderRunOutput,
    CheckResult,
    DressActivity,
    DressProp,
    DressPropOption,
    DressScript,
    DressScripts,
    JobOutcome,
    SceneActivity,
    SceneDressInput,
    SceneDressOutput,
    ScenePropsOutput,
    StageEval,
    TaskQueue,
)
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, TimeSpan
from temporalio import activity
from test_simulation import Stubs, env, make_workers, review, start  # noqa: F401

SPEC = SimSpec(title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
               parameters=[], state=[], equations=[], time=TimeSpan(end=1.0),
               entities=[Entity(name="ball", kind="sphere")])


class Dress:
    """Scripted dress outcomes: `passes[i]` is attempt i's evaluation; `infra` makes every Blender run fail to start."""

    def __init__(self, passes=(True,), infra=False, export_ok=True, props=None):
        self.passes, self.infra, self.export_ok = list(passes), infra, export_ok
        self.props = props  # ids the writer places; None = the props service isn't running
        self.calls: list[tuple[str, dict]] = []

    def names(self):
        return [n for n, _ in self.calls]

    def activities(self):
        s = self

        @activity.defn(name=DressActivity.WRITE.value)
        async def dress_write(inp: dict) -> DressScripts:
            s.calls.append(("write", inp))
            return DressScripts(scripts=[DressScript(agent="look", code=f"# attempt {inp['attempt']}", notes="")],
                                props=list(s.props or []))

        @activity.defn(name=BLENDER_RUN_ACTIVITY)
        async def run_blender(inp: dict) -> BlenderRunOutput:
            s.calls.append((f"run.{inp['mode']}", inp))
            if s.infra:
                return BlenderRunOutput(ok=False, stderr="Blender sandbox error: no image")
            if inp["mode"] == "export":
                return BlenderRunOutput(ok=s.export_ok, blend_key="jobs/j/dress/export/scene.blend",
                                        stderr="" if s.export_ok else "export boom")
            p = f"jobs/j/{inp['prefix']}"
            return BlenderRunOutput(ok=True, report_key=f"{p}/report.json", scene_key=f"{p}/scene.gltf",
                                    blend_key=f"{p}/scene.blend", still_keys=[f"{p}/stills/0.png"])

        @activity.defn(name=SceneActivity.CHECK_SCENE.value)
        async def check_scene(inp: dict) -> StageEval:
            s.calls.append(("check_scene", inp))
            return StageEval(stage="dress", attempt=0, passed=True,
                             checks=[CheckResult(name="scene.entities", passed=True, detail="ok")])

        @activity.defn(name=DressActivity.EVALUATE.value)
        async def dress_evaluate(inp: dict) -> StageEval:
            s.calls.append(("evaluate", inp))
            ok = s.passes[inp["attempt"]] if inp["attempt"] < len(s.passes) else False
            return StageEval(stage="dress", attempt=inp["attempt"], passed=ok, critique="" if ok else
                             f"the crate hides the ball ({inp['attempt']})",
                             checks=[CheckResult(name="dress.critic", passed=ok, detail="d")])

        @activity.defn(name=SceneActivity.SCENERY_PROPS.value)
        async def scenery_props(inp: dict) -> ScenePropsOutput:
            s.calls.append(("props.fetch" if inp["ids"] else "props.catalogue", inp))
            if not inp["ids"]:
                return ScenePropsOutput(options=[DressPropOption(id="park_bench", name="Park Bench", size=[1.5, 0.5, 0.9]),
                                                 DressPropOption(id="tree_01", name="Tree")])
            return ScenePropsOutput(props=[DressProp(id=i, name=i, key=f"jobs/j/props/{i}.glb", credit=f"{i} CC0")
                                           for i in inp["ids"] if i != "broken"],
                                    notes=["broken: failed"] if "broken" in inp["ids"] else [])

        mesh = [check_scene] + ([scenery_props] if s.props is not None else [])
        return {TaskQueue.DRESS: [dress_write, dress_evaluate], TaskQueue.SANDBOX: [run_blender],
                TaskQueue.MESH: mesh}


async def run_dress(env, dress: Dress, **kw) -> SceneDressOutput:  # noqa: F811
    async with make_workers(env, dress.activities()):
        return await env.client.execute_workflow(
            "SceneDress", SceneDressInput(job_id="j", prompt="a ball", spec=SPEC, playback_key="jobs/j/pb.json",
                                          scene_key="jobs/j/scene.gltf", **kw),
            id=f"dress-{uuid.uuid4().hex}", task_queue=TaskQueue.WORKFLOW.value, result_type=SceneDressOutput)


async def test_passes_first_time(env):  # noqa: F811
    d = Dress(passes=[True])
    out = await run_dress(env, d)
    assert out.report.dressed and out.report.attempts == 1
    assert out.scene_key == "jobs/j/dress/attempt-0/scene.gltf"
    assert out.blend_key == "jobs/j/dress/attempt-0/scene.blend"
    assert d.names() == ["write", "run.dress", "check_scene", "evaluate"]
    assert out.report.scripts[0].code == "# attempt 0"
    run = d.calls[1][1]
    assert run["entities"] == ["ball"] and run["scene_key"] == "jobs/j/scene.gltf"


async def test_retry_feeds_critique_and_previous_scripts_back(env):  # noqa: F811
    d = Dress(passes=[False, True])
    out = await run_dress(env, d)
    assert out.report.dressed and out.report.attempts == 2
    second = [inp for n, inp in d.calls if n == "write"][1]
    assert second["critique"] == "the crate hides the ball (0)"
    assert second["previous"][0]["code"] == "# attempt 0"
    assert [e.passed for e in out.report.evals] == [False, True]


async def test_never_passing_keeps_scenebuild_scene_and_still_exports_a_blend(env):  # noqa: F811
    d = Dress(passes=[False, False])
    out = await run_dress(env, d, max_attempts=2)
    assert not out.report.dressed and out.report.attempts == 2
    assert out.scene_key == "jobs/j/scene.gltf"
    assert out.blend_key == "jobs/j/dress/export/scene.blend"
    assert d.names()[-1] == "run.export"


async def test_unavailable_runner_stops_at_once(env):  # noqa: F811
    d = Dress(infra=True)
    out = await run_dress(env, d)
    assert not out.report.dressed and out.blend_key is None
    assert d.names() == ["write", "run.dress", "run.export"]
    assert any("unavailable" in n for n in out.report.notes)


async def test_simulation_job_uses_the_dressed_scene(env):  # noqa: F811
    stubs, d = Stubs(), Dress(passes=[True])
    acts = stubs.activities()
    for q, fns in d.activities().items():
        have = acts.get(q, [])
        acts[q] = (have if isinstance(have, list) else [have]) + fns
    async with make_workers(env, acts):
        handle = await start(env, uuid.uuid4().hex, dress=True)
        await review(handle, 1, approved=True)
        out = JobOutcome.model_validate(await handle.result())
    assert out.status == "approved"
    assert out.scene_key == "jobs/j/dress/attempt-0/scene.gltf" and out.raw_scene_key == "sc"
    assert out.blend_key == "jobs/j/dress/attempt-0/scene.blend"
    assert out.dress_report is not None and out.dress_report.dressed
    assert out.eval_report is not None and out.eval_report.passed  # the physics evaluation is untouched


async def test_props_reach_the_writer_and_the_runner_once(env):  # noqa: F811
    d = Dress(passes=[False, True], props=["park_bench", "broken"])
    out = await run_dress(env, d)
    assert out.report.dressed and [p.id for p in out.report.props] == ["park_bench"]
    catalogue = [inp for n, inp in d.calls if n == "props.catalogue"]
    assert len(catalogue) == 1 and "a ball" in catalogue[0]["query"]
    writes = [inp for n, inp in d.calls if n == "write"]
    assert [o["id"] for o in writes[0]["prop_options"]] == ["park_bench", "tree_01"]
    fetches = [inp["ids"] for n, inp in d.calls if n == "props.fetch"]
    assert fetches == [["park_bench", "broken"], ["broken"]]  # what was fetched is reused on the retry
    runs = [inp for n, inp in d.calls if n == "run.dress"]
    assert all([p["id"] for p in r["props"]] == ["park_bench"] for r in runs)
    assert any("broken" in n for n in out.report.notes)



async def test_failed_blend_export_fails_the_workflow_so_it_can_be_retried(env):  # noqa: F811
    import pytest
    from omnific_common.contracts import BlendExportInput
    from temporalio.client import WorkflowFailureError

    d = Dress(export_ok=False)
    async with make_workers(env, d.activities()):
        with pytest.raises(WorkflowFailureError):
            await env.client.execute_workflow(
                "BlendExport", BlendExportInput(job_id="j", scene_key="s", playback_key="p", entities=["ball"],
                                                prefix="variants/v/blend"),
                id=f"blend-{uuid.uuid4().hex}", task_queue=TaskQueue.WORKFLOW.value, result_type=BlenderRunOutput)
