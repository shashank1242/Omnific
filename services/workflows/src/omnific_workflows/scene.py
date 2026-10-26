"""SceneBuild (mesh v2): plan → per entity, in parallel: generate → postprocess → rig → animate → assemble.

Every stage is evaluated. A failed evaluation retries that stage with the critique, up to `max_attempts`; a stage
that never passes drops the entity one level (generated+rigged → generated → procedural). Meshes never fail a job:
the worst case is the MVP's procedural scene. Activities are called by name (SceneActivity), so this module imports
only contracts.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError, TimeoutType

with workflow.unsafe.imports_passed_through():
    from omnific_common.contracts import (
        GPU_SCENE_ACTIVITIES,
        AssembleInput,
        AssembleOutput,
        AssetPlan,
        CheckResult,
        EntityReport,
        EvaluateStageInput,
        MeshArtifact,
        MeshStageInput,
        MeshStageOutput,
        PlanAssetsInput,
        PlanAssetsOutput,
        SceneActivity,
        SceneBuildInput,
        SceneOutput,
        SceneReport,
        StageEval,
        TaskQueue,
    )

CPU_TIMEOUT = timedelta(minutes=10)
GPU_TIMEOUT = timedelta(minutes=45)
GPU_HEARTBEAT = timedelta(minutes=2)
RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=["ValidationError"])


class StageFailed(Exception):
    pass


def _failed(ev: StageEval) -> str:
    return "; ".join(f"{c.name}: {c.detail}" for c in ev.checks if not c.passed)


@workflow.defn(name="SceneBuild")
class SceneBuild:
    def __init__(self) -> None:
        self._inp: SceneBuildInput | None = None

    async def _act(self, name: SceneActivity, arg, result_type):
        gpu = name in GPU_SCENE_ACTIVITIES
        return await workflow.execute_activity(
            name.value, arg, result_type=result_type,
            task_queue=(TaskQueue.MESH_GPU if gpu else TaskQueue.MESH).value,
            start_to_close_timeout=GPU_TIMEOUT if gpu else CPU_TIMEOUT,
            schedule_to_start_timeout=timedelta(minutes=self._inp.gpu_wait_minutes) if gpu else None,
            heartbeat_timeout=GPU_HEARTBEAT if gpu else None,
            retry_policy=RETRY,
        )

    def _stage_input(self, plan: AssetPlan, art: MeshArtifact | None, attempt: int, critique: str) -> MeshStageInput:
        inp = self._inp
        return MeshStageInput(job_id=inp.job_id, plan=plan, spec=inp.spec, artifact=art, attempt=attempt,
                              critique=critique)

    async def _gpu_stage(self, name: SceneActivity, stage: str, plan: AssetPlan, art: MeshArtifact | None,
                         evals: list[StageEval], counter: list[int]) -> MeshArtifact:
        """Run a GPU stage until its evaluation passes. Raises StageFailed after max_attempts."""
        critique = ""
        for attempt in range(self._inp.max_attempts):
            counter[0] += 1
            try:
                out: MeshArtifact = await self._act(name, self._stage_input(plan, art, attempt, critique),
                                                    MeshArtifact)
            except ActivityError as e:
                evals.append(StageEval(stage=stage, attempt=attempt, passed=False, checks=[CheckResult(
                    name=f"{stage}.run", passed=False, detail=f"{name.value} failed: {e.cause or e}")]))
                if isinstance(e.cause, TimeoutError) and e.cause.type == TimeoutType.SCHEDULE_TO_START:
                    break  # no GPU worker picked it up: retrying won't help
                if isinstance(e.cause, ApplicationError) and e.cause.type == "StageUnavailable":
                    break  # the GPU worker has no backend for this stage (e.g. no ML animator configured)
                continue
            ev: StageEval = await self._act(SceneActivity.EVALUATE, EvaluateStageInput(
                job_id=self._inp.job_id, stage=stage, plan=plan, artifact=out, attempt=attempt, before=art),
                StageEval)
            evals.append(ev)
            if ev.passed:
                return out
            critique = ev.critique or _failed(ev)
        raise StageFailed(stage)

    async def _cpu_stage(self, name: SceneActivity, plan: AssetPlan, art: MeshArtifact | None, attempt: int,
                         evals: list[StageEval]) -> MeshStageOutput:
        out: MeshStageOutput = await self._act(name, self._stage_input(plan, art, attempt, ""), MeshStageOutput)
        evals.append(out.eval)
        return out

    async def _entity(self, plan: AssetPlan, cached: MeshArtifact | None) -> tuple[MeshArtifact, EntityReport]:
        evals: list[StageEval] = []
        notes: list[str] = []
        counter = [0]
        art: MeshArtifact | None = None

        if cached is not None:
            art = cached
            notes.append("reused a cached asset")
        elif plan.mode == "generated":
            art = await self._generate(plan, evals, notes, counter)
            if art is not None and plan.rig in ("ml", "both"):
                art = await self._rig_and_animate(plan, art, evals, notes, counter)

        if art is None:
            out = await self._cpu_stage(SceneActivity.PROCEDURAL, plan, None, 0, evals)
            art = out.artifact
        if plan.rig in ("physics", "both"):
            pr = await self._cpu_stage(SceneActivity.PHYSICS_RIG, plan, art, 0, evals)
            if pr.eval.passed:
                art = pr.artifact
            else:
                notes.append("physics joint failed its checks; the entity moves as a rigid node")
        if art.source == "generated" and cached is None:
            cached_art = await self._act(SceneActivity.CACHE, self._stage_input(plan, art, 0, ""), MeshArtifact)
            notes.append(f"cached as {cached_art.glb_key}")
        final = ("generated_rigged" if art.source == "generated" and art.bones
                 else "generated" if art.source == "generated" else "procedural")
        return art, EntityReport(entity=plan.entity, final=final, attempts=counter[0], evals=evals, notes=notes)

    async def _generate(self, plan, evals, notes, counter) -> MeshArtifact | None:
        """generate (GPU) → evaluate → postprocess (CPU, self-evaluated); a postprocess failure regenerates."""
        critique_from_post = ""
        for round_ in range(self._inp.max_attempts):
            try:
                raw = await self._gpu_stage(SceneActivity.GENERATE, "generate", plan.model_copy(update={
                    "gen_prompt": plan.gen_prompt + (f"\nAvoid: {critique_from_post}" if critique_from_post else "")}),
                    None, evals, counter)
            except StageFailed:
                notes.append("generation never passed its evaluation; using the procedural asset")
                return None
            post = await self._cpu_stage(SceneActivity.POSTPROCESS, plan, raw, round_, evals)
            if post.eval.passed:
                return post.artifact
            critique_from_post = post.eval.critique or _failed(post.eval)
        notes.append("post-processing never passed its evaluation; using the procedural asset")
        return None

    async def _rig_and_animate(self, plan, art, evals, notes, counter) -> MeshArtifact:
        try:
            rigged = await self._gpu_stage(SceneActivity.ML_RIG, "ml_rig", plan, art, evals, counter)
        except StageFailed:
            notes.append("ML rig never passed its evaluation; the mesh stays unrigged")
            return art
        if not plan.clips:
            return rigged
        try:
            return await self._gpu_stage(SceneActivity.ML_ANIMATE, "animate", plan, rigged, evals, counter)
        except StageFailed:
            notes.append("ML animation never passed its evaluation; trying procedural clips")
        out = await self._cpu_stage(SceneActivity.PROCEDURAL_ANIMATE, plan, rigged, 0, evals)
        if out.artifact.clips:
            dropped = len(plan.clips) - len(out.artifact.clips)
            if dropped:
                notes.append(f"{dropped} procedural clip(s) failed their checks and were dropped")
            return out.artifact
        notes.append("no clip passed its checks; rigged without animation")
        return rigged

    @workflow.run
    async def run(self, inp: SceneBuildInput) -> SceneOutput:
        self._inp = inp
        planned: PlanAssetsOutput = await self._act(SceneActivity.PLAN, PlanAssetsInput(
            job_id=inp.job_id, prompt=inp.prompt, spec=inp.spec, backend=inp.backend), PlanAssetsOutput)
        cached = {a.entity: a for a in planned.cached}
        results = await asyncio.gather(*(self._entity(p, cached.get(p.entity)) for p in planned.assets))
        arts = [a for a, _ in results]
        reports = [r for _, r in results]
        out: AssembleOutput = await self._act(SceneActivity.ASSEMBLE, AssembleInput(
            job_id=inp.job_id, spec=inp.spec, artifacts=arts), AssembleOutput)
        scene_checks = [c.model_copy(update={"name": f"plan: {c.name}"}) for c in planned.eval.checks]
        scene_checks += out.eval.checks
        if not out.eval.passed and any(a.source == "generated" for a in arts):
            # last resort: a broken scene file is worse than plain shapes
            procedural = [a.model_copy(update={"source": "procedural", "glb_key": "", "bones": [], "clips": []})
                          for a in arts]
            out = await self._act(SceneActivity.ASSEMBLE, AssembleInput(
                job_id=inp.job_id, spec=inp.spec, artifacts=procedural), AssembleOutput)
            scene_checks += [c.model_copy(update={"name": f"fallback: {c.name}"}) for c in out.eval.checks]
            for r in reports:
                r.notes.append("scene assembly failed its checks; fell back to procedural assets")
                r.final = "procedural"
        return SceneOutput(scene_key=out.scene_key, entity_assets=out.entity_assets,
                           report=SceneReport(backend=inp.backend, entities=reports, scene_checks=scene_checks))
