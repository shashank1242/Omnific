"""AssetChoices: the 3D-model options the user picks from while the simulation builds.

Started by SimulationJob as soon as the modeler has a spec, so it runs next to coding, running and evaluating:
plan (the mesh router) -> per entity, in parallel: search every enabled library, rank by the vision shortlist with a
score each (`asset_choices`) -> signal the parent -> where the router would use a library model anyway, fully
evaluate the top options with the usual library stages (download + safety, geometry and vision critic, postprocess),
signalling each result so the badges update live. Options that pass are handed to SceneBuild ready to use.
Activities are called by name, so this module imports only contracts.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from omnific_common.contracts import (
        AssetChoices,
        AssetChoicesInput,
        AssetChoicesOutput,
        AssetPlan,
        EntityChoices,
        EntityChoicesInput,
        EvaluateStageInput,
        LibraryFetchInput,
        MeshCapabilities,
        MeshRoute,
        MeshStageInput,
        MeshStageOutput,
        PlanAssetsInput,
        PlanAssetsOutput,
        PrefetchedAsset,
        SceneActivity,
        StageEval,
        TaskQueue,
    )

TIMEOUT = timedelta(minutes=10)
RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=["ValidationError"])
PREVALIDATE = 3  # options fully evaluated per entity whose default route is the library
# Stored assets are keyed by entity, stage and attempt: give every pre-evaluated option its own attempt number, far
# from SceneBuild's own (0..max_attempts), so options never overwrite each other or a later SceneBuild stage.
PREVALIDATE_ATTEMPT = 100


def routes_available(caps: MeshCapabilities) -> list[MeshRoute]:
    out: list[MeshRoute] = []
    if caps.library_sources:
        out.append("library")
    if caps.cad:
        out.append("cad")
    if caps.gpu:
        out.append("generated")
    return [*out, "procedural"]


def candidate_id(c) -> str:
    return f"{c.source}/{c.id}"


@workflow.defn(name="AssetChoices")
class AssetChoicesWorkflow:
    def __init__(self) -> None:
        self._choices = AssetChoices()
        self._inp: AssetChoicesInput | None = None

    async def _act(self, name: SceneActivity, arg, result_type):
        return await workflow.execute_activity(name.value, arg, result_type=result_type,
                                               task_queue=TaskQueue.MESH.value, start_to_close_timeout=TIMEOUT,
                                               retry_policy=RETRY)

    async def _publish(self) -> None:
        try:
            await workflow.get_external_workflow_handle(self._inp.parent_workflow_id).signal(
                "choices_update", self._choices)
        except Exception as e:  # noqa: BLE001 - the parent may have moved on; the result still carries the choices
            workflow.logger.warning("could not update the job's asset choices: %s", e)

    async def _entity(self, plan: AssetPlan, routes: list[MeshRoute]) -> EntityChoices:
        try:
            return await self._act(SceneActivity.ASSET_CHOICES, EntityChoicesInput(
                job_id=self._inp.job_id, plan=plan, routes_available=routes), EntityChoices)
        except ActivityError as e:
            return EntityChoices(entity=plan.entity, kind=plan.kind, default_route=plan.mode,
                                 routes_available=routes, note=f"model search failed: {e.cause or e}")

    async def _prevalidate(self, plan: AssetPlan, ent: EntityChoices) -> list[PrefetchedAsset]:
        """The usual library stages on the top options, one at a time (bounded): what SceneBuild would do."""
        out: list[PrefetchedAsset] = []
        job = self._inp.job_id
        for k, opt in enumerate(ent.options[:PREVALIDATE]):
            detail, art = "", None
            n = PREVALIDATE_ATTEMPT + k
            try:
                fetched: MeshStageOutput = await self._act(SceneActivity.LIBRARY_FETCH, LibraryFetchInput(
                    job_id=job, plan=plan, candidate=opt.candidate, attempt=n), MeshStageOutput)
                if fetched.eval.passed:
                    ev: StageEval = await self._act(SceneActivity.EVALUATE, EvaluateStageInput(
                        job_id=job, stage="library", plan=plan, artifact=fetched.artifact, attempt=n), StageEval)
                    if ev.passed:
                        post: MeshStageOutput = await self._act(SceneActivity.POSTPROCESS, MeshStageInput(
                            job_id=job, plan=plan, spec=self._inp.spec, artifact=fetched.artifact, attempt=n),
                            MeshStageOutput)
                        art = post.artifact if post.eval.passed else None
                        detail = _summary(post.eval if art is None else ev)
                    else:
                        detail = _summary(ev)
                else:
                    detail = _summary(fetched.eval)
            except ActivityError as e:
                detail = f"evaluation failed to run: {e.cause or e}"
            opt.status = "passed" if art is not None else "failed"
            opt.detail = (detail + "; " + opt.detail).strip("; ") if opt.detail else detail
            if art is not None:
                out.append(PrefetchedAsset(entity=plan.entity, candidate_id=candidate_id(opt.candidate),
                                           artifact=art))
            await self._publish()
        return out

    @workflow.run
    async def run(self, inp: AssetChoicesInput) -> AssetChoicesOutput:
        self._inp = inp
        planned: PlanAssetsOutput = await self._act(SceneActivity.PLAN, PlanAssetsInput(
            job_id=inp.job_id, prompt=inp.prompt, spec=inp.spec, backend="generated"), PlanAssetsOutput)
        routes = routes_available(planned.capabilities)
        found = await asyncio.gather(*(self._entity(p, routes) for p in planned.assets))
        self._choices = AssetChoices(entities=list(found))
        await self._publish()
        plans = {p.entity: p for p in planned.assets}
        jobs = [self._prevalidate(plans[e.entity], e) for e in self._choices.entities
                if e.default_route == "library" and e.options]
        prefetched = [a for lst in await asyncio.gather(*jobs) for a in lst]
        self._choices.ready = True
        await self._publish()
        return AssetChoicesOutput(choices=self._choices, prefetched=prefetched)


def _summary(ev: StageEval) -> str:
    failed = [f"{c.name}: {c.detail}" for c in ev.checks if not c.passed]
    if failed:
        return "; ".join(failed)[:400]
    critic = next((c.detail for c in ev.checks if c.name.startswith("critic")), "")
    return f"passed every check{f' ({critic})' if critic else ''}"[:400]
