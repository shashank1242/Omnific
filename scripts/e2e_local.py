"""Run one real SimulationJob end to end on this machine: Temporal dev server + every agent worker in-process,
real OpenAI calls, sandbox in subprocess mode (NOT isolated: trusted local testing only).

    uv run --no-sync python scripts/e2e_local.py "a projectile launched on Mars at 45 degrees"
    uv run --no-sync python scripts/e2e_local.py "cool the tile from the report" --doc report.docx --answer h=15
    uv run --no-sync python scripts/e2e_local.py --serve     # real API on :8000 for the web UI

Reads OPENAI_API_KEY, MODEL_* and RAG_DATABASE_URL (optional, needed for --doc) from .env. MCP tools are used when
MCP_GATEWAY_URL is reachable (e.g. `docker compose up -d mcp-gateway` and MCP_GATEWAY_URL=http://localhost:8811/mcp).
Clarifying questions are answered from --answer id=value, or with defaults. Approves at review unless --no-approve.
Artifacts land in .artifacts/.
"""

import argparse
import asyncio
import json
import logging
import mimetypes
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("omnific_common.llm").setLevel(logging.INFO)  # shows each tool-loop step
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        # .env wins over the shell: a stale OPENAI_API_KEY in the OS environment is a common trap
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


async def serve_api(client) -> int:
    """Run the real API on :8000 against this in-process Temporal until interrupted."""
    import uvicorn
    from omnific_api.app import create_app

    async def factory():
        return client

    print("API on http://localhost:8000. Run the web app with `npm run dev` in apps/web. Ctrl-C to stop.")
    server = uvicorn.Server(uvicorn.Config(create_app(client_factory=factory), port=8000, log_level="info"))
    await server.serve()
    return 0


async def main(args) -> int:
    load_env(ROOT / ".env")
    os.environ["ARTIFACT_STORE"] = "local"
    os.environ["ARTIFACT_DIR"] = str(ROOT / ".artifacts")
    os.environ["SANDBOX_BACKEND"] = "subprocess"
    if os.environ.get("RAG_DATABASE_URL", "").find("@postgres:") >= 0:  # compose hostname -> host port
        os.environ["RAG_DATABASE_URL"] = os.environ["RAG_DATABASE_URL"].replace("@postgres:", "@localhost:")
    if os.environ.get("MCP_GATEWAY_URL", "").startswith("http://mcp-gateway:"):
        os.environ["MCP_GATEWAY_URL"] = os.environ["MCP_GATEWAY_URL"].replace("mcp-gateway", "localhost")

    from omnific_clarifier.activities import clarify
    from omnific_common.contracts import TaskQueue
    from omnific_data.activities import prepare_playback
    from omnific_evaluator.activities import evaluate
    from omnific_librarian.activities import ingest_document
    from omnific_mesh.activities import build_scene
    from omnific_modeler.activities import build_spec
    from omnific_researcher.activities import research
    from omnific_sandbox.activities import run_solver
    from omnific_solver_coder.activities import write_solver
    from omnific_workflows.ingest import DocumentIngest
    from omnific_workflows.simulation import SimulationJob
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    queues = {
        TaskQueue.CLARIFIER: clarify, TaskQueue.LIBRARIAN: ingest_document,
        TaskQueue.RESEARCHER: research, TaskQueue.MODELER: build_spec,
        TaskQueue.SOLVER_CODER: write_solver, TaskQueue.SANDBOX: run_solver,
        TaskQueue.DATA: prepare_playback, TaskQueue.MESH: build_scene, TaskQueue.EVALUATOR: evaluate,
    }
    env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter)
    async with env:
        workers = [Worker(env.client, task_queue=TaskQueue.WORKFLOW.value,
                          workflows=[SimulationJob, DocumentIngest])]
        workers += [Worker(env.client, task_queue=q.value, activities=[fn]) for q, fn in queues.items()]
        async with asyncio.TaskGroup() as tg:
            for w in workers:
                tg.create_task(w.run())
            try:
                if args.serve:
                    return await serve_api(env.client)
                doc_ids = [await ingest(env.client, Path(p)) for p in args.doc]
                if None in doc_ids:
                    return 1
                return await drive(env.client, args, doc_ids)
            finally:
                for w in workers:
                    tg.create_task(w.shutdown())


