"""DocumentIngest: run the librarian's ingest_document activity with a long timeout and heartbeats."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from omnific_common.contracts import ACTIVITY, IngestInput, IngestOutput, TaskQueue

INGEST_TIMEOUT = timedelta(minutes=30)
INGEST_HEARTBEAT = timedelta(minutes=2)
INGEST_RETRY = RetryPolicy(maximum_attempts=3)  # ApplicationError(non_retryable=True) is never retried


@workflow.defn(name="DocumentIngest")
class DocumentIngest:
    @workflow.run
    async def run(self, inp: IngestInput) -> IngestOutput:
        return await workflow.execute_activity(
            ACTIVITY[TaskQueue.LIBRARIAN],
            inp,
            task_queue=TaskQueue.LIBRARIAN.value,
            result_type=IngestOutput,
            start_to_close_timeout=INGEST_TIMEOUT,
            heartbeat_timeout=INGEST_HEARTBEAT,
            retry_policy=INGEST_RETRY,
        )
