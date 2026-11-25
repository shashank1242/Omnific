"""SimulationJob: [interpret attached sketches/voice notes] → [clarify] → research → [model → code → run (+ refined
run) → evaluate, with bounded automatic revisions] → package → human review. A rejection feeds the reviewer's notes
back and starts another round, up to `max_review_rounds`.

Follow-up versions (`JobRequest.edit`) are jobs of their own that start from a finished version. The edit classifier
sorts the change and only what it touches re-runs: visual → packaging only (a new scene from the same run);
parameters → the same solver with the new values, then evaluation; structural → the modeler from the previous SimSpec
and everything after it. Then the same review.

Activities are invoked by name on each agent's task queue, so this module imports only contracts.
"""

import asyncio
import re
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from omnific_common.contracts import (
        ACTIVITY,
        EDIT_ACTIVITY,
        AssetChoices,
        AssetChoicesInput,
        AssetChoicesOutput,
        AssetPick,
        AssetPicks,
        Clarification,
        ClarifyAnswers,
        ClarifyInput,
        ClarifyOutput,
        ClarifyQuestion,
        EditClassifyInput,
        EditPlan,
        EditSummary,
        EvalInput,
        EvalReport,
        IntakeInput,
        IntakeItem,
        IntakeOutput,
        JobOutcome,
        JobRequest,
        JobStatus,
        ModelInput,
        PlaybackInput,
        PlaybackOutput,
        PrefetchedAsset,
        ResearchInput,
        ResearchOutput,
        ReviewDecision,
        RunInput,
        RunOutput,
        SceneBuildInput,
        SceneDressInput,
        SceneDressOutput,
        SceneOutput,
        SolverCode,
        SolverCodeInput,
        TaskQueue,
        VariantChanges,
    )
    from omnific_common.edits import apply_plan, spec_diff
    from omnific_sim.spec import SimSpec

# LLM calls are slow and occasionally flaky; retry transport errors, not logic.
LLM_TIMEOUT = timedelta(minutes=5)
RUN_TIMEOUT = timedelta(minutes=15)
RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=["ValidationError"])

# Truncate failure text fed back into prompts.
FEEDBACK_CHARS = 2000

# SceneBuild (mesh v2) may run generation, rigging and animation on a GPU: hours, not minutes.
SCENE_TIMEOUT = timedelta(hours=6)
# SceneDress: up to 3 x (write + Blender run + evaluate) plus the .blend export.
DRESS_TIMEOUT = timedelta(hours=1)
# AssetChoices: search + shortlist + pre-evaluation of a few options per entity.
CHOICES_TIMEOUT = timedelta(hours=1)
CHOICES_WAIT = timedelta(minutes=10)  # packaging waits at most this long for the picker itself

# Refinement factor for the convergence run.
CONVERGENCE_REFINE = 2