async def ingest(client, path: Path) -> str | None:
    from omnific_common.contracts import IngestInput, IngestOutput
    from omnific_common.storage import store_from_env
    from omnific_rag import db

    await db.migrate()
    doc_id = uuid.uuid4().hex[:12]
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    key = f"documents/{doc_id}/{path.name}"
    store_from_env().put(key, path.read_bytes(), ctype)
    await db.create_document(doc_id, path.name, ctype)
    t0 = time.time()
    try:
        out = await client.execute_workflow(
            "DocumentIngest", IngestInput(document_id=doc_id, filename=path.name, content_type=ctype, raw_key=key),
            id=f"ingest-{doc_id}", task_queue="workflow", result_type=IngestOutput,
        )
    except Exception as e:  # noqa: BLE001
        print(f"ingest {path.name} FAILED: {e}")
        return None
    print(f"ingested {path.name} -> {doc_id} '{out.title}' in {time.time() - t0:.0f}s: {out.pages} pages, "
          f"{out.n_chunks} chunks, {out.n_figures} figures, {out.n_tables} tables {out.warnings or ''}")
    return doc_id


async def drive(client, args, doc_ids: list[str]) -> int:
    from omnific_common.contracts import (
        ClarifyAnswer,
        ClarifyAnswers,
        ClarifyQuestion,
        JobOutcome,
        JobRequest,
        JobStatus,
        ReviewDecision,
    )

    answers = dict(a.split("=", 1) for a in args.answer)
    job_id = uuid.uuid4().hex
    handle = await client.start_workflow(
        "SimulationJob", JobRequest(job_id=job_id, prompt=args.prompt, document_ids=doc_ids,
                                    clarify=not args.no_clarify),
        id=job_id, task_queue="workflow", result_type=JobOutcome,
    )
    print(f"job {job_id}: {args.prompt!r}")
    t0, last = time.time(), None
    while time.time() - t0 < args.timeout_min * 60:
        status = await handle.query("status", result_type=JobStatus)
        line = status.stage if status else None
        desc = await handle.describe()
        if line != last:
            print(f"  [{time.time() - t0:6.1f}s] {line}")
            last = line
        if desc.status.name != "RUNNING" or line == "awaiting_review":
            break
        if line == "awaiting_clarification":
            qs = await handle.query("questions", result_type=list[ClarifyQuestion])
            for q in qs:
                given = answers.get(q.id)
                print(f"    Q[{q.id}] {q.question} ({q.kind}{', ' + q.unit if q.unit else ''}; "
                      f"default {q.default!r}) -> {given if given is not None else 'default'}")
            await handle.signal("clarify", ClarifyAnswers(
                answers=[ClarifyAnswer(id=q.id, value=answers[q.id]) for q in qs if q.id in answers]))
        await asyncio.sleep(2)

    draft = await handle.query("draft", result_type=JobOutcome)
    if draft is None:
        print("no draft produced (timed out?)")
        return 1
    summarize(draft)
    if draft.status == "awaiting_review":
        if args.no_approve:
            print("left awaiting review (--no-approve)")
            return 0
        await handle.signal("review", ReviewDecision(approved=True, reviewer="e2e-script"))
        outcome = await handle.result()
        print(f"final status: {outcome.status}")
        return 0 if outcome.status == "approved" else 1
    return 1


def summarize(o) -> None:
    print(f"\nstatus={o.status} revisions={o.revisions} {o.failure_reason}")
    for c in o.clarifications:
        print(f"  clarification: {c.question} -> {c.answer} {c.unit}{' (default)' if c.used_default else ''}")
    if o.research:
        for f in o.research.facts:
            print(f"  fact: {f.name} = {f.value} {f.unit}  [{f.citation.url}]")
    if o.spec:
        s = o.spec
        print(f"spec: {s.title} | fidelity={s.fidelity.value} | {len(s.equations)} equations, "
              f"{len(s.parameters)} params, {len(s.state)} states")
        for p in s.parameters:
            print(f"  param: {p.name} = {p.value} {p.unit}" + (f"  [{p.source.url}]" if p.source else ""))
        for a in s.assumptions[:8]:
            print(f"  assumption: {a}")
    if o.eval_report:
        for c in o.eval_report.checks:
            print(f"  {'PASS' if c.passed else 'FAIL'} {c.name}: {c.detail[:120]}")
        if o.eval_report.critic_summary:
            print(f"  critic: {o.eval_report.critic_summary[:400]}")
    print("artifacts:", json.dumps({k: getattr(o, k) for k in
                                    ("code_key", "result_key", "playback_key", "scene_key")}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", help="omit with --serve to submit jobs from the web UI")
    ap.add_argument("--serve", action="store_true", help="serve the real API on :8000 instead of one job")
    ap.add_argument("--doc", action="append", default=[], help="ingest this file and attach it to the job")
    ap.add_argument("--answer", action="append", default=[], help="clarifying answer as id=value")
    ap.add_argument("--no-clarify", action="store_true")
    ap.add_argument("--no-approve", action="store_true")
    ap.add_argument("--timeout-min", type=float, default=30)
    a = ap.parse_args()
    if not a.serve and not a.prompt:
        ap.error("a prompt is required unless --serve is given")
    from omnific_common import aio

    raise SystemExit(aio.run(main(a)))
