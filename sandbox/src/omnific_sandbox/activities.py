import asyncio
import os
import tempfile
import zipfile
from pathlib import Path

from omnific_common.contracts import (
    ACTIVITY,
    CAD_RUN_ACTIVITY,
    CadRunInput,
    CadRunOutput,
    RunInput,
    RunOutput,
    TaskQueue,
)
from omnific_common.storage import job_key, store_from_env
from temporalio import activity

from .backends import backend_from_env, truncate

# Module-level hooks so tests can monkeypatch them.
get_store = store_from_env
get_backend = backend_from_env
get_cad_backend = lambda: backend_from_env(cad=True)
MAX_CAD_UNPACKED = 200 * 1024 * 1024  # all members of one CAD result.zip, uncompressed


def _work_root() -> str | None:
    # With the docker backend from inside a container, SANDBOX_WORK_DIR must be mounted at the same
    # path on the Docker host, because the runner container's bind mounts resolve on the host.
    work_root = os.environ.get("SANDBOX_WORK_DIR") or None
    if work_root:
        os.makedirs(work_root, exist_ok=True)
    return work_root


@activity.defn(name=ACTIVITY[TaskQueue.SANDBOX])
async def run_solver(inp: RunInput) -> RunOutput:
    store = get_store()
    prefix = f"attempt-{inp.attempt}" + (f"-refine{inp.refine}" if inp.refine > 1 else "")
    code_key = job_key(inp.job_id, f"{prefix}/solver.py")
    store.put(code_key, inp.code.code.encode("utf-8"), "text/x-python")

    with tempfile.TemporaryDirectory(prefix="omnific-work-", dir=_work_root()) as work:
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


@activity.defn(name=CAD_RUN_ACTIVITY)
async def run_cad(inp: CadRunInput) -> CadRunOutput:
    """Run LLM-written build123d code in the CAD runner (same isolation as solver code)."""
    store = get_store()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in inp.entity)[:60]
    prefix = f"assets/{safe}/cad-{inp.attempt}"
    code_key = job_key(inp.job_id, f"{prefix}.py")
    store.put(code_key, inp.code.encode("utf-8"), "text/x-python")
    with tempfile.TemporaryDirectory(prefix="omnific-cad-", dir=_work_root()) as work:
        in_dir, out_dir = Path(work) / "in", Path(work) / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "cad.py").write_text(inp.code, encoding="utf-8")
        try:
            res = await asyncio.to_thread(get_cad_backend().run, in_dir, out_dir, inp.timeout_s)
        except Exception as e:  # noqa: BLE001 - infrastructure failure: report it, the workflow decides
            return CadRunOutput(ok=False, code_key=code_key, stderr=truncate(f"CAD sandbox error: {e!r}"))
        result_key = step_key = None
        if res.ok and res.result_path is not None:
            # the archive comes from a runner that executed LLM-written code: bound what we unpack
            try:
                with zipfile.ZipFile(res.result_path) as z:
                    sizes = {i.filename: i.file_size for i in z.infolist()}
                    if sum(sizes.values()) > MAX_CAD_UNPACKED or not {"cad.json", "parts.npz"} <= set(sizes):
                        raise zipfile.BadZipFile(f"unexpected CAD result contents: {sizes}")
                    step = z.read("model.step") if "model.step" in sizes else None
            except (zipfile.BadZipFile, OSError) as e:
                return CadRunOutput(ok=False, code_key=code_key, stdout=res.stdout,
                                    stderr=truncate(f"invalid CAD result archive: {e}"), duration_s=res.duration_s)
            result_key = job_key(inp.job_id, f"{prefix}.zip")
            store.put(result_key, res.result_path.read_bytes(), "application/zip")
            if step:
                step_key = job_key(inp.job_id, f"{prefix}.step")
                store.put(step_key, step, "model/step")
    return CadRunOutput(ok=res.ok, code_key=code_key, result_key=result_key, step_key=step_key, stdout=res.stdout,
                        stderr=res.stderr, duration_s=res.duration_s)