@workflow.defn(name="SimulationJob")
class SimulationJob:
    def __init__(self) -> None:
        self._status: JobStatus | None = None
        self._draft: JobOutcome | None = None
        self._pending_review: ReviewDecision | None = None
        self._reviews: list[ReviewDecision] = []
        self._questions: list[ClarifyQuestion] = []
        self._clarifications: list[Clarification] = []
        self._answers: ClarifyAnswers | None = None
        self._prompt = ""  # the typed prompt plus what the intake read from attachments
        self._intake: list[IntakeItem] = []
        self._attempt = 0  # unique per job across rounds; keys sandbox artifacts
        # "ask for changes": the user's words and the version they apply to reach the modeler on every rebuild,
        # so a failed check during the rebuild (which replaces model feedback) can't drop the request
        self._change_requests: list[str] = []
        self._base_spec: SimSpec | None = None
        self._mesh_backend = "procedural"
        self._edit: EditSummary | None = None  # follow-up versions only
        self._edit_from: SimSpec | None = None  # the version this one changes, for the diff
        # asset picker: options found while the job builds, and the user's picks
        self._choices: AssetChoices | None = None
        self._choices_child = None
        self._picks: AssetPicks | None = None
        self._picks_closed = False

    @workflow.query
    def status(self) -> JobStatus | None:
        return self._status

    @workflow.query
    def draft(self) -> JobOutcome | None:
        """The outcome awaiting (or past) review, so reviewers can inspect artifacts before completion."""
        return self._draft

    @workflow.signal
    def review(self, decision: ReviewDecision) -> None:
        if self._status is not None and self._status.stage == "awaiting_review":
            self._pending_review = decision

    @workflow.query
    def asset_choices(self) -> AssetChoices | None:
        return self._choices

    @workflow.signal
    def choices_update(self, choices: AssetChoices) -> None:
        """From the AssetChoices child: options found / an option's evaluation finished."""
        self._choices = choices

    @workflow.signal
    def pick_assets(self, picks: AssetPicks) -> None:
        if self._choices is not None and not self._picks_closed:
            self._picks = picks

    @workflow.query
    def questions(self) -> list[ClarifyQuestion]:
        return self._questions

    @workflow.signal
    def clarify(self, answers: ClarifyAnswers) -> None:
        if (self._status is not None and self._status.stage == "awaiting_clarification"
                and self._answers is None):
            self._answers = answers

    def _set(self, job_id: str, stage, revision: int = 0, message: str = "") -> None:
        self._status = JobStatus(job_id=job_id, stage=stage, revision=revision, message=message)

    def _front_fields(self) -> dict:
        """Outcome fields settled before research: intake and clarification (and, on a version, its prompt)."""
        prompt = self._prompt if self._intake or self._edit else ""
        return {"questions": list(self._questions), "clarifications": list(self._clarifications),
                "effective_prompt": prompt, "intake": list(self._intake)}

    def _extras(self, spec: SimSpec | None) -> dict:
        """Outcome fields every outcome carries: the mesh backend and, on a version, what it changed."""
        edit = self._edit
        if edit is not None and spec is not None and self._edit_from is not None:
            edit = edit.model_copy(update={"diff": spec_diff(self._edit_from, spec)})
        return {"mesh_backend": self._mesh_backend, "edit": edit}

    async def _interpret(self, req: JobRequest) -> None:
        """Turn attached sketches/photos/voice notes into prompt text. Failures fall back to the typed prompt."""
        self._set(req.job_id, "interpreting_inputs", message=f"{len(req.inputs)} attachments")
        try:
            out: IntakeOutput = await self._call(
                TaskQueue.INTAKE, IntakeInput(job_id=req.job_id, prompt=req.prompt, inputs=req.inputs),
                IntakeOutput,
            )
        except ActivityError as e:
            workflow.logger.warning("intake failed, proceeding with the typed prompt: %s", e)
            return
        for w in out.warnings:
            workflow.logger.warning("intake: %s", w)
        if out.items:
            self._prompt, self._intake = out.effective_prompt, out.items

    async def _clarify(self, req: JobRequest) -> None:
        """Ask the clarifier, wait for the user's answers (or time out), and settle on clarifications."""
        self._set(req.job_id, "clarifying")
        try:
            out: ClarifyOutput = await self._call(
                TaskQueue.CLARIFIER, ClarifyInput(job_id=req.job_id, prompt=self._prompt, document_ids=req.document_ids),
                ClarifyOutput,
            )
        except ActivityError as e:  # never fail the job over clarification
            workflow.logger.warning("clarifier failed, proceeding without clarifications: %s", e)
            return
        if not out.questions:
            return
        self._clarifications = await self._ask(req, out.questions)

    async def _ask(self, req: JobRequest, questions: list[ClarifyQuestion]) -> list[Clarification]:
        """Show the questions and wait for the answers (or time out to the defaults)."""
        self._questions, self._answers = list(questions), None
        self._set(req.job_id, "awaiting_clarification", message=f"{len(questions)} questions")
        try:
            await workflow.wait_condition(
                lambda: self._answers is not None,
                timeout=timedelta(hours=req.clarify_timeout_hours),
            )
        except TimeoutError:
            pass
        return build_clarifications(self._questions, self._answers)

    async def _call(self, queue: TaskQueue, arg, result_type, timeout=LLM_TIMEOUT, activity: str | None = None):
        return await workflow.execute_activity(
            activity or ACTIVITY[queue],
            arg,
            task_queue=queue.value,
            result_type=result_type,
            start_to_close_timeout=timeout,
            retry_policy=RETRY,
        )

    def _fail(self, req: JobRequest, reason: str, **fields) -> JobOutcome:
        self._set(req.job_id, "failed", message=reason)
        outcome = JobOutcome(job_id=req.job_id, status="failed", failure_reason=reason, reviews=list(self._reviews),
                             **{**self._front_fields(), **self._extras(fields.get("spec")), **fields})
        self._draft = outcome
        return outcome

    @workflow.run
    async def run(self, req: JobRequest) -> JobOutcome:
        job = req.job_id
        self._mesh_backend = req.mesh_backend
        if req.edit is not None:
            return await self._run_edit(req)
        self._prompt = req.prompt
        if req.inputs:
            await self._interpret(req)
        if not self._prompt.strip():
            return self._fail(req, "none of the attachments could be interpreted and there is no typed prompt",
                              revisions=0)
        if req.clarify:
            await self._clarify(req)
        self._set(job, "researching")
        research: ResearchOutput = await self._call(
            TaskQueue.RESEARCHER,
            ResearchInput(job_id=job, prompt=self._prompt, clarifications=self._clarifications,
                          document_ids=req.document_ids,
                          input_filenames=[it.filename for it in self._intake]),
            ResearchOutput,
        )
        return await self._review_rounds(req, research, None, None, None)

    async def _review_rounds(self, req: JobRequest, research: ResearchOutput, first: JobOutcome | None,
                             spec: SimSpec | None, code: SolverCode | None) -> JobOutcome:
        """Produce (or take `first`), wait for the decision, and on 'ask for changes' rebuild, up to the limit."""
        job = req.job_id
        model_feedback: list[str] = []
        code_feedback: list[str] = []
        for review_round in range(req.max_review_rounds):
            if review_round == 0 and first is not None:
                outcome = first
            else:
                outcome, spec, code = await self._produce(
                    req, research, spec, code, model_feedback, code_feedback, review_round
                )
            if outcome.status == "failed":
                return outcome

            self._draft = outcome
            self._set(job, "awaiting_review", outcome.revisions, f"review round {review_round + 1}")
            try:
                await workflow.wait_condition(
                    lambda: self._pending_review is not None,
                    timeout=timedelta(hours=req.review_timeout_hours),
                )
            except TimeoutError:
                return self._fail(req, "review timed out", **_carry(outcome))

            decision, self._pending_review = self._pending_review, None
            self._reviews.append(decision)
            if decision.approved:
                outcome = outcome.model_copy(update={"status": "approved",
                                                     "reviews": list(self._reviews)})
                self._draft = outcome
                self._set(job, "approved", outcome.revisions, f"approved by {decision.reviewer}")
                return outcome

            notes = [f"Human reviewer ({decision.reviewer}) rejected the result: {decision.notes}"]
            if decision.blame == "solver_coder":
                model_feedback, code_feedback = [], notes
            else:
                model_feedback, code_feedback = notes, []
                if decision.notes.strip():
                    # the version just reviewed already contains every earlier request: apply only the new one
                    self._change_requests = [decision.notes.strip()]
                    self._base_spec = spec
                spec = None

        return self._fail(req, "rejected in every review round", **_carry(self._draft))

    # --- follow-up versions ----------------------------------------------------------------------

    async def _classify(self, job: str, request: str, spec: SimSpec, answers: list[Clarification]) -> EditPlan:
        try:
            return await self._call(
                TaskQueue.MODELER, EditClassifyInput(job_id=job, request=request, spec=spec, answers=answers),
                EditPlan, activity=EDIT_ACTIVITY,
            )
        except ActivityError as e:  # the modeler can always do it the long way
            workflow.logger.warning("edit classifier failed, treating the change as structural: %s", e)
            return _structural(request)

    async def _run_edit(self, req: JobRequest) -> JobOutcome:
        ed = req.edit
        assert ed is not None
        job, base = req.job_id, ed.base
        if base.spec is None or base.research is None or not base.result_key or not base.code_key:
            return self._fail(req, "the version being changed has no complete simulation to start from", revisions=0)
        request = ed.request.strip()
        if ed.inputs:
            self._set(job, "interpreting_inputs", message=f"{len(ed.inputs)} attachments")
            try:
                out: IntakeOutput = await self._call(
                    TaskQueue.INTAKE, IntakeInput(job_id=job, prompt=request, inputs=ed.inputs), IntakeOutput)
                if out.items:
                    request, self._intake = out.effective_prompt, out.items
            except ActivityError as e:
                workflow.logger.warning("intake failed, proceeding with the typed change: %s", e)
        if not request:
            return self._fail(req, "the change is empty and none of the attachments could be interpreted",
                              revisions=0)
        before = base.effective_prompt or base.spec.prompt
        self._prompt = f"{before}\n\nChange requested for version {ed.version}: {request}"
        self._clarifications = list(base.clarifications)
        research = base.research

        self._set(job, "planning_edit")
        plan = await self._classify(job, request, base.spec, [])
        if plan.kind == "unclear" and plan.questions and req.clarify:
            answers = await self._ask(req, plan.questions)
            self._clarifications += answers
            self._set(job, "planning_edit")
            plan = await self._classify(job, request, base.spec, answers)
        if plan.kind == "unclear":
            plan = _structural(request, plan.summary)

        spec: SimSpec | None = None
        if plan.kind in ("visual", "parameters"):
            try:
                spec = apply_plan(base.spec, plan)
            except ValueError as e:
                workflow.logger.warning("edit plan does not apply, treating it as structural: %s", e)
                plan = _structural(request, plan.summary)
        self._edit = EditSummary(root_job_id=ed.root_job_id, base_job_id=ed.base_job_id,
                                 base_version=ed.base_version, version=ed.version,
                                 request=ed.request.strip(), kind=plan.kind, summary=plan.summary)
        self._edit_from = base.spec
        # anything that falls back to the modeler (a failed check, a structural change) is still this change
        self._base_spec, self._change_requests = base.spec, [request]
        self._set(job, "planning_edit", message=plan.summary)

        code: SolverCode | None = SolverCode(code=ed.code)
        if plan.kind == "visual" and spec is not None:
            # nothing physical changed: the same run and its evaluation, a new scene
            first = await self._package(req, spec, base.result_key, base.code_key, research, base.eval_report,
                                        revisions=0, review_round=0)
        elif plan.kind == "parameters" and spec is not None:
            # the same solver works only if it reads the changed values from the spec; one that writes them as
            # literals would quietly ignore the change, so it is rewritten instead
            baked = embedded_values(ed.code, base.spec, plan)
            feedback = [] if not baked else [(
                f"The previous solver wrote {', '.join(baked)} as literal numbers instead of reading them from `spec`; "
                "read every parameter and initial value from `spec`.")]
            first, spec, code = await self._produce(req, research, spec, code, [], feedback, 0, keep_code=not baked)
        else:
            first, spec, code = await self._produce(req, research, None, None, [], [], 0)
            if first.status != "failed" and spec is not None and not spec_diff(base.spec, spec):
                # the diff check: a structural change that changed nothing gets one more, explicit try
                first, spec, code = await self._produce(
                    req, research, None, None,
                    [f"The SimSpec came back unchanged, but the user asked for this change: {request}. Apply it."],
                    [], 0)
        return await self._review_rounds(req, research, first, spec, code)

    # --- production --------------------------------------------------------------------------------

    async def _produce(
        self,
        req: JobRequest,
        research: ResearchOutput,
        spec: SimSpec | None,
        code: SolverCode | None,
        model_feedback: list[str],
        code_feedback: list[str],
        review_round: int,
        keep_code: bool = False,
    ) -> tuple[JobOutcome, SimSpec | None, SolverCode | None]:
        """`keep_code`: the first attempt runs `code` as it is (a version that only changed values)."""
        job = req.job_id
        run: RunOutput | None = None
        report: EvalReport | None = None

        for revision in range(req.max_revisions + 1):
            report = None  # never surface an evaluation of an earlier revision's code
            if spec is None:
                self._set(job, "modeling", revision)
                spec = await self._call(
                    TaskQueue.MODELER,
                    ModelInput(job_id=job, prompt=self._prompt, research=research,
                               clarifications=self._clarifications, feedback=model_feedback,
                               previous_spec=self._base_spec, change_requests=list(self._change_requests)),
                    SimSpec,
                )
                code = None
                await self._start_choices(req, spec)

            if not (keep_code and revision == 0 and code is not None):
                self._set(job, "coding", revision)
                code = await self._call(
                    TaskQueue.SOLVER_CODER,
                    SolverCodeInput(job_id=job, spec=spec, feedback=code_feedback,
                                    previous_code=code.code if code else None),
                    SolverCode,
                )

            self._set(job, "running", revision)
            attempt = self._attempt
            self._attempt += 1
            run, fine = await asyncio.gather(*(
                self._call(
                    TaskQueue.SANDBOX,
                    RunInput(job_id=job, attempt=attempt, spec=spec, code=code, refine=r),
                    RunOutput,
                    timeout=RUN_TIMEOUT,
                )
                for r in (1, CONVERGENCE_REFINE)
            ))
            if not run.ok:
                code_feedback = [f"Sandbox run failed:\n{run.stderr[-FEEDBACK_CHARS:]}"]
                continue
            if not fine.ok:
                code_feedback = [(f"Run with refine={CONVERGENCE_REFINE} failed (simulate must accept "
                                  f"`refine`):\n{fine.stderr[-FEEDBACK_CHARS:]}")]
                continue

            self._set(job, "evaluating", revision)
            report = await self._call(
                TaskQueue.EVALUATOR,
                EvalInput(job_id=job, prompt=self._prompt, spec=spec, code=code,
                          result_key=run.result_key, fine_result_key=fine.result_key),
                EvalReport,
            )
            if report.passed:
                break

            failures = [c for c in report.checks if not c.passed]
            model_feedback = [f"{c.name}: {c.detail}" for c in failures if c.blame == "modeler"]
            code_feedback = [f"{c.name}: {c.detail}" for c in failures if c.blame != "modeler"]
            if not failures:
                code_feedback = [f"Evaluation failed without a specific check: {report.critic_summary}"]
            if model_feedback:
                spec = None  # rebuild the model; code is regenerated from the new spec
        else:
            reason = "sandbox run failed" if run is None or not run.ok else "evaluation failed"
            outcome = self._fail(
                req, reason, revisions=req.max_revisions + 1, review_round=review_round, spec=spec,
                research=research, eval_report=report, code_key=run.code_key if run else None,
            )
            return outcome, spec, code

        outcome = await self._package(req, spec, run.result_key, run.code_key, research, report,
                                      revisions=revision + 1, review_round=review_round)
        return outcome, spec, code

    async def _package(self, req: JobRequest, spec: SimSpec, result_key: str | None, code_key: str | None,
                       research: ResearchOutput, report: EvalReport | None, revisions: int,
                       review_round: int) -> JobOutcome:
        """Playback data and the 3D scene (in parallel) for a run that passed: the outcome to review."""
        job = req.job_id
        picks, prefetched = await self._settle_picks(req, spec, max(revisions - 1, 0))
        self._set(job, "packaging", max(revisions - 1, 0))
        playback, scene = await asyncio.gather(
            self._call(TaskQueue.DATA,
                       PlaybackInput(job_id=job, spec=spec, result_key=result_key),
                       PlaybackOutput),
            workflow.execute_child_workflow(
                "SceneBuild",
                SceneBuildInput(job_id=job, prompt=self._prompt, spec=spec, backend=req.mesh_backend,
                                picks=picks, prefetched=prefetched),
                id=f"{job}-scene-{self._attempt}-{review_round}", task_queue=TaskQueue.WORKFLOW.value,
                result_type=SceneOutput, execution_timeout=SCENE_TIMEOUT,
            ),
        )
        dressed = await self._dress(req, spec, playback.playback_key, scene, review_round)
        return JobOutcome(
            job_id=job, status="awaiting_review", revisions=revisions, review_round=review_round,
            reviews=list(self._reviews), spec=spec, research=research, eval_report=report,
            **self._front_fields(), **self._extras(spec),
            code_key=code_key, result_key=result_key, playback_key=playback.playback_key,
            scene_key=dressed.scene_key if dressed else scene.scene_key, scene_report=scene.report,
            raw_scene_key=scene.scene_key if dressed else None,
            blend_key=dressed.blend_key if dressed else None, dress_report=dressed.report if dressed else None,
            asset_choices=self._choices, asset_picks=picks,
        )

    async def _start_choices(self, req: JobRequest, spec: SimSpec) -> None:
        """Start the asset picker once, as soon as the first spec exists, so it runs next to coding and running.
        (Patched so jobs started before the picker replay unchanged.)"""
        if (self._choices_child is not None or not req.pick_assets or req.mesh_backend != "generated"
                or req.edit is not None or not workflow.patched("asset-picker-v1")):
            return
        try:
            self._choices_child = await workflow.start_child_workflow(
                "AssetChoices",
                AssetChoicesInput(job_id=req.job_id, prompt=self._prompt, spec=spec,
                                  parent_workflow_id=workflow.info().workflow_id),
                id=f"{req.job_id}-choices", task_queue=TaskQueue.WORKFLOW.value,
                result_type=AssetChoicesOutput, execution_timeout=CHOICES_TIMEOUT,
            )
        except Exception as e:  # noqa: BLE001 - the picker is optional: without it the router decides
            workflow.logger.warning("asset picker could not start: %s", e)

    async def _settle_picks(self, req: JobRequest, spec: SimSpec,
                            revision: int) -> tuple[list[AssetPick], list[PrefetchedAsset]]:
        """Wait for the user's picks (if there is anything to pick and none came yet), then the picker's result.
        Picks are checked against the options actually offered; anything else falls back to the router."""
        child = self._choices_child
        if child is None:
            return [], []
        job = req.job_id
        if self._picks is None and not self._picks_closed:  # a later review round never waits again
            if self._choices is None:  # the search is still running: give it a moment before deciding
                try:
                    await workflow.wait_condition(lambda: self._choices is not None or child.done(),
                                                  timeout=CHOICES_WAIT)
                except TimeoutError:
                    pass
            if self._choices is not None and any(e.options or len(e.routes_available) > 1
                                                 for e in self._choices.entities):
                n = len(self._choices.entities)
                self._set(job, "awaiting_asset_picks", revision, f"{n} object{'s' if n != 1 else ''} to choose")
                try:
                    await workflow.wait_condition(lambda: self._picks is not None,
                                                  timeout=timedelta(minutes=req.pick_timeout_minutes))
                except TimeoutError:
                    pass
        self._picks_closed = True
        prefetched: list[PrefetchedAsset] = []
        if self._choices is None and not child.done():  # the picker produced nothing in time: don't wait twice
            workflow.logger.warning("asset picker gave no choices in time; the router decides")
            return [], []
        try:
            await workflow.wait_condition(lambda: child.done(), timeout=CHOICES_WAIT)
            out: AssetChoicesOutput = child.result()
            self._choices, prefetched = out.choices, out.prefetched
        except Exception as e:  # noqa: BLE001 - timeout or a failed picker: use what we have
            workflow.logger.warning("asset picker result unavailable: %s", e)
        # a revision may have changed what an entity is (same name, new kind): its options no longer apply
        kinds = {e.name: e.kind for e in spec.entities}
        stale = {e.entity for e in (self._choices.entities if self._choices else []) if kinds.get(e.entity) != e.kind}
        prefetched = [a for a in prefetched if a.entity not in stale]
        return [p for p in resolve_picks(self._picks, self._choices, spec) if p.entity not in stale], prefetched

    async def _dress(self, req: JobRequest, spec: SimSpec, playback_key: str, scene: SceneOutput,
                     review_round: int) -> SceneDressOutput | None:
        """Blender restyles, decorates and lights the assembled scene and exports a .blend. Never fails the job:
        any failure keeps SceneBuild's scene. (Patched so jobs started before scene dressing replay unchanged.)"""
        if not req.dress or not workflow.patched("scene-dress-v1"):
            return None
        job = req.job_id
        self._set(job, "packaging", self._status.revision if self._status else 0, "dressing the scene in Blender")
        try:
            return await workflow.execute_child_workflow(
                "SceneDress",
                SceneDressInput(job_id=job, prompt=self._prompt, spec=spec, playback_key=playback_key,
                                scene_key=scene.scene_key, scene_report=scene.report),
                id=f"{job}-dress-{self._attempt}-{review_round}", task_queue=TaskQueue.WORKFLOW.value,
                result_type=SceneDressOutput, execution_timeout=DRESS_TIMEOUT,
            )
        except ChildWorkflowError as e:
            workflow.logger.warning("scene dressing failed, keeping SceneBuild's scene: %s", e)
            return None


