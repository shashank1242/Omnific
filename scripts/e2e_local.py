"""Run one real SimulationJob end to end on this machine: Temporal dev server + every agent worker in-process,
real OpenAI calls, sandbox in subprocess mode (NOT isolated â€” trusted local testing only).

    uv run --no-sync python scripts/e2e_local.py "a projectile launched on Mars at 45 degrees"
    uv run --no-sync python scripts/e2e_local.py --serve     # real API on :8000 for the web UI

Reads OPENAI_API_KEY and MODEL_* from .env. Auto-approves at review unless --no-approve.
Artifacts land in .artifacts/jobs/<job_id>/.
"""

import argparseimport asyncioimport jsonimport osimport timeimport uuidfrom pathlib import PathROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        # .env wins over the shell: a stale OPENAI_API_KEY in the OS environment is a common trap
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


async def serve_api(client) -> int:
    """Run the real API on :8000 against this in-process Temporal until interrupted."""
    import uvicorn    from omnific_api.app import create_app

    async def factory():
        return client

    print("API on http://localhost:8000 — run the web app with `npm run dev` in apps/web. Ctrl-C to stop.")
    server = uvicorn.Server(uvicorn.Config(create_app(client_factory=factory), port=8000, log_level="info"))
    await server.serve()
    return 0


async def main(prompt: str | None, approve: bool, timeout_min: float, serve: bool) -> int:
    load_env(ROOT / ".env")
    os.environ["ARTIFACT_STORE"] = "local"
    os.environ["ARTIFACT_DIR"] = str(ROOT / ".artifacts")
    os.environ["SANDBOX_BACKEND"] = "subprocess"

    from omnific_common.contracts import JobRequest, ReviewDecision, TaskQueue    from omnific_data.activities import prepare_playback    from omnific_evaluator.activities import evaluate    from omnific_mesh.activities import build_scene    from omnific_modeler.activities import build_spec    from omnific_researcher.activities import research    from omnific_sandbox.activities import run_solver    from omnific_solver_coder.activities import write_solver    from omnific_workflows.simulation import SimulationJob    from temporalio.contrib.pydantic import pydantic_data_converter    from temporalio.testing import WorkflowEnvironment    from temporalio.worker import Worker

    queues = {
        TaskQueue.RESEARCHER: research, TaskQueue.MODELER: build_spec,
        TaskQueue.SOLVER_CODER: write_solver, TaskQueue.SANDBOX: run_solver,
        TaskQueue.DATA: prepare_playback, TaskQueue.MESH: build_scene, TaskQueue.EVALUATOR: evaluate,
    }
    env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter)
    async with env:
        workers = [Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[SimulationJob])]
        workers += [Worker(env.client, task_queue=q.value, activities=[fn]) for q, fn in queues.items()]
        async with asyncio.TaskGroup() as tg:
            for w in workers:
                tg.create_task(w.run())
            try:
                if serve:
                    return await serve_api(env.client)
                return await drive(env.client, prompt, approve, timeout_min, JobRequest, ReviewDecision)
            finally:
                for w in workers:
                    tg.create_task(w.shutdown())


async def drive(client, prompt, approve, timeout_min, JobRequest, ReviewDecision) -> int:
    from omnific_common.contracts import JobOutcome, JobStatus

    job_id = uuid.uuid4().hex
    handle = await client.start_workflow(
        "SimulationJob", JobRequest(job_id=job_id, prompt=prompt), id=job_id, task_queue="workflow",
        result_type=JobOutcome,
    )
    print(f"job {job_id}: {prompt!r}")
    t0, last = time.time(), None
    while time.time() - t0 < timeout_min * 60:
        status = await handle.query("status", result_type=JobStatus)
        line = status.stage if status else None
        desc = await handle.describe()
        if line != last:
            print(f"  [{time.time() - t0:6.1f}s] {line}")
            last = line
        if desc.status.name != "RUNNING":
            break
        if line == "awaiting_review":
            break
        await asyncio.sleep(2)

    draft = await handle.query("draft", result_type=JobOutcome)
    if draft is None:
        print("no draft produced (timed out?)")
        return 1
    summarize(draft)
    if draft.status == "awaiting_review":
        if not approve:
            print("left awaiting review (--no-approve)")
            return 0
        await handle.signal("review", ReviewDecision(approved=True, reviewer="e2e-script"))
        outcome = await handle.result()
        print(f"final status: {outcome.status}")
        return 0 if outcome.status == "approved" else 1
    return 1


def summarize(o) -> None:
    print(f"\nstatus={o.status} revisions={o.revisions} {o.failure_reason}")
    if o.spec:
        s = o.spec
        print(f"spec: {s.title} | fidelity={s.fidelity.value} | {len(s.equations)} equations, "
              f"{len(s.parameters)} params, {len(s.state)} states")
        for a in s.assumptions[:6]:
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
    ap.add_argument("--no-approve", action="store_true")
    ap.add_argument("--timeout-min", type=float, default=30)
    a = ap.parse_args()
    if not a.serve and not a.prompt:
        ap.error("a prompt is required unless --serve is given")
    raise SystemExit(asyncio.run(main(a.prompt, not a.no_approve, a.timeout_min, a.serve)))
