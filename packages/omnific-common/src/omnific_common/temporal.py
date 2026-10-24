"""Temporal connection and worker helpers. All services must connect through `connect()` so they share
the pydantic data converter (contracts are pydantic models)."""


import logging
import os
from collections.abc import Callable, Sequence

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from . import aio
from .contracts import TaskQueue


async def connect() -> Client:
    return await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        data_converter=pydantic_data_converter,
    )


async def _run(task_queue: TaskQueue, activities: Sequence[Callable], workflows: Sequence[type]):
    client = await connect()
    worker = Worker(
        client, task_queue=task_queue.value, activities=list(activities), workflows=list(workflows)
    )
    logging.getLogger(__name__).info("worker started on %s", task_queue.value)
    await worker.run()


def run_worker(
    task_queue: TaskQueue, activities: Sequence[Callable] = (), workflows: Sequence[type] = ()
) -> None:
    """Entry point for `python -m omnific_<agent>`."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    aio.run(_run(task_queue, activities, workflows))
