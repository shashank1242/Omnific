"""FastAPI gateway: start SimulationJob workflows, report status, serve artifacts."""

import re
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from omnific_common.contracts import JobOutcome, JobRequest, JobStatus, ReviewDecision, TaskQueue
from omnific_common.storage import ArtifactStore, store_from_env
from omnific_common.temporal import connect
from pydantic import BaseModel, Field
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# public artifact name -> (JobOutcome attribute holding the storage key, content type)
ARTIFACTS: dict[str, tuple[str, str]] = {
    "playback.json": ("playback_key", "application/json"),
    "scene.gltf": ("scene_key", "model/gltf+json"),
    "result.json": ("result_key", "application/json"),
    "solver.py": ("code_key", "text/x-python; charset=utf-8"),
}


class CreateJob(BaseModel):
    prompt: str = Field(min_length=1)
    max_revisions: int | None = Field(default=None, ge=0, le=10)
    max_review_rounds: int | None = Field(default=None, ge=1, le=10)
    review_timeout_hours: float | None = Field(default=None, gt=0)


class JobCreated(BaseModel):
    job_id: str


class JobView(BaseModel):
    job_id: str
    state: Literal["running", "completed", "failed", "canceled", "terminated", "timed_out", "other"]
    status: JobStatus | None = None
    draft: JobOutcome | None = None  # outcome awaiting review, while the workflow runs
    result: JobOutcome | None = None


_STATE = {
    WorkflowExecutionStatus.RUNNING: "running",
    WorkflowExecutionStatus.COMPLETED: "completed",
    WorkflowExecutionStatus.FAILED: "failed",
    WorkflowExecutionStatus.CANCELED: "canceled",
    WorkflowExecutionStatus.TERMINATED: "terminated",
    WorkflowExecutionStatus.TIMED_OUT: "timed_out",
}


def create_app(
    client_factory: Callable[[], Awaitable[Client]] = connect,
    store: ArtifactStore | None = None,
) -> FastAPI:
    """`client_factory` and `store` are injectable so tests can pass fakes."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = await client_factory()
        app.state.store = store if store is not None else store_from_env()
        yield

    app = FastAPI(title="Omnific API", lifespan=lifespan)

    def client_of(request: Request) -> Any:
        return request.app.state.client

    async def load(request: Request, job_id: str):
        if not JOB_ID_RE.match(job_id):
            raise HTTPException(404, "job not found")
        handle = client_of(request).get_workflow_handle(job_id)
        try:
            desc = await handle.describe()
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(404, "job not found") from e
            raise
        return handle, desc

    async def outcome_of(handle) -> JobOutcome:
        return await handle.result()

    async def query_or_none(handle, name: str, result_type):
        try:
            return await handle.query(name, result_type=result_type)
        except Exception:  # noqa: BLE001 - query can fail before the first workflow task runs
            return None

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/jobs", response_model=JobCreated)
    async def create_job(body: CreateJob, request: Request) -> JobCreated:
        job_id = uuid.uuid4().hex
        req = JobRequest(job_id=job_id, prompt=body.prompt)
        for field in ("max_revisions", "max_review_rounds", "review_timeout_hours"):
            if getattr(body, field) is not None:
                setattr(req, field, getattr(body, field))
        await client_of(request).start_workflow(
            "SimulationJob", req, id=job_id, task_queue=TaskQueue.WORKFLOW.value,
            result_type=JobOutcome,
        )
        return JobCreated(job_id=job_id)

    @app.get("/jobs/{job_id}", response_model=JobView)
    async def get_job(job_id: str, request: Request) -> JobView:
        handle, desc = await load(request, job_id)
        state = _STATE.get(desc.status, "other")
        status = await query_or_none(handle, "status", JobStatus | None)
        draft: JobOutcome | None = None
        result: JobOutcome | None = None
        if state == "running":
            draft = await query_or_none(handle, "draft", JobOutcome | None)
        elif state == "completed":
            result = await outcome_of(handle)
        return JobView(job_id=job_id, state=state, status=status, draft=draft, result=result)

    @app.post("/jobs/{job_id}/review", status_code=202)
    async def review_job(job_id: str, decision: ReviewDecision, request: Request) -> dict[str, str]:
        handle, desc = await load(request, job_id)
        if desc.status != WorkflowExecutionStatus.RUNNING:
            raise HTTPException(409, "job is not running")
        status = await query_or_none(handle, "status", JobStatus | None)
        if status is None or status.stage != "awaiting_review":
            raise HTTPException(409, "job is not awaiting review")
        await handle.signal("review", decision)
        return {"status": "accepted"}

    @app.get("/jobs/{job_id}/artifacts/{name}")
    async def get_artifact(job_id: str, name: str, request: Request) -> Response:
        if name not in ARTIFACTS:  # whitelist: also rules out any path traversal
            raise HTTPException(404, "unknown artifact")
        handle, desc = await load(request, job_id)
        if desc.status == WorkflowExecutionStatus.RUNNING:
            outcome = await query_or_none(handle, "draft", JobOutcome | None)  # pre-approval view
        elif desc.status == WorkflowExecutionStatus.COMPLETED:
            outcome = await outcome_of(handle)
        else:
            outcome = None
        if outcome is None:
            raise HTTPException(404, "artifact not available yet")
        attr, content_type = ARTIFACTS[name]
        key = getattr(outcome, attr)
        st: ArtifactStore = request.app.state.store
        if not key or not st.exists(key):
            raise HTTPException(404, "artifact not available")
        return Response(content=st.get(key), media_type=content_type)

    return app
