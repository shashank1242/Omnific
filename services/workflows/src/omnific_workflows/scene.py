"""SceneBuild (mesh v2): plan/route → per entity, in parallel: try each planned route (library model, CAD,
3D generation) → postprocess → rig → animate → assemble. With a GPU worker, the CAD route uses both technologies:
exact geometry and joints from CAD, surface from 3D generation.

Every stage is evaluated. A failed evaluation retries that stage with the critique, up to `max_attempts`; a route
that never passes hands the entity to the next route in its plan, ending at the procedural shape; a rig or clip
stage that never passes drops just that layer. Meshes never fail a job: the worst case is the MVP's procedural
scene. Route outcomes are recorded so the router learns which routes work per kind. Activities are called by name
(SceneActivity), so this module imports only contracts.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError, TimeoutType

with workflow.unsafe.imports_passed_through():
    from omnific_common.contracts import (
        CAD_RUN_ACTIVITY,
        GPU_SCENE_ACTIVITIES,
        AssembleInput,
        AssembleOutput,
        AssetPick,
        AssetPlan,
        CadEvaluateInput,
        CadRunInput,
        CadRunOutput,
        CadSkinInput,
        CadWriteInput,
        CadWriteOutput,
        CheckResult,
        EntityReport,
        EvaluateStageInput,
        LibraryFetchInput,
        LibrarySearchOutput,
        MeshArtifact,
        MeshCapabilities,
        MeshStageInput,
        MeshStageOutput,
        PlanAssetsInput,
        PlanAssetsOutput,
        PrefetchedAsset,
        RouteOutcome,
        RouteStatsInput,
        SceneActivity,
        SceneBuildInput,
        SceneOutput,
        SceneReport,
        StageEval,
        TaskQueue,
    )

CPU_TIMEOUT = timedelta(minutes=10)
CAD_RUN_TIMEOUT_S = 180
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
        self._caps = MeshCapabilities()
        self._picks: dict[str, AssetPick] = {}
        self._pre: dict[str, list[PrefetchedAsset]] = {}

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

    async def _route(self, route: str, plan: AssetPlan, evals, notes, counter) -> MeshArtifact | None:
        """One non-procedural route end to end; None when it never passes (the next route takes over)."""
        if route == "generated":
            art = await self._generate(plan, evals, notes, counter)
            if art is not None and plan.rig in ("ml", "both"):
                art = await self._rig_and_animate(plan, art, evals, notes, counter)
            return art
        if route == "library":
            return await self._library(plan, evals, notes, counter)
        if route == "cad":
            return await self._cad(plan, evals, notes, counter)
        notes.append(f"route {route!r} is not served by this workflow version")
        return None

    async def _library(self, plan: AssetPlan, evals, notes, counter) -> MeshArtifact | None:
        """search (one query variant per attempt) → per shortlisted candidate: fetch → evaluate → postprocess →
        keep / strip its own rig → rig or animate what's missing. The first candidate through wins."""
        for attempt in range(self._inp.max_attempts):
            counter[0] += 1
            found: LibrarySearchOutput = await self._act(
                SceneActivity.LIBRARY_SEARCH, self._stage_input(plan, None, attempt, ""), LibrarySearchOutput)
            evals.append(found.eval)
            for c in found.candidates:
                fetched: MeshStageOutput = await self._act(SceneActivity.LIBRARY_FETCH, LibraryFetchInput(
                    job_id=self._inp.job_id, plan=plan, candidate=c, attempt=attempt), MeshStageOutput)
                evals.append(fetched.eval)
                if not fetched.eval.passed:
                    continue
                ev: StageEval = await self._act(SceneActivity.EVALUATE, EvaluateStageInput(
                    job_id=self._inp.job_id, stage="library", plan=plan, artifact=fetched.artifact, attempt=attempt),
                    StageEval)
                evals.append(ev)
                if not ev.passed:
                    continue
                post = await self._cpu_stage(SceneActivity.POSTPROCESS, plan, fetched.artifact, attempt, evals)
                if not post.eval.passed:
                    continue
                notes.append(f"library model {c.source}/{c.id} {c.name!r} by {c.author or 'unknown'} ({c.license})")
                return await self._library_motion(plan, post.artifact, evals, notes, counter)
            if found.exhausted:
                break
        notes.append("no library model passed its checks")
        return None

    async def _cad(self, plan: AssetPlan, evals, notes, counter) -> MeshArtifact | None:
        """write (LLM) → run (CAD sandbox) → evaluate (+ rig from joints, kinematic clips, vision critic) →
        postprocess; every failure goes back to the writer with the failed checks and its previous code."""
        inp = self._inp
        critique, code = "", ""
        for attempt in range(inp.max_attempts):
            counter[0] += 1
            written: CadWriteOutput = await self._act(SceneActivity.CAD_WRITE, CadWriteInput(
                job_id=inp.job_id, plan=plan, spec=inp.spec, attempt=attempt, critique=critique,
                previous_code=code), CadWriteOutput)
            code = written.code
            run: CadRunOutput = await workflow.execute_activity(
                CAD_RUN_ACTIVITY, CadRunInput(job_id=inp.job_id, entity=plan.entity, code=code, attempt=attempt,
                                              timeout_s=CAD_RUN_TIMEOUT_S),
                result_type=CadRunOutput, task_queue=TaskQueue.SANDBOX.value,
                start_to_close_timeout=timedelta(seconds=CAD_RUN_TIMEOUT_S + 120), retry_policy=RETRY)
            judged: MeshStageOutput = await self._act(SceneActivity.CAD_EVALUATE, CadEvaluateInput(
                job_id=inp.job_id, plan=plan, spec=inp.spec, attempt=attempt, run=run, parts=written.parts),
                MeshStageOutput)
            evals.append(judged.eval)
            if not judged.eval.passed:
                critique = judged.eval.critique or _failed(judged.eval)
                continue
            post = await self._cpu_stage(SceneActivity.POSTPROCESS, plan, judged.artifact, attempt, evals)
            if not post.eval.passed:
                critique = post.eval.critique or _failed(post.eval)
                continue
            notes.append(f"CAD model ({len(written.parts)} parts): {written.notes}")
            return await self._cad_skin(plan, post.artifact, evals, notes, counter)
        notes.append("the CAD model never passed its checks")
        return None

    async def _cad_skin(self, plan: AssetPlan, art: MeshArtifact, evals, notes, counter) -> MeshArtifact:
        """Use both: the CAD model keeps the dimensions, joints and clips, a generated mesh supplies the surface.
        A finished CAD model is never lost: any failure here keeps it as it is."""
        if not self._caps.gpu or not plan.gen_prompt.strip():
            return art
        look = plan.model_copy(update={"gen_prompt": f"{plan.gen_prompt}. Parts and proportions: {plan.cad_brief}"})
        try:
            generated = await self._generate(look, evals, notes, counter)
            if generated is None:
                notes.append("no generated surface passed its checks; keeping the plain CAD surface")
                return art
            counter[0] += 1
            out: MeshStageOutput = await self._act(SceneActivity.CAD_SKIN, CadSkinInput(
                job_id=self._inp.job_id, plan=plan, cad=art, generated=generated), MeshStageOutput)
        except ActivityError as e:
            notes.append(f"generated surface failed ({e.cause or e}); keeping the plain CAD surface")
            return art
        evals.append(out.eval)
        if not out.eval.passed:
            notes.append("the generated surface didn't fit the CAD model; keeping the plain CAD surface")
            return art
        notes.append("generated surface fitted onto the CAD model and its joints")
        return out.artifact

    async def _library_motion(self, plan: AssetPlan, art: MeshArtifact, evals, notes, counter) -> MeshArtifact:
        if art.bones:  # arrived rigged: keep what passes, strip what doesn't
            out = await self._cpu_stage(SceneActivity.LIBRARY_RIG, plan, art, 0, evals)
            art = out.artifact
            notes.append("kept the model's own rig" if art.bones else "the model's own rig failed its checks")
        if plan.rig not in ("ml", "both"):
            return art
        if not art.bones:
            if not self._caps.gpu:
                notes.append("no GPU worker for ML rigging: the model stays unrigged")
                return art
            return await self._rig_and_animate(plan, art, evals, notes, counter)
        if plan.clips and not art.clips:
            out = await self._cpu_stage(SceneActivity.PROCEDURAL_ANIMATE, plan, art, 0, evals)
            if out.artifact.clips:
                return out.artifact
            notes.append("no clip passed its checks; rigged without animation")
        return art

    async def _pinned(self, plan: AssetPlan, pick: AssetPick, evals, notes, counter) -> MeshArtifact | None:
        """The library model the user chose: its pre-evaluated asset, or download + evaluate + postprocess it now
        (the same stages as the library route). None when it fails a check."""
        c = pick.candidate
        credit = f"library model {pick.candidate_id} {c.name!r} by {c.author or 'unknown'} ({c.license})"
        hit = next((a for a in self._pre.get(plan.entity, []) if a.candidate_id == pick.candidate_id), None)
        if hit is not None:
            notes.append(f"your choice: {credit}, evaluated while the simulation built")
            return await self._library_motion(plan, hit.artifact, evals, notes, counter)
        counter[0] += 1
        fetched: MeshStageOutput = await self._act(SceneActivity.LIBRARY_FETCH, LibraryFetchInput(
            job_id=self._inp.job_id, plan=plan, candidate=c, attempt=0), MeshStageOutput)
        evals.append(fetched.eval)
        if not fetched.eval.passed:
            return None
        ev: StageEval = await self._act(SceneActivity.EVALUATE, EvaluateStageInput(
            job_id=self._inp.job_id, stage="library", plan=plan, artifact=fetched.artifact, attempt=0), StageEval)
        evals.append(ev)
        if not ev.passed:
            return None
        post = await self._cpu_stage(SceneActivity.POSTPROCESS, plan, fetched.artifact, 0, evals)
        if not post.eval.passed:
            return None
        notes.append(f"your choice: {credit}")
        return await self._library_motion(plan, post.artifact, evals, notes, counter)

    async def _chosen(self, plan: AssetPlan, evals, notes, counter, tried, outcomes) -> MeshArtifact | None:
        """An asset from the asset picker: the user's library pick, or (no pick) the router's library default
        with an option that already passed every check while the simulation built."""
        pick = self._picks.get(plan.entity)
        pre = self._pre.get(plan.entity, [])
        if pick is not None and pick.route == "library" and pick.candidate is not None:
            tried.append("library")
            n0, e0 = counter[0], len(evals)
            try:
                art = await self._pinned(plan, pick, evals, notes, counter)
            except ActivityError as e:
                notes.append(f"your chosen model could not be prepared ({e.cause or e})")
                art = None
            failed = [c.name for ev in evals[e0:] for c in ev.checks if not c.passed]
            outcomes.append(RouteOutcome(kind=plan.kind, route="library", passed=art is not None,
                                         attempts=counter[0] - n0, failed_checks=failed[:20]))
            if art is None:
                notes.append("your chosen model did not pass its checks; using the planned routes instead")
            return art
        if pick is None and pre and plan.mode == "library":
            try:
                art = await self._library_motion(plan, pre[0].artifact, evals, notes, counter)
            except ActivityError as e:
                notes.append(f"the pre-evaluated model could not be prepared ({e.cause or e}); trying the routes")
                return None
            tried.append("library")
            notes.append(f"library model {pre[0].candidate_id}: the best option, evaluated while the simulation built")
            return art
        return None

    def _routes_for(self, plan: AssetPlan, notes: list[str]) -> list:
        pick = self._picks.get(plan.entity)
        if pick is None or pick.route in ("auto", "library"):
            return list(plan.routes)
        if pick.route == "procedural":
            notes.append("you chose a simple shape")
            return ["procedural"]
        notes.append(f"you chose {'a CAD model' if pick.route == 'cad' else 'an AI-generated model'}")
        return [pick.route, *[r for r in plan.routes if r != pick.route]]

    async def _entity(self, plan: AssetPlan,
                      cached: MeshArtifact | None) -> tuple[MeshArtifact, EntityReport, list[RouteOutcome]]:
        evals: list[StageEval] = []
        notes: list[str] = []
        counter = [0]
        art: MeshArtifact | None = None
        tried: list[str] = []
        outcomes: list[RouteOutcome] = []
        if plan.entity in self._picks:
            cached = None  # the user chose: an asset cached from an earlier plan doesn't count
        routes = self._routes_for(plan, notes)

        recache = False
        if cached is not None:
            art = cached
            notes.append("reused a cached asset")
            if art.source == "generated" and not art.bones and plan.rig in ("ml", "both") and self._caps.gpu:
                # cached before its rig passed (or by an older evaluator): rig it now, and cache it once it is rigged
                notes.append("the cached model has no rig yet: rigging it now")
                try:
                    art = await self._rig_and_animate(plan, art, evals, notes, counter)
                    recache = bool(art.bones)
                except ActivityError as e:  # keep the usable cached mesh, unrigged
                    notes.append(f"rigging the cached model failed ({e.cause or e}); it stays unrigged")
        else:
            art = await self._chosen(plan, evals, notes, counter, tried, outcomes)
            for route in ([] if art is not None else routes):
                if route == "procedural":
                    break
                tried.append(route)
                n0, e0 = counter[0], len(evals)
                try:
                    art = await self._route(route, plan, evals, notes, counter)
                except ActivityError as e:  # e.g. the CAD writer's LLM call kept failing: next route
                    evals.append(StageEval(stage="plan", attempt=0, passed=False, checks=[CheckResult(
                        name=f"route.{route}", passed=False, detail=f"route failed: {e.cause or e}")]))
                    art = None
                failed = [c.name for ev in evals[e0:] for c in ev.checks if not c.passed]
                outcomes.append(RouteOutcome(kind=plan.kind, route=route, passed=art is not None,
                                             attempts=counter[0] - n0, failed_checks=failed[:20]))
                if art is not None:
                    break
                notes.append(f"route {route!r} failed; trying the next one")

        if art is None:
            tried.append("procedural")
            out = await self._cpu_stage(SceneActivity.PROCEDURAL, plan, None, 0, evals)
            art = out.artifact
        if plan.rig in ("physics", "both"):
            pr = await self._cpu_stage(SceneActivity.PHYSICS_RIG, plan, art, 0, evals)
            if pr.eval.passed:
                art = pr.artifact
            else:
                notes.append("physics joint failed its checks; the entity moves as a rigid node")
        if art.source != "procedural" and (cached is None or recache):
            cached_art = await self._act(SceneActivity.CACHE, self._stage_input(plan, art, 0, ""), MeshArtifact)
            notes.append(f"cached as {cached_art.glb_key}")
        final = art.source if art.source == "procedural" or not art.bones else f"{art.source}_rigged"
        return art, EntityReport(entity=plan.entity, final=final, attempts=counter[0], evals=evals, notes=notes,
                                 routes_tried=tried, route_used=art.source, route_reason=plan.route_reason,
                                 attribution=art.attribution if art.source == "library" else None,
                                 step_key=art.step_key if art.source == "cad" else ""), outcomes

    async def _generate(self, plan, evals, notes, counter) -> MeshArtifact | None:
        """generate (GPU) → evaluate → postprocess (CPU, self-evaluated); a postprocess failure regenerates."""
        critique_from_post = ""
        for round_ in range(self._inp.max_attempts):
            try:
                raw = await self._gpu_stage(SceneActivity.GENERATE, "generate", plan.model_copy(update={
                    "gen_prompt": plan.gen_prompt + (f"\nAvoid: {critique_from_post}" if critique_from_post else "")}),
                    None, evals, counter)
            except StageFailed:
                notes.append("generation never passed its evaluation")
                return None
            post = await self._cpu_stage(SceneActivity.POSTPROCESS, plan, raw, round_, evals)
            if post.eval.passed:
                return post.artifact
            critique_from_post = post.eval.critique or _failed(post.eval)
        notes.append("post-processing never passed its evaluation")
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
        self._picks = {p.entity: p for p in inp.picks}
        for a in inp.prefetched:
            self._pre.setdefault(a.entity, []).append(a)
        planned: PlanAssetsOutput = await self._act(SceneActivity.PLAN, PlanAssetsInput(
            job_id=inp.job_id, prompt=inp.prompt, spec=inp.spec, backend=inp.backend), PlanAssetsOutput)
        self._caps = planned.capabilities
        cached = {a.entity: a for a in planned.cached}
        results = await asyncio.gather(*(self._entity(p, cached.get(p.entity)) for p in planned.assets))
        arts = [a for a, _, _ in results]
        reports = [r for _, r, _ in results]
        outcomes = [o for _, _, os_ in results for o in os_]
        out: AssembleOutput = await self._act(SceneActivity.ASSEMBLE, AssembleInput(
            job_id=inp.job_id, spec=inp.spec, artifacts=arts, length_scale=inp.length_scale), AssembleOutput)
        scene_checks = [c.model_copy(update={"name": f"plan: {c.name}"}) for c in planned.eval.checks]
        scene_checks += out.eval.checks
        if not out.eval.passed and any(a.source != "procedural" for a in arts):
            # last resort: a broken scene file is worse than plain shapes
            procedural = [a.model_copy(update={"source": "procedural", "glb_key": "", "bones": [], "clips": [],
                                               "attribution": None}) for a in arts]
            out = await self._act(SceneActivity.ASSEMBLE, AssembleInput(
                job_id=inp.job_id, spec=inp.spec, artifacts=procedural, length_scale=inp.length_scale),
                AssembleOutput)
            scene_checks += [c.model_copy(update={"name": f"fallback: {c.name}"}) for c in out.eval.checks]
            for r in reports:
                r.notes.append("scene assembly failed its checks; fell back to procedural assets")
                r.final = r.route_used = "procedural"
                r.attribution, r.step_key = None, ""
        if outcomes:
            try:
                await self._act(SceneActivity.ROUTE_STATS, RouteStatsInput(job_id=inp.job_id, outcomes=outcomes), int)
            except ActivityError:
                pass  # bookkeeping only
        return SceneOutput(scene_key=out.scene_key, entity_assets=out.entity_assets,
                           report=SceneReport(backend=inp.backend, entities=reports, scene_checks=scene_checks))
