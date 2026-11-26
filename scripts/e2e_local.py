"""Run one real SimulationJob end to end on this machine: Temporal dev server + every agent worker in-process,
real OpenAI calls, sandbox in subprocess mode (NOT isolated: trusted local testing only).

    uv run --no-sync python scripts/e2e_local.py "a projectile launched on Mars at 45 degrees"
    uv run --no-sync python scripts/e2e_local.py "cool the tile from the report" --doc report.docx --answer h=15
    uv run --no-sync python scripts/e2e_local.py "" --input ramp_sketch.png --input note.m4a   # sketch / voice note
    uv run --no-sync python scripts/e2e_local.py --serve     # real API on :8000 for the web UI
    MESH_GEN_BACKEND=fake MESH_RIG_BACKEND=fake uv run --no-sync python scripts/e2e_local.py "a dog trotting at 2 m/s"         --mesh-backend generated                              # mesh v2 path with CPU stand-ins for the GPU models

Reads OPENAI_API_KEY, MODEL_* (MODEL_TRANSCRIBE for audio --input) and RAG_DATABASE_URL (optional, needed for --doc) from .env. MCP tools are used when
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


def cad_runner_image() -> bool:
    try:
        import docker

        docker.from_env().images.get(os.environ.get("SANDBOX_CAD_IMAGE", "omnific-cad-runner:latest"))
        return True
    except Exception:  # noqa: BLE001 - no Docker or no image: the CAD route stays off
        return False


def blender_runner() -> str | None:
    """How to run Blender for scene dressing: BLENDER_PYTHON (a Python 3.11 with `bpy`, subprocess) or the docker
    Blender runner image. None: dressing stays off."""
    chosen = os.environ.get("SANDBOX_BLENDER_BACKEND", "")
    py = os.environ.get("BLENDER_PYTHON", "")
    if chosen == "subprocess" or (not chosen and py and Path(py).exists()):
        return "subprocess" if py and Path(py).exists() else None
    try:
        import docker

        docker.from_env().images.get(os.environ.get("SANDBOX_BLENDER_IMAGE", "omnific-blender-runner:latest"))
        return "docker"
    except Exception:  # noqa: BLE001 - no Docker or no image
        return None


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
    from omnific_dress.activities import DRESS_ACTIVITIES
    from omnific_evaluator.activities import evaluate
    from omnific_intake.activities import interpret_inputs
    from omnific_librarian.activities import ingest_document
    from omnific_mesh.activities import SCENE_ACTIVITIES, build_scene
    from omnific_modeler.activities import build_spec
    from omnific_modeler.edit import classify_edit
    from omnific_researcher.activities import research
    from omnific_researcher.chat import answer_question
    from omnific_sandbox.activities import run_blender, run_cad, run_solver
    from omnific_solver_coder.activities import write_solver
    from omnific_workflows.chat import SimulationChat
    from omnific_workflows.choices import AssetChoicesWorkflow
    from omnific_workflows.dress import BlendExport, SceneDress
    from omnific_workflows.ingest import DocumentIngest
    from omnific_workflows.scene import SceneBuild
    from omnific_workflows.simulation import SimulationJob
    from omnific_workflows.variant import SimulationVariant
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    queues = {
        TaskQueue.INTAKE: interpret_inputs, TaskQueue.CLARIFIER: clarify, TaskQueue.LIBRARIAN: ingest_document,
        TaskQueue.RESEARCHER: [research, answer_question], TaskQueue.MODELER: [build_spec, classify_edit],
        TaskQueue.SOLVER_CODER: write_solver, TaskQueue.SANDBOX: [run_solver, run_cad, run_blender],
        TaskQueue.DATA: prepare_playback, TaskQueue.MESH: [build_scene, *SCENE_ACTIVITIES],
        TaskQueue.EVALUATOR: evaluate, TaskQueue.DRESS: DRESS_ACTIVITIES,
    }
    runner = None if args.no_dress else blender_runner()
    if runner:
        os.environ["SANDBOX_BLENDER_BACKEND"] = runner  # what was detected is what run_blender uses
        print(f"Scene dressing on: Blender runs via {runner}")
    else:
        os.environ["DRESS_ENABLED"] = "0"  # the API (--serve) then starts jobs without dressing
        if not args.no_dress:
            print("Scene dressing off: set BLENDER_PYTHON to a Python 3.11 with `bpy`, or build the runner with "
                  "`docker compose build blender-runner-build`")
    args.dress = runner is not None
    if args.mesh_backend == "generated":
        if not args.external_gpu:  # GPU stages in-process (MESH_*_BACKEND=fake on machines without TRELLIS)
            from omnific_mesh_gpu.activities import GPU_ACTIVITIES

            queues[TaskQueue.MESH_GPU] = GPU_ACTIVITIES
        os.environ.setdefault("MESH_GPU_ENABLED", "1")  # a GPU worker runs: the router may pick generated
        # CAD code needs build123d: run it in the docker CAD runner (isolated) when its image exists
        if cad_runner_image():
            os.environ.setdefault("SANDBOX_CAD_BACKEND", "docker")
            os.environ.setdefault("MESH_CAD_ENABLED", "1")
        else:
            os.environ["MESH_CAD_ENABLED"] = "0"
            print("CAD route off: build it with `docker compose build cad-runner-build` to enable it")
    # --external-gpu: the real GPU worker runs in its container and reaches this dev server on a fixed port
    env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter,
                                                **({"port": args.temporal_port} if args.external_gpu else {}))
    if args.external_gpu:
        print(f"Temporal on localhost:{args.temporal_port}: start the GPU worker container against it "
              f"(TEMPORAL_ADDRESS=host.docker.internal:{args.temporal_port}, ARTIFACT_STORE=local, the same "
              ".artifacts mounted at ARTIFACT_DIR)")
    async with env:
        from omnific_common.temporal import workflow_runner

        workers = [Worker(env.client, task_queue=TaskQueue.WORKFLOW.value,
                          workflows=[SimulationJob, DocumentIngest, SceneBuild, SimulationVariant, SceneDress,
                                     BlendExport, AssetChoicesWorkflow, SimulationChat],
                          workflow_runner=workflow_runner())]
        workers += [Worker(env.client, task_queue=q.value, activities=fn if isinstance(fn, list) else [fn])
                    for q, fn in queues.items()]
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


def stage_input(path: Path):
    """Store an attachment the way POST /inputs does and return its InputRef."""
    from omnific_api.app import INPUT_TYPES, safe_filename
    from omnific_common.contracts import InputRef
    from omnific_common.storage import store_from_env

    mime, kind = INPUT_TYPES[path.suffix.lower()]
    input_id = uuid.uuid4().hex
    ref = InputRef(input_id=input_id, key=f"inputs/{input_id}/{safe_filename(path.name)}", kind=kind, mime=mime,
                   filename=safe_filename(path.name))
    store_from_env().put(ref.key, path.read_bytes(), mime)
    return ref


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
        AssetChoices,
        AssetPick,
        AssetPicks,
        ClarifyAnswer,
        ClarifyAnswers,
        ClarifyQuestion,
        JobOutcome,
        JobRequest,
        JobStatus,
        ReviewDecision,
    )

    answers = dict(a.split("=", 1) for a in args.answer)
    picks = dict(p.split("=", 1) for p in args.pick)  # entity=route[:source/id]
    picked = False
    job_id = uuid.uuid4().hex
    handle = await client.start_workflow(
        "SimulationJob", JobRequest(job_id=job_id, prompt=args.prompt or "", document_ids=doc_ids,
                                    clarify=not args.no_clarify, mesh_backend=args.mesh_backend, dress=args.dress,
                                    inputs=[stage_input(Path(p)) for p in args.input]),
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
        if line == "awaiting_asset_picks" and not picked:
            choices = await handle.query("asset_choices", result_type=AssetChoices | None)
            for e in (choices.entities if choices else []):
                opts = "; ".join(f"{o.candidate.source}/{o.candidate.id} {o.candidate.name!r} {o.score}/10 "
                                 f"{o.status}" for o in e.options)
                print(f"    pick[{e.entity}] default {e.default_route}, types {e.routes_available}: {opts or e.note}")
            wanted = {e.entity: picks.get(e.entity, picks.get("*")) for e in (choices.entities if choices else [])}
            chosen = [AssetPick(entity=k, route=v.split(":", 1)[0], candidate_id=v.split(":", 1)[1] if ":" in v else "")
                      for k, v in wanted.items() if v]
            print(f"    picks: {[(p.entity, p.route, p.candidate_id) for p in chosen] or 'let Omnific choose'}")
            await handle.signal("pick_assets", AssetPicks(picks=chosen))
            picked = True
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
    for it in o.intake:
        print(f"  intake {it.kind} {it.filename}: {it.text[:300]}")
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
    if o.scene_report:
        for r in o.scene_report.entities:
            failed = [c.name for e in r.evals for c in e.checks if not c.passed]
            print(f"  mesh {r.entity}: {r.final} via {r.route_used} (tried {r.routes_tried}: {r.route_reason}) "
                  f"after {r.attempts} attempts; failed checks {failed[:6]} {r.notes}")
    if o.dress_report:
        d = o.dress_report
        print(f"  dress: {'dressed' if d.dressed else 'not dressed'} after {d.attempts} attempts; {d.notes}")
        for ev in d.evals:
            for c in ev.checks:
                print(f"    [{ev.attempt}] {'PASS' if c.passed else 'FAIL'} {c.name}: {c.detail[:140]}")
    print("artifacts:", json.dumps({k: getattr(o, k) for k in
                                    ("code_key", "result_key", "playback_key", "scene_key", "raw_scene_key",
                                     "blend_key")}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", help="omit with --serve to submit jobs from the web UI")
    ap.add_argument("--serve", action="store_true", help="serve the real API on :8000 instead of one job")
    ap.add_argument("--doc", action="append", default=[], help="ingest this file and attach it to the job")
    ap.add_argument("--input", action="append", default=[],
                    help="sketch/photo (png/jpg/webp) or voice note (mp3/wav/m4a/webm/ogg) interpreted into the prompt")
    ap.add_argument("--answer", action="append", default=[], help="clarifying answer as id=value")
    ap.add_argument("--no-clarify", action="store_true")
    ap.add_argument("--mesh-backend", choices=["procedural", "generated"], default="procedural",
                    help="generated = TRELLIS/rig/animation on a mesh-gpu worker (docker compose --profile gpu)")
    ap.add_argument("--external-gpu", action="store_true",
                    help="with --mesh-backend generated: leave the mesh-gpu queue to a real GPU worker container")
    ap.add_argument("--temporal-port", type=int, default=7233, help="dev server port with --external-gpu")
    ap.add_argument("--no-approve", action="store_true")
    ap.add_argument("--no-dress", action="store_true", help="skip Blender scene dressing and the .blend export")
    ap.add_argument("--pick", action="append", default=[],
                    help="asset picker (--mesh-backend generated): entity=route[:source/id], e.g. car=library:polypizza/abc "
                         "or ball=procedural, or *=generated for every object; unpicked objects use Omnific's choice")
    ap.add_argument("--timeout-min", type=float, default=30)
    a = ap.parse_args()
    if not a.serve and not (a.prompt or "").strip() and not a.input:
        ap.error("a prompt or --input is required unless --serve is given")
    from omnific_api.app import INPUT_TYPES

    for f in a.input:
        if Path(f).suffix.lower() not in INPUT_TYPES or not Path(f).is_file():
            ap.error(f"--input {f}: not a file of a supported type ({', '.join(sorted(INPUT_TYPES))})")
    from omnific_common import aio

    raise SystemExit(aio.run(main(a)))
