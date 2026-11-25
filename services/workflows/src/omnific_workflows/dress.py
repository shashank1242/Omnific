"""SceneDress: Blender restyles, decorates and lights the scene SceneBuild assembled, and exports the run as a .blend.

write (dress worker, MODEL_REASONING) -> run (Blender runner on the sandbox queue: scripts, checks, dressed
scene.gltf, .blend, stills) -> check_scene (mesh worker: the scene.gltf contract) -> evaluate (dress worker:
deterministic checks, then the vision critic on MODEL_GENERAL). A failed evaluation goes back to the writer with its
critique and previous scripts, up to `max_attempts`. Dressing never fails a job: if it never passes, the job keeps
SceneBuild's scene and still gets a .blend from the harness's export mode. Motion is never touched: the entities'
nodes stay byte-identical and the .blend is keyed from the evaluated playback with the viewer's own math.

BlendExport: a .blend for any run (a Tune variant), the harness's export mode alone.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from omnific_common.contracts import (
        BLENDER_RUN_ACTIVITY,
        BlenderRunInput,
        BlenderRunOutput,
        BlendExportInput,
        CheckResult,
        CheckSceneInput,
        DressActivity,
        DressEvaluateInput,
        DressProp,
        DressPropOption,
        DressReport,
        DressScripts,
        DressWriteInput,
        SceneActivity,
        SceneDressInput,
        SceneDressOutput,
        ScenePropsInput,
        ScenePropsOutput,
        StageEval,
        TaskQueue,
    )

LLM_TIMEOUT = timedelta(minutes=10)
CPU_TIMEOUT = timedelta(minutes=5)
# A worker that never picks an activity up (dress / mesh / sandbox not running) must not hang the job: dressing then
# falls back like any failure.
QUEUE_WAIT = timedelta(minutes=10)
BLENDER_TIMEOUT_S = 900
RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=["ValidationError"])
INFRA = "Blender sandbox error"  # run_blender's prefix for a runner that could not start at all


async def run_blender(inp: BlenderRunInput) -> BlenderRunOutput:
    return await workflow.execute_activity(
        BLENDER_RUN_ACTIVITY, inp, result_type=BlenderRunOutput, task_queue=TaskQueue.SANDBOX.value,
        start_to_close_timeout=timedelta(seconds=inp.timeout_s + 120), schedule_to_start_timeout=QUEUE_WAIT,
        retry_policy=RETRY)


@workflow.defn(name="SceneDress")
class SceneDress:
    async def _act(self, name: str, arg, result_type, queue: TaskQueue, timeout=LLM_TIMEOUT):
        return await workflow.execute_activity(name, arg, result_type=result_type, task_queue=queue.value,
                                               start_to_close_timeout=timeout, schedule_to_start_timeout=QUEUE_WAIT,
                                               retry_policy=RETRY)

    async def _props(self, inp: SceneDressInput, query: str = "", ids: list[str] | None = None) -> ScenePropsOutput:
        """Ready-made CC0 props (mesh worker, Poly Haven): the catalogue for the writer, or the ones it chose."""
        try:
            return await self._act(SceneActivity.SCENERY_PROPS.value, ScenePropsInput(
                job_id=inp.job_id, query=query, ids=ids or []), ScenePropsOutput, TaskQueue.MESH, LLM_TIMEOUT)
        except ActivityError as e:
            return ScenePropsOutput(notes=[f"props unavailable: {e.cause or e}"])

    @workflow.run
    async def run(self, inp: SceneDressInput) -> SceneDressOutput:
        entities = [e.name for e in inp.spec.entities]
        evals: list[StageEval] = []
        notes: list[str] = []
        critique, previous = "", []
        attempts = 0
        env = inp.spec.environment
        setting = " ".join(x for x in ([env.name, env.description, env.kind, env.ground_material] if env else [])
                           + [inp.prompt] if x)
        catalogue = await self._props(inp, query=setting)
        options: list[DressPropOption] = catalogue.options
        notes += catalogue.notes
        fetched: dict[str, DressProp] = {}
        for attempt in range(inp.max_attempts):
            attempts = attempt + 1
            try:
                written: DressScripts = await self._act(DressActivity.WRITE.value, DressWriteInput(
                    job_id=inp.job_id, prompt=inp.prompt, spec=inp.spec, playback_key=inp.playback_key,
                    scene_key=inp.scene_key, scene_report=inp.scene_report, attempt=attempt, critique=critique,
                    previous=previous, prop_options=options), DressScripts, TaskQueue.DRESS)
            except ActivityError as e:
                notes.append(f"the dress writer failed ({e.cause or e}); keeping SceneBuild's scene")
                break
            wanted = [p for p in written.props if p not in fetched]
            if wanted:
                got = await self._props(inp, ids=wanted)
                fetched.update({p.id: p for p in got.props})
                notes += got.notes
            props = [fetched[p] for p in written.props if p in fetched]
            try:
                run = await run_blender(BlenderRunInput(
                    job_id=inp.job_id, mode="dress", scene_key=inp.scene_key, playback_key=inp.playback_key,
                    entities=entities, scripts=written.scripts, props=props, prefix=f"dress/attempt-{attempt}",
                    timeout_s=BLENDER_TIMEOUT_S))
            except ActivityError as e:  # the runner could not be reached at all: stop trying, keep the scene
                notes.append(f"the Blender runner is unavailable ({e.cause or e}); keeping SceneBuild's scene")
                break
            if not run.ok and run.stderr.startswith(INFRA):
                notes.append(f"the Blender runner is unavailable ({run.stderr[:300]}); keeping SceneBuild's scene")
                evals.append(StageEval(stage="dress", attempt=attempt, passed=False, checks=[CheckResult(
                    name="dress.run", passed=False, detail=run.stderr[:1500])]))
                break
            scene_checks: list[CheckResult] = []
            try:
                if run.ok and run.scene_key:
                    checked: StageEval = await self._act(SceneActivity.CHECK_SCENE.value, CheckSceneInput(
                        job_id=inp.job_id, spec=inp.spec, scene_key=run.scene_key), StageEval, TaskQueue.MESH,
                        CPU_TIMEOUT)
                    scene_checks = checked.checks
                ev: StageEval = await self._act(DressActivity.EVALUATE.value, DressEvaluateInput(
                    job_id=inp.job_id, prompt=inp.prompt, spec=inp.spec, attempt=attempt, run=run,
                    scene_checks=scene_checks), StageEval, TaskQueue.DRESS)
            except ActivityError as e:  # the checks could not run: this attempt cannot pass
                notes.append(f"attempt {attempts} could not be evaluated ({e.cause or e})")
                evals.append(StageEval(stage="dress", attempt=attempt, passed=False, checks=[CheckResult(
                    name="dress.evaluate", passed=False, detail=f"evaluation failed to run: {e.cause or e}")]))
                break
            evals.append(ev)
            if ev.passed and run.scene_key:
                notes.append(f"dressed in Blender on attempt {attempts}")
                return SceneDressOutput(scene_key=run.scene_key, blend_key=run.blend_key, report=DressReport(
                    dressed=True, attempts=attempts, evals=evals, notes=notes, scripts=written.scripts, props=props))
            critique = ev.critique or "; ".join(f"{c.name}: {c.detail}" for c in ev.checks if not c.passed)
            previous = written.scripts
        else:
            notes.append(f"no dressing passed its evaluation in {inp.max_attempts} attempts; keeping SceneBuild's scene")

        blend_key = None
        try:
            export = await run_blender(BlenderRunInput(
                job_id=inp.job_id, mode="export", scene_key=inp.scene_key, playback_key=inp.playback_key,
                entities=entities, prefix="dress/export", timeout_s=BLENDER_TIMEOUT_S))
            blend_key = export.blend_key if export.ok else None
            if not export.ok:
                notes.append(f"no .blend: the export failed ({export.stderr[:300]})")
        except ActivityError as e:
            notes.append(f"no .blend: the export failed ({e.cause or e})")
        return SceneDressOutput(scene_key=inp.scene_key, blend_key=blend_key, report=DressReport(
            dressed=False, attempts=attempts, evals=evals, notes=notes))


@workflow.defn(name="BlendExport")
class BlendExport:
    @workflow.run
    async def run(self, inp: BlendExportInput) -> BlenderRunOutput:
        out = await run_blender(BlenderRunInput(
            job_id=inp.job_id, mode="export", scene_key=inp.scene_key, playback_key=inp.playback_key,
            entities=inp.entities, prefix=inp.prefix, timeout_s=BLENDER_TIMEOUT_S))
        if not out.ok:  # a failed workflow (not a completed one) lets the user's next click start a fresh export
            raise ApplicationError(f"the .blend export failed: {out.stderr[-500:]}", non_retryable=True)
        return out
