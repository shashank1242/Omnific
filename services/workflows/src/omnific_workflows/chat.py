"""SimulationChat: answer one question about a simulation (one researcher activity, seconds).

The API starts it per question with the job's own record (spec, research, checks, playback key) and waits for the
reply, so the API keeps to starting and querying workflows and every model call stays in an agent worker.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from omnific_common.contracts import CHAT_ACTIVITY, ChatInput, ChatReply, TaskQueue

CHAT_TIMEOUT = timedelta(minutes=2)
RETRY = RetryPolicy(maximum_attempts=2, non_retryable_error_types=["ValidationError", "LLMError"])


@workflow.defn(name="SimulationChat")
class SimulationChat:
    @workflow.run
    async def run(self, inp: ChatInput) -> ChatReply:
        return await workflow.execute_activity(
            CHAT_ACTIVITY, inp, task_queue=TaskQueue.RESEARCHER.value, result_type=ChatReply,
            start_to_close_timeout=CHAT_TIMEOUT, retry_policy=RETRY,
        )