def resolve_picks(picks: AssetPicks | None, choices: AssetChoices | None, spec: SimSpec) -> list[AssetPick]:
    """The user's picks that match what was offered, with the chosen candidate filled in. Unknown entities, routes
    the deployment doesn't serve and options that weren't offered (or failed) are dropped: the router decides."""
    if picks is None or choices is None:
        return []
    offered = {e.entity: e for e in choices.entities}
    names = {e.name for e in spec.entities}
    out: list[AssetPick] = []
    for p in picks.picks:
        ent = offered.get(p.entity)
        if ent is None or p.entity not in names or p.route == "auto":
            continue
        if p.route == "library":
            opt = next((o for o in ent.options if f"{o.candidate.source}/{o.candidate.id}" == p.candidate_id), None)
            if opt is None or opt.status == "failed":
                continue
            out.append(p.model_copy(update={"candidate": opt.candidate}))
        elif p.route in ent.routes_available:
            out.append(p.model_copy(update={"candidate_id": "", "candidate": None}))
    return out


def build_clarifications(
    questions: list[ClarifyQuestion], answers: ClarifyAnswers | None
) -> list[Clarification]:
    """Match answers to questions by id; anything unanswered (or use_defaults / timeout) takes the default."""
    given = {} if answers is None or answers.use_defaults else {a.id: a for a in answers.answers}
    out = []
    for q in questions:
        a = given.get(q.id)
        used_default = a is None or not a.value.strip()
        custom = not used_default and a is not None and a.custom
        out.append(Clarification(
            question=q.question, answer=q.default if used_default else a.value.strip(),
            # a typed answer carries its own unit if it has one ("3 ft"): don't stamp the question's unit on it
            unit="" if custom else q.unit, used_default=used_default, custom=custom,
        ))
    return out


