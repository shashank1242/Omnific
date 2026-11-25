"""SimulationVariant: re-run a finished simulation's own solver with values the user changed, then rebuild playback.

No LLM is involved: the SimSpec (with the changes applied) and the solver code come from the original job, and
solvers read every value from the spec, so a variant takes seconds. The variant's artifacts live under the
original job: jobs/<job_id>/variants/<variant_id>/...
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from omnific_common.contracts import (
        ACTIVITY,
        PlaybackInput,
        PlaybackOutput,
        RunInput,
        RunOutput,
        TaskQueue,
        VariantInput,
        VariantOutcome,
    )

RUN_TIMEOUT = timedelta(minutes=15)
DATA_TIMEOUT = timedelta(minutes=5)
RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=["ValidationError"])
ERROR_CHARS = 1500


def variant_key_prefix(job_id: str, variant_id: str) -> str:
    """The job_id the sandbox and data activities key artifacts under (they write jobs/<this>/...)."""
    return f"{job_id}/variants/{variant_id}"


@workflow.defn(name="SimulationVariant")
class SimulationVariant:
    @workflow.run
    async def run(self, inp: VariantInput) -> VariantOutcome:
        key_id = variant_key_prefix(inp.job_id, inp.variant_id)
        try:
            run: RunOutput = await workflow.execute_activity(
                ACTIVITY[TaskQueue.SANDBOX],
                RunInput(job_id=key_id, attempt=0, spec=inp.spec, code=inp.code),
                task_queue=TaskQueue.SANDBOX.value, result_type=RunOutput,
                start_to_close_timeout=RUN_TIMEOUT, retry_policy=RETRY,
            )
        except ActivityError as e:
            return VariantOutcome(variant_id=inp.variant_id, ok=False, error=f"the run could not start: {e.cause or e}")
        if not run.ok or not run.result_key:
            return VariantOutcome(variant_id=inp.variant_id, ok=False, duration_s=run.duration_s,
                                  error=(run.stderr or "the solver failed with these values")[-ERROR_CHARS:])
        try:
            playback: PlaybackOutput = await workflow.execute_activity(
                ACTIVITY[TaskQueue.DATA],
                PlaybackInput(job_id=key_id, spec=inp.spec, result_key=run.result_key),
                task_queue=TaskQueue.DATA.value, result_type=PlaybackOutput,
                start_to_close_timeout=DATA_TIMEOUT, retry_policy=RETRY,
            )
        except ActivityError as e:
            return VariantOutcome(variant_id=inp.variant_id, ok=False, result_key=run.result_key,
                                  duration_s=run.duration_s, error=f"the result could not be prepared: {e.cause or e}")
        return VariantOutcome(variant_id=inp.variant_id, ok=True, playback_key=playback.playback_key,
                              result_key=run.result_key, duration_s=run.duration_s)
