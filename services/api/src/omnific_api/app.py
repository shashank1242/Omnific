"""FastAPI gateway: start SimulationJob workflows, report status, serve artifacts, list published simulations."""

import base64
import binascii
import hashlib
import json
import math
import os
import re
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from omnific_common.contracts import (
    ClarifyAnswers,
    ClarifyQuestion,
    IngestInput,
    InputRef,
    JobOutcome,
    JobRequest,
    JobStatus,
    ReviewDecision,
    SimSpec,
    SolverCode,
    TaskQueue,
    VariantChanges,
    VariantInput,
    VariantOutcome,
)
from omnific_common.storage import ArtifactStore, store_from_env
from omnific_common.temporal import connect
from pydantic import BaseModel, Field, model_validator
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DOC_ID_RE = INPUT_ID_RE = JOB_ID_RE

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

# job attachments interpreted into the prompt (sketch/photo, voice note): extension -> (content type, kind)
INPUT_TYPES: dict[str, tuple[str, str]] = {
    ".png": ("image/png", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".webp": ("image/webp", "image"),
    ".mp3": ("audio/mpeg", "audio"),
    ".wav": ("audio/wav", "audio"),
    ".m4a": ("audio/mp4", "audio"),
    ".webm": ("audio/webm", "audio"),
    ".ogg": ("audio/ogg", "audio"),
}
_EXTRA_INPUT_TYPES = {"image/jpg", "audio/mp3", "audio/x-wav", "audio/wave", "audio/x-m4a", "audio/m4a",
                      "video/webm", "audio/opus"}  # browsers label MediaRecorder output video/webm sometimes
MAX_INPUTS = 10


def mesh_backend_default() -> str:
    b = os.environ.get("MESH_BACKEND", "procedural")
    return b if b in ("procedural", "generated") else "procedural"


def input_meta_key(input_id: str) -> str:
    return f"inputs/{input_id}/meta.json"

# public artifact name -> (JobOutcome attribute holding the storage key, content type)
ARTIFACTS: dict[str, tuple[str, str]] = {
    "playback.json": ("playback_key", "application/json"),
    "scene.gltf": ("scene_key", "model/gltf+json"),
    "result.json": ("result_key", "application/json"),
    "solver.py": ("code_key", "text/x-python; charset=utf-8"),
}


# a finished job's outcome never changes: it is snapshotted here on first read, then served without Temporal
# (and outlives the Temporal history, which a local dev server keeps only in memory)
def outcome_key(job_id: str) -> str:
    return f"jobs/{job_id}/outcome.json"


def thumbnail_key(job_id: str) -> str:
    return f"jobs/{job_id}/thumbnail"


IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
THUMB_MAX_BYTES = 2 * 1024 * 1024
_IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


def sniff_image(data: bytes) -> str | None:
    for magic, mime in _IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def etag_of(data: bytes) -> str:
    return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'


def etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    tags = {t.strip().removeprefix("W/") for t in header.split(",")}
    return "*" in tags or etag in tags


class LRU:
    """Small in-process cache with an item budget and, for bytes values, a byte budget."""

    def __init__(self, max_items: int, max_bytes: int = 0):
        self.max_items, self.max_bytes = max_items, max_bytes
        self.data: OrderedDict[str, Any] = OrderedDict()
        self.size = 0

    @staticmethod
    def _n(value: Any) -> int:
        return len(value) if isinstance(value, bytes) else 0

    def get(self, key: str) -> Any:
        if key in self.data:
            self.data.move_to_end(key)
            return self.data[key]
        return None

    def put(self, key: str, value: Any) -> None:
        n = self._n(value)
        if self.max_bytes and n > self.max_bytes // 4:
            return  # one huge artifact must not evict everything else
        if key in self.data:
            self.size -= self._n(self.data.pop(key))
        self.data[key] = value
        self.size += n
        while len(self.data) > self.max_items or (self.max_bytes and self.size > self.max_bytes):
            _, old = self.data.popitem(last=False)
            self.size -= self._n(old)


class CreateJob(BaseModel):
    prompt: str = ""
    max_revisions: int | None = Field(default=None, ge=0, le=10)
    max_review_rounds: int | None = Field(default=None, ge=1, le=10)
    review_timeout_hours: float | None = Field(default=None, gt=0)
    document_ids: list[str] = Field(default_factory=list)
    clarify: bool | None = None
    clarify_timeout_hours: float | None = Field(default=None, gt=0)
    input_ids: list[str] = Field(default_factory=list, max_length=MAX_INPUTS)
    mesh_backend: Literal["procedural", "generated"] | None = None  # default: MESH_BACKEND env, else procedural

    @model_validator(mode="after")
    def _prompt_or_inputs(self):
        if not self.prompt.strip() and not self.input_ids:
            raise ValueError("give a prompt, attachments, or both")
        return self


class JobCreated(BaseModel):
    job_id: str


class JobView(BaseModel):
    job_id: str
    state: Literal["running", "completed", "failed", "canceled", "terminated", "timed_out", "other"]
    status: JobStatus | None = None
    draft: JobOutcome | None = None  # outcome awaiting review, while the workflow runs
    questions: list[ClarifyQuestion] | None = None  # while awaiting_clarification
    result: JobOutcome | None = None


VARIANT_ID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_VARIANTS = 50
VARIANT_START_GRACE = timedelta(minutes=2)


def variant_key(job_id: str, variant_id: str, name: str) -> str:
    return f"jobs/{job_id}/variants/{variant_id}/{name}"


def variant_index_key(job_id: str) -> str:
    return f"jobs/{job_id}/variants/index.json"


def variant_workflow_id(job_id: str, variant_id: str) -> str:
    return f"variant-{job_id}-{variant_id}"


class VariantRecord(BaseModel):
    """One re-run of a simulation with values the user changed (stored beside the job's artifacts)."""

    variant_id: str
    created_at: str
    changes: VariantChanges
    status: Literal["running", "ready", "failed"]
    error: str = ""
    playback_key: str | None = None


def apply_changes(spec: SimSpec, changes: VariantChanges) -> SimSpec:
    """The SimSpec with the user's values applied. Raises ValueError naming the first bad change."""
    new_params = {c.name: c.value for c in changes.parameters}
    new_initial = {c.name: c.value for c in changes.initial}
    params = {p.name for p in spec.parameters}
    states = {v.name for v in spec.state}
    for name in new_params:
        if name not in params:
            raise ValueError(f"unknown parameter {name!r}")
    for name in new_initial:
        if name not in states:
            raise ValueError(f"unknown state variable {name!r}")
    for c in [*changes.parameters, *changes.initial]:
        if not math.isfinite(c.value):
            raise ValueError(f"{c.name} must be a finite number")
    time = spec.time
    if changes.end is not None:
        if not math.isfinite(changes.end) or changes.end <= time.start:
            raise ValueError(f"the end time must be a number after the start ({time.start} {time.unit})")
        time = time.model_copy(update={"end": changes.end})
    return spec.model_copy(update={
        "parameters": [p.model_copy(update={"value": new_params[p.name]}) if p.name in new_params else p
                       for p in spec.parameters],
        "state": [v.model_copy(update={"initial_value": new_initial[v.name]}) if v.name in new_initial else v
                  for v in spec.state],
        "time": time,
    })


class PublishRequest(BaseModel):
    title: str = Field(default="", max_length=120, description="Default: the simulation's own title")
    description: str = Field(default="", max_length=1000)
    thumbnail: str = Field(default="", description="data: URL (PNG, JPEG or WebP) captured from the viewer")


class DocumentCreated(BaseModel):
    document_id: str


class InputCreated(BaseModel):
    input_id: str
    kind: Literal["image", "audio"]


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
    gallery: Any = None,
) -> FastAPI:
    """`client_factory`, `store`, `rag` (the `omnific_rag.db` module) and `gallery` (`omnific_rag.gallery`) are
    injectable for tests."""
    outcomes = LRU(int(os.environ.get("OUTCOME_CACHE_ITEMS", "256")))
    blobs = LRU(10_000, int(float(os.environ.get("ARTIFACT_CACHE_MB", "256")) * 1024 * 1024))

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
        app.state.gallery = gallery
        if app.state.gallery is None and os.environ.get("RAG_DATABASE_URL"):
            from omnific_rag import gallery as gallery_db

            app.state.gallery = gallery_db
        if app.state.gallery is not None:
            await app.state.gallery.migrate()
        yield

    app = FastAPI(title="Omnific API", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024)  # playback.json and scene.gltf compress several-fold

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

    def final_outcome(request: Request, job_id: str) -> JobOutcome | None:
        """A finished job's snapshot (memory, then the store), or None while it runs or was never snapshotted."""
        if (o := outcomes.get(job_id)) is not None:
            return o
        st: ArtifactStore = request.app.state.store
        if not JOB_ID_RE.match(job_id) or not st.exists(outcome_key(job_id)):
            return None
        o = JobOutcome.model_validate_json(st.get(outcome_key(job_id)))
        outcomes.put(job_id, o)
        return o

    async def finish(request: Request, job_id: str, handle) -> JobOutcome:
        # a handle without result_type (get_workflow_handle) returns the outcome as a plain dict
        o = JobOutcome.model_validate(await outcome_of(handle))
        request.app.state.store.put(outcome_key(job_id), o.model_dump_json().encode(), "application/json")
        outcomes.put(job_id, o)
        return o

    async def query_or_none(handle, name: str, result_type):
        try:
            return await handle.query(name, result_type=result_type)
        except Exception:  # noqa: BLE001 - query can fail before the first workflow task runs
            return None

    async def read_upload(file: UploadFile, limit_env: str, default_mb: str) -> bytes:
        limit = int(float(os.environ.get(limit_env, default_mb)) * 1024 * 1024)
        chunks, size = [], 0
        while chunk := await file.read(UPLOAD_CHUNK):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"file exceeds the {limit // (1024 * 1024)} MB upload limit")
            chunks.append(chunk)
        if size == 0:
            raise HTTPException(422, "empty file")
        return b"".join(chunks)

    def resolve_input(request: Request, input_id: str) -> InputRef:
        st: ArtifactStore = request.app.state.store
        if not INPUT_ID_RE.match(input_id) or not st.exists(input_meta_key(input_id)):
            raise HTTPException(422, f"input {input_id} does not exist")
        return InputRef.model_validate_json(st.get(input_meta_key(input_id)))

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
        inputs = [resolve_input(request, i) for i in dict.fromkeys(body.input_ids)]
        req = JobRequest(job_id=job_id, prompt=body.prompt.strip(),
                         document_ids=list(dict.fromkeys(body.document_ids)), inputs=inputs,
                         mesh_backend=body.mesh_backend or mesh_backend_default())
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
        if (final := final_outcome(request, job_id)) is not None:
            stage = "failed" if final.status == "failed" else "approved"
            return JobView(job_id=job_id, state="completed", result=final,
                           status=JobStatus(job_id=job_id, stage=stage, revision=final.revisions))
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
            result = await finish(request, job_id, handle)
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

    # --- job attachments (interpreted into the prompt by the intake agent) -----------------

    @app.post("/inputs", status_code=201, response_model=InputCreated)
    async def upload_input(request: Request, file: UploadFile = File(...)) -> InputCreated:  # noqa: B008
        filename = safe_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in INPUT_TYPES:
            raise HTTPException(415, f"unsupported file type; supported: {', '.join(sorted(INPUT_TYPES))}")
        declared = (file.content_type or "").split(";")[0].strip().lower()
        known = {t for t, _ in INPUT_TYPES.values()}
        if declared not in _GENERIC_TYPES | _EXTRA_INPUT_TYPES | known:
            raise HTTPException(415, f"unsupported content type {declared}")
        # 25 MB is also the OpenAI transcription upload limit
        data = await read_upload(file, "INPUT_MAX_UPLOAD_MB", "25")
        mime, kind = INPUT_TYPES[ext]
        input_id = uuid.uuid4().hex
        ref = InputRef(input_id=input_id, key=f"inputs/{input_id}/{filename}", kind=kind, mime=mime,
                       filename=filename)
        st: ArtifactStore = request.app.state.store
        st.put(ref.key, data, mime)
        st.put(input_meta_key(input_id), json.dumps(ref.model_dump()).encode(), "application/json")
        return InputCreated(input_id=input_id, kind=kind)

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
        data = await read_upload(file, "RAG_MAX_UPLOAD_MB", "50")
        content_type = DOC_TYPES[ext]
        doc_id = uuid.uuid4().hex
        raw_key = f"documents/{doc_id}/{filename}"
        request.app.state.store.put(raw_key, data, content_type)
        await db.create_document(doc_id, filename, content_type, meta={"size": len(data)})
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

    async def viewable_outcome(request: Request, job_id: str) -> tuple[JobOutcome, bool]:
        """The outcome whose artifacts are shown, and whether it is final (immutable)."""
        if (final := final_outcome(request, job_id)) is not None:
            return final, True
        handle, desc = await load(request, job_id)
        if desc.status == WorkflowExecutionStatus.RUNNING:
            outcome = await query_or_none(handle, "draft", JobOutcome | None)  # pre-approval view
        elif desc.status == WorkflowExecutionStatus.COMPLETED:
            return await finish(request, job_id, handle), True
        else:
            outcome = None
        if outcome is None:
            raise HTTPException(404, "artifact not available yet")
        return outcome, False

    def cached_get(request: Request, key: str, final: bool) -> bytes:
        if final and (b := blobs.get(key)) is not None:
            return b
        data = request.app.state.store.get(key)
        if final:
            blobs.put(key, data)
        return data

    def cached_response(request: Request, data: bytes, media_type: str, final: bool,
                        headers: dict[str, str] | None = None) -> Response:
        """Finished jobs: cache forever. Drafts: revalidate (the same URL changes when a new round is built)."""
        etag = etag_of(data)
        h = {"ETag": etag, "Cache-Control": IMMUTABLE if final else REVALIDATE, **(headers or {})}
        if etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=h)
        return Response(content=data, media_type=media_type, headers=h)

    @app.get("/jobs/{job_id}/assets/{entity}/model.step")
    async def get_step(job_id: str, entity: str, request: Request) -> Response:
        """STEP file of an entity's CAD model. The key comes from the job's own scene report, never the request."""
        outcome, final = await viewable_outcome(request, job_id)
        reports = outcome.scene_report.entities if outcome.scene_report else []
        rep = next((r for r in reports if r.entity == entity), None)
        key = rep.step_key if rep is not None else ""
        st: ArtifactStore = request.app.state.store
        # this job's own CAD output, or the cross-job asset cache it was reused from
        if not key or not key.startswith((f"jobs/{job_id}/", "assets/")) or not st.exists(key):
            raise HTTPException(404, "no CAD model for this entity")
        safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in entity)[:60] or "model"
        return cached_response(request, cached_get(request, key, final), "model/step", final,
                               {"Content-Disposition": f'attachment; filename="{safe}.step"'})

    @app.get("/jobs/{job_id}/artifacts/{name}")
    async def get_artifact(job_id: str, name: str, request: Request) -> Response:
        if name not in ARTIFACTS:  # whitelist: also rules out any path traversal
            raise HTTPException(404, "unknown artifact")
        outcome, final = await viewable_outcome(request, job_id)
        attr, content_type = ARTIFACTS[name]
        key = getattr(outcome, attr)
        st: ArtifactStore = request.app.state.store
        if not key or (blobs.get(key) is None and not st.exists(key)):
            raise HTTPException(404, "artifact not available")
        return cached_response(request, cached_get(request, key, final), content_type, final)

    # --- public library (opt-in: the creator publishes an accepted simulation) ------------------

    def gallery_of(request: Request) -> Any:
        g = request.app.state.gallery
        if g is None:
            raise HTTPException(503, "the public library is not configured")
        return g

    def decode_thumbnail(data_url: str) -> tuple[bytes, str]:
        head, sep, body = data_url.partition(",")
        if not sep or not head.startswith("data:image/") or not head.endswith(";base64"):
            raise HTTPException(422, "thumbnail must be a base64 data: URL of an image")
        if len(body) > THUMB_MAX_BYTES * 4 // 3 + 4:
            raise HTTPException(413, "thumbnail is too large")
        try:
            data = base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(422, "thumbnail is not valid base64") from e
        mime = sniff_image(data)
        if mime is None:
            raise HTTPException(415, "thumbnail must be a PNG, JPEG or WebP image")
        return data, mime

    @app.post("/jobs/{job_id}/publish")
    async def publish_job(job_id: str, body: PublishRequest, request: Request) -> Any:
        g = gallery_of(request)
        outcome = final_outcome(request, job_id)
        if outcome is None:
            view = await get_job(job_id, request)  # snapshots a job that has just finished; 404 if unknown
            outcome = view.result
        if outcome is None or outcome.status != "approved" or outcome.spec is None:
            raise HTTPException(409, "only accepted simulations can be published")
        st: ArtifactStore = request.app.state.store
        has_thumb = st.exists(thumbnail_key(job_id))
        if body.thumbnail:
            data, mime = decode_thumbnail(body.thumbnail)
            st.put(thumbnail_key(job_id), data, mime)
            has_thumb = True
        from omnific_rag.models import PublishedSimulation

        spec = outcome.spec
        sim = PublishedSimulation(
            job_id=job_id, title=body.title.strip() or spec.title, description=body.description.strip(),
            prompt=spec.prompt, fidelity=getattr(spec.fidelity, "value", spec.fidelity),
            domains=[getattr(d, "value", d) for d in spec.domains],
            has_thumbnail=has_thumb,
        )
        return _doc_json(await g.publish(sim))

    @app.delete("/jobs/{job_id}/publish", status_code=204)
    async def unpublish_job(job_id: str, request: Request) -> Response:
        if not JOB_ID_RE.match(job_id) or not await gallery_of(request).unpublish(job_id):
            raise HTTPException(404, "simulation is not published")
        return Response(status_code=204)

    @app.get("/gallery")
    async def list_gallery(request: Request, q: str = "", limit: int = 24, offset: int = 0) -> list[Any]:
        return [_doc_json(s) for s in await gallery_of(request).search(q[:200], limit, offset)]

    @app.get("/gallery/{job_id}")
    async def get_gallery_entry(job_id: str, request: Request) -> Any:
        sim = await gallery_of(request).get(job_id) if JOB_ID_RE.match(job_id) else None
        if sim is None:
            raise HTTPException(404, "simulation is not published")
        return _doc_json(sim)

    # --- variants: change values, re-run the job's own solver, compare -----------------------

    def read_variants(st: ArtifactStore, job_id: str) -> list[str]:
        key = variant_index_key(job_id)
        return json.loads(st.get(key)) if st.exists(key) else []

    def save_variant(st: ArtifactStore, job_id: str, rec: VariantRecord) -> None:
        st.put(variant_key(job_id, rec.variant_id, "variant.json"), rec.model_dump_json().encode(), "application/json")

    async def refresh_variant(request: Request, job_id: str, rec: VariantRecord) -> VariantRecord:
        """A running variant's record, updated from its workflow once that has finished."""
        if rec.status != "running":
            return rec
        handle = client_of(request).get_workflow_handle(variant_workflow_id(job_id, rec.variant_id))
        try:
            desc = await handle.describe()
        except RPCError as e:
            if e.status != RPCStatusCode.NOT_FOUND:
                raise
            # the record is saved just before its workflow starts: a young one is still starting, not lost
            if datetime.now(UTC) - datetime.fromisoformat(rec.created_at) < VARIANT_START_GRACE:
                return rec
            rec = rec.model_copy(update={"status": "failed", "error": "this run was interrupted; start it again"})
        else:
            if desc.status == WorkflowExecutionStatus.RUNNING:
                return rec
            if desc.status == WorkflowExecutionStatus.COMPLETED:
                out = VariantOutcome.model_validate(await handle.result())
                rec = rec.model_copy(update={"status": "ready" if out.ok else "failed", "error": out.error,
                                             "playback_key": out.playback_key})
            else:
                rec = rec.model_copy(update={"status": "failed", "error": "the run stopped before it finished"})
        save_variant(request.app.state.store, job_id, rec)
        return rec

    async def load_variant(request: Request, job_id: str, variant_id: str) -> VariantRecord:
        st: ArtifactStore = request.app.state.store
        key = variant_key(job_id, variant_id, "variant.json")
        if not JOB_ID_RE.match(job_id) or not VARIANT_ID_RE.match(variant_id) or not st.exists(key):
            raise HTTPException(404, "variant not found")
        return await refresh_variant(request, job_id, VariantRecord.model_validate_json(st.get(key)))

    @app.post("/jobs/{job_id}/variants", status_code=201, response_model=VariantRecord)
    async def create_variant(job_id: str, changes: VariantChanges, request: Request) -> VariantRecord:
        outcome, _ = await viewable_outcome(request, job_id)
        if outcome.spec is None or not outcome.code_key or outcome.status == "failed":
            raise HTTPException(409, "this simulation has no working solver to re-run")
        if not (changes.parameters or changes.initial or changes.end is not None):
            raise HTTPException(422, "change at least one value")
        try:
            spec = apply_changes(outcome.spec, changes)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        st: ArtifactStore = request.app.state.store
        code = SolverCode(code=cached_get(request, outcome.code_key, True).decode("utf-8"))
        rec = VariantRecord(variant_id=uuid.uuid4().hex[:12], created_at=datetime.now(UTC).isoformat(),
                            changes=changes, status="running")
        # read, check and reserve the index slot with no await in between: one event loop serves this API, so
        # concurrent requests can't both pass the limit or overwrite each other's entry
        ids = read_variants(st, job_id)
        if len(ids) >= MAX_VARIANTS:
            raise HTTPException(409, f"at most {MAX_VARIANTS} runs per simulation")
        save_variant(st, job_id, rec)
        st.put(variant_index_key(job_id), json.dumps([*ids, rec.variant_id]).encode(), "application/json")
        try:
            await client_of(request).start_workflow(
                "SimulationVariant", VariantInput(job_id=job_id, variant_id=rec.variant_id, spec=spec, code=code),
                id=variant_workflow_id(job_id, rec.variant_id), task_queue=TaskQueue.WORKFLOW.value,
                result_type=VariantOutcome,
            )
        except Exception:
            save_variant(st, job_id, rec.model_copy(update={"status": "failed", "error": "the run could not start"}))
            raise
        # API replicas share the store without a lock: if another replica's index write landed over ours meanwhile,
        # add our entry back (the cap can then be passed by a run or two, never by many)
        if rec.variant_id not in (now := read_variants(st, job_id)):
            st.put(variant_index_key(job_id), json.dumps([*now, rec.variant_id]).encode(), "application/json")
        return rec

    @app.get("/jobs/{job_id}/variants", response_model=list[VariantRecord])
    async def list_variants(job_id: str, request: Request) -> list[VariantRecord]:
        if not JOB_ID_RE.match(job_id):
            raise HTTPException(404, "job not found")
        return [await load_variant(request, job_id, v) for v in read_variants(request.app.state.store, job_id)]

    @app.get("/jobs/{job_id}/variants/{variant_id}", response_model=VariantRecord)
    async def get_variant(job_id: str, variant_id: str, request: Request) -> VariantRecord:
        return await load_variant(request, job_id, variant_id)

    @app.get("/jobs/{job_id}/variants/{variant_id}/playback.json")
    async def get_variant_playback(job_id: str, variant_id: str, request: Request) -> Response:
        rec = await load_variant(request, job_id, variant_id)
        st: ArtifactStore = request.app.state.store
        key = rec.playback_key
        # the key comes from our own record and must sit under this variant's own prefix
        if rec.status != "ready" or not key or not key.startswith(f"jobs/{job_id}/variants/{variant_id}/") \
                or not st.exists(key):
            raise HTTPException(404, "this run has no playback")
        return cached_response(request, cached_get(request, key, True), "application/json", True)

    @app.get("/jobs/{job_id}/thumbnail")
    async def get_thumbnail(job_id: str, request: Request) -> Response:
        st: ArtifactStore = request.app.state.store
        if not JOB_ID_RE.match(job_id) or not st.exists(thumbnail_key(job_id)):
            raise HTTPException(404, "no thumbnail")
        data = st.get(thumbnail_key(job_id))
        return cached_response(request, data, sniff_image(data) or "application/octet-stream", False)

    return app
