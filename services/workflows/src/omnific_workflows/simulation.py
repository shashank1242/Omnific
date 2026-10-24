"""SimulationJob: research → [model → code → run (+ refined run) → evaluate, with bounded automatic
revisions] → package → human review. A rejection feeds the reviewer's notes back and starts another
round, up to `max_review_rounds`.

Activities are invoked by name on each agent's task queue, so this module imports only contracts.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from omnific_common.contracts import (
        ACTIVITY,
        EvalInput,
        EvalReport,
        JobOutcome,
        JobRequest,
        JobStatus,
        ModelInput,
        PlaybackInput,
        PlaybackOutput,
        ResearchInput,
        ResearchOutput,
        ReviewDecision,
        RunInput,
        RunOutput,
        SceneInput,
        SceneOutput,
        SolverCode,
        SolverCodeInput,
        TaskQueue,
    )
    from omnific_sim.spec import SimSpec

# LLM calls are slow and occasionally flaky; retry transport errors, not logic.
LLM_TIMEOUT = timedelta(minutes=5)
RUN_TIMEOUT = timedelta(minutes=15)
RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=["ValidationError"])

# Truncate failure text fed back into prompts.
FEEDBACK_CHARS = 2000

# Refinement factor for the convergence run.
CONVERGENCE_REFINE = 2


@workflow.defn(name="SimulationJob")
class SimulationJob:
    def __init__(self) -> None:
        self._status: JobStatus | None = None
        self._draft: JobOutcome | None = None
        self._pending_review: ReviewDecision | None = None
        self._reviews: list[ReviewDecision] = []
        self._attempt = 0  # unique per job across rounds; keys sandbox artifacts

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

    def _set(self, job_id: str, stage, revision: int = 0, message: str = "") -> None:
        self._status = JobStatus(job_id=job_id, stage=stage, revision=revision, message=message)

    async def _call(self, queue: TaskQueue, arg, result_type, timeout=LLM_TIMEOUT):
        return await workflow.execute_activity(
            ACTIVITY[queue],
            arg,
            task_queue=queue.value,
            result_type=result_type,
            start_to_close_timeout=timeout,
            retry_policy=RETRY,
        )

    def _fail(self, req: JobRequest, reason: str, **fields) -> JobOutcome:
        self._set(req.job_id, "failed", message=reason)
        outcome = JobOutcome(job_id=req.job_id, status="failed", failure_reason=reason,
                             reviews=list(self._reviews), **fields)
        self._draft = outcome
        return outcome

    @workflow.run
    async def run(self, req: JobRequest) -> JobOutcome:
        job = req.job_id
        self._set(job, "researching")
        research: ResearchOutput = await self._call(
            TaskQueue.RESEARCHER, ResearchInput(job_id=job, prompt=req.prompt), ResearchOutput
        )

        model_feedback: list[str] = []
        code_feedback: list[str] = []
        spec: SimSpec | None = None
        code: SolverCode | None = None

        for review_round in range(req.max_review_rounds):
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
                spec = None

        return self._fail(req, "rejected in every review round", **_carry(self._draft))

    async def _produce(
        self,
        req: JobRequest,
        research: ResearchOutput,
        spec: SimSpec | None,
        code: SolverCode | None,
        model_feedback: list[str],
        code_feedback: list[str],
        review_round: int,
    ) -> tuple[JobOutcome, SimSpec | None, SolverCode | None]:
        job = req.job_id
        run: RunOutput | None = None
        report: EvalReport | None = None

        for revision in range(req.max_revisions + 1):
            report = None  # never surface an evaluation of an earlier revision's code
            if spec is None:
                self._set(job, "modeling", revision)
                spec = await self._call(
                    TaskQueue.MODELER,
                    ModelInput(job_id=job, prompt=req.prompt, research=research,
                               feedback=model_feedback),
                    SimSpec,
                )
                code = None

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
                EvalInput(job_id=job, prompt=req.prompt, spec=spec, code=code,
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

        self._set(job, "packaging", revision)
        playback, scene = await asyncio.gather(
            self._call(TaskQueue.DATA,
                       PlaybackInput(job_id=job, spec=spec, result_key=run.result_key),
                       PlaybackOutput),
            self._call(TaskQueue.MESH, SceneInput(job_id=job, spec=spec), SceneOutput),
        )
        outcome = JobOutcome(
            job_id=job, status="awaiting_review", revisions=revision + 1, review_round=review_round,
            reviews=list(self._reviews), spec=spec, research=research, eval_report=report,
            code_key=run.code_key, result_key=run.result_key, playback_key=playback.playback_key,
            scene_key=scene.scene_key,
        )
        return outcome, spec, code


def _carry(outcome: JobOutcome | None) -> dict:
    """Fields of the last draft to keep on a failed outcome."""
    if outcome is None:
        return {"revisions": 0}
    fields = ("revisions", "review_round", "spec", "research", "eval_report", "code_key",
              "result_key", "playback_key", "scene_key")
    return {f: getattr(outcome, f) for f in fields}
