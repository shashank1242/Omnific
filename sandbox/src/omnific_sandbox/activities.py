import asyncio
import os
import tempfile
from pathlib import Path

from omnific_common.contracts import ACTIVITY, RunInput, RunOutput, TaskQueue
from omnific_common.storage import job_key, store_from_env
from temporalio import activity

from .backends import backend_from_env, truncate

# Module-level hooks so tests can monkeypatch them.
get_store = store_from_env
get_backend = backend_from_env


@activity.defn(name=ACTIVITY[TaskQueue.SANDBOX])
async def run_solver(inp: RunInput) -> RunOutput:
    store = get_store()
    prefix = f"attempt-{inp.attempt}" + (f"-refine{inp.refine}" if inp.refine > 1 else "")
    code_key = job_key(inp.job_id, f"{prefix}/solver.py")
    store.put(code_key, inp.code.code.encode("utf-8"), "text/x-python")

    # With the docker backend from inside a container, SANDBOX_WORK_DIR must be mounted at the same
    # path on the Docker host, because the runner container's bind mounts resolve on the host.
    work_root = os.environ.get("SANDBOX_WORK_DIR") or None
    if work_root:
        os.makedirs(work_root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="omnific-work-", dir=work_root) as work:
        in_dir, out_dir = Path(work) / "in", Path(work) / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "spec.json").write_text(inp.spec.model_dump_json(), encoding="utf-8")
        (in_dir / "refine").write_text(str(inp.refine), encoding="utf-8")
        (in_dir / "solver.py").write_text(inp.code.code, encoding="utf-8")
        try:
            res = await asyncio.to_thread(get_backend().run, in_dir, out_dir, inp.timeout_s)
        except Exception as e:  # noqa: BLE001 - infrastructure failure: report it, the workflow decides
            return RunOutput(ok=False, code_key=code_key, stderr=truncate(f"sandbox error: {e!r}"))
        result_key = None
        if res.ok and res.result_path is not None:
            result_key = job_key(inp.job_id, f"{prefix}/result.npz")
            store.put(result_key, res.result_path.read_bytes())
    return RunOutput(ok=res.ok, result_key=result_key, code_key=code_key, stdout=res.stdout,
                     stderr=res.stderr, duration_s=res.duration_s)
