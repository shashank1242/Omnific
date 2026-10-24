"""FastAPI gateway: start SimulationJob workflows, report status, serve artifacts."""

import os
import re
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from omnific_common.contracts import (
    ClarifyAnswers,
    ClarifyQuestion,
    IngestInput,
    JobOutcome,
    JobRequest,
    JobStatus,
    ReviewDecision,
    TaskQueue,
)
from omnific_common.storage import ArtifactStore, store_from_env
from omnific_common.temporal import connect
from pydantic import BaseModel, Field
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DOC_ID_RE = JOB_ID_RE

# supported upload extension -> canonical content type (docs/rag.md)
DOC_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
# content types that browsers commonly send besides the canonical ones
_GENERIC_TYPES = {"", "application/octet-stream"}
_EXTRA_TYPES = {"text/x-markdown", "application/xhtml+xml", "application/csv", "application/vnd.ms-excel",
                "image/jpg"}
UPLOAD_CHUNK = 1 << 20

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
    document_ids: list[str] = Field(default_factory=list)
    clarify: bool | None = None
    clarify_timeout_hours: float | None = Field(default=None, gt=0)


class JobCreated(BaseModel):
    job_id: str


class JobView(BaseModel):
    job_id: str
    state: Literal["running", "completed", "failed", "canceled", "terminated", "timed_out", "other"]
    status: JobStatus | None = None
    draft: JobOutcome | None = None  # outcome awaiting review, while the workflow runs
    questions: list[ClarifyQuestion] | None = None  # while awaiting_clarification
    result: JobOutcome | None = None


class DocumentCreated(BaseModel):
    document_id: str


def safe_filename(name: str | None) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).lstrip(".")[:100]
    return base or "document"


def _doc_json(doc: Any) -> Any:
    return doc.model_dump(mode="json") if hasattr(doc, "model_dump") else doc


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
    rag: Any = None,
) -> FastAPI:
    """`client_factory`, `store` and `rag` (the `omnific_rag.db` module) are injectable for tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = await client_factory()
        app.state.store = store if store is not None else store_from_env()
        app.state.rag = rag
        if app.state.rag is None and os.environ.get("RAG_DATABASE_URL"):
            from omnific_rag import db

            app.state.rag = db
        if app.state.rag is not None:
            await app.state.rag.migrate()
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

    def rag_of(request: Request) -> Any:
        r = request.app.state.rag
        if r is None:
            raise HTTPException(503, "document library is not configured")
        return r

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/jobs", response_model=JobCreated)
    async def create_job(body: CreateJob, request: Request) -> JobCreated:
        job_id = uuid.uuid4().hex
        if body.document_ids:
            db = rag_of(request)
            for doc_id in body.document_ids:
                doc = await db.get_document(doc_id) if DOC_ID_RE.match(doc_id) else None
                if doc is None or _doc_json(doc).get("status") != "ready":
                    raise HTTPException(422, f"document {doc_id} does not exist or is not ready")
        req = JobRequest(job_id=job_id, prompt=body.prompt,
                         document_ids=list(dict.fromkeys(body.document_ids)))
        for field in ("max_revisions", "max_review_rounds", "review_timeout_hours", "clarify",
                      "clarify_timeout_hours"):
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
        questions: list[ClarifyQuestion] | None = None
        if state == "running":
            draft = await query_or_none(handle, "draft", JobOutcome | None)
            if status is not None and status.stage == "awaiting_clarification":
                questions = await query_or_none(handle, "questions", list[ClarifyQuestion])
        elif state == "completed":
            result = await outcome_of(handle)
        return JobView(job_id=job_id, state=state, status=status, draft=draft, result=result,
                       questions=questions)

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

    @app.post("/jobs/{job_id}/clarify", status_code=202)
    async def clarify_job(job_id: str, answers: ClarifyAnswers, request: Request) -> dict[str, str]:
        handle, desc = await load(request, job_id)
        if desc.status != WorkflowExecutionStatus.RUNNING:
            raise HTTPException(409, "job is not running")
        status = await query_or_none(handle, "status", JobStatus | None)
        if status is None or status.stage != "awaiting_clarification":
            raise HTTPException(409, "job is not awaiting clarification")
        await handle.signal("clarify", answers)
        return {"status": "accepted"}

    # --- document library ---------------------------------------------------------------

    @app.post("/documents", status_code=202, response_model=DocumentCreated)
    async def upload_document(request: Request, file: UploadFile = File(...)) -> DocumentCreated:  # noqa: B008
        db = rag_of(request)
        filename = safe_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in DOC_TYPES:
            raise HTTPException(415, f"unsupported file type; supported: {', '.join(sorted(DOC_TYPES))}")
        declared = (file.content_type or "").split(";")[0].strip().lower()
        if declared not in _GENERIC_TYPES | _EXTRA_TYPES and declared not in DOC_TYPES.values():
            raise HTTPException(415, f"unsupported content type {declared}")
        limit = int(float(os.environ.get("RAG_MAX_UPLOAD_MB", "50")) * 1024 * 1024)
        chunks, size = [], 0
        while chunk := await file.read(UPLOAD_CHUNK):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"file exceeds the {limit // (1024 * 1024)} MB upload limit")
            chunks.append(chunk)
        if size == 0:
            raise HTTPException(422, "empty file")
        content_type = DOC_TYPES[ext]
        doc_id = uuid.uuid4().hex
        raw_key = f"documents/{doc_id}/{filename}"
        request.app.state.store.put(raw_key, b"".join(chunks), content_type)
        await db.create_document(doc_id, filename, content_type, meta={"size": size})
        try:
            await client_of(request).start_workflow(
                "DocumentIngest",
                IngestInput(document_id=doc_id, filename=filename, content_type=content_type,
                            raw_key=raw_key),
                id=f"ingest-{doc_id}", task_queue=TaskQueue.WORKFLOW.value,
            )
        except Exception as e:
            await db.set_status(doc_id, "failed", error=f"could not start ingestion: {e}")
            raise
        return DocumentCreated(document_id=doc_id)

    @app.get("/documents")
    async def list_documents(request: Request) -> list[Any]:
        return [_doc_json(d) for d in await rag_of(request).list_documents()]

    @app.get("/documents/{doc_id}")
    async def get_document(doc_id: str, request: Request) -> Any:
        db = rag_of(request)
        doc = await db.get_document(doc_id) if DOC_ID_RE.match(doc_id) else None
        if doc is None:
            raise HTTPException(404, "document not found")
        return _doc_json(doc)

    @app.delete("/documents/{doc_id}", status_code=204)
    async def delete_document(doc_id: str, request: Request) -> Response:
        db = rag_of(request)
        if not DOC_ID_RE.match(doc_id) or not await db.delete_document(doc_id):
            raise HTTPException(404, "document not found")
        return Response(status_code=204)

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