def embedded_values(code: str, spec: SimSpec, plan: EditPlan) -> list[str]:
    """Names of changed values whose old value appears in the solver as a literal number (0, 1 and 2 are everywhere,
    so they don't count). A false alarm only costs a rewrite of the solver; a miss would ignore the change."""
    old = {p.name: p.value for p in spec.parameters} | {v.name: v.initial_value for v in spec.state}
    changed = [(c.name, c.value) for c in [*plan.changes.parameters, *plan.changes.initial]]
    if plan.changes.end is not None:  # a solver that builds its own time grid from the old end
        old["the end time"] = spec.time.end
        changed.append(("the end time", plan.changes.end))
    out = []
    for name, value in changed:
        v = old.get(name)
        if v is None or v in (0, 1, 2) or v == value:
            continue
        forms = {repr(float(v)), f"{v:g}"} | ({str(int(v))} if float(v).is_integer() else set())
        if any(re.search(rf"(?<![\w.]){re.escape(f)}(?![\w.])", code) for f in forms):
            out.append(name)
    return out


def _structural(request: str, summary: str = "") -> EditPlan:
    """The safe plan: the modeler revises the SimSpec from the previous one with the request."""
    return EditPlan(kind="structural", summary=summary or request, changes=VariantChanges(), stop_fixed=False,
                    looks=[], questions=[])


def _carry(outcome: JobOutcome | None) -> dict:
    """Fields of the last draft to keep on a failed outcome."""
    if outcome is None:
        return {"revisions": 0}
    fields = ("revisions", "review_round", "spec", "research", "eval_report", "code_key",
              "result_key", "playback_key", "scene_key", "questions", "clarifications", "effective_prompt",
              "intake", "scene_report", "mesh_backend", "edit", "raw_scene_key", "blend_key", "dress_report",
              "asset_choices", "asset_picks")
    return {f: getattr(outcome, f) for f in fields}
