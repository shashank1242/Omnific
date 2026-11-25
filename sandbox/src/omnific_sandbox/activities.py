import asyncio
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

from omnific_common.contracts import (
    ACTIVITY,
    BLENDER_RUN_ACTIVITY,
    CAD_RUN_ACTIVITY,
    BlenderRunInput,
    BlenderRunOutput,
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
get_blender_backend = lambda: backend_from_env(blender=True)
MAX_CAD_UNPACKED = 200 * 1024 * 1024  # all members of one CAD result.zip, uncompressed
MAX_BLENDER_UNPACKED = 400 * 1024 * 1024  # all members of one Blender result.zip, uncompressed
BLENDER_FILES = {"report.json": "application/json", "scene.gltf": "model/gltf+json",
                 "scene.blend": "application/x-blender"}


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


@activity.defn(name=BLENDER_RUN_ACTIVITY)
async def run_blender(inp: BlenderRunInput) -> BlenderRunOutput:
    """Dress (LLM-written Blender scripts) or export a scene in the Blender runner: same isolation as solver code.
    The archive comes from a runner that executed LLM-written code, so only known members are unpacked, bounded."""
    store = get_store()
    with tempfile.TemporaryDirectory(prefix="omnific-blender-", dir=_work_root()) as work:
        in_dir, out_dir = Path(work) / "in", Path(work) / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "scene.gltf").write_bytes(await asyncio.to_thread(store.get, inp.scene_key))
        (in_dir / "playback.json").write_bytes(await asyncio.to_thread(store.get, inp.playback_key))
        props = {}
        for pr in inp.props:  # only this job's stored props, under safe names: the runner reads them as files
            if not re.fullmatch(r"[a-z0-9_]{1,80}", pr.id) or pr.key != job_key(inp.job_id, f"props/{pr.id}.glb"):
                continue
            (in_dir / "props").mkdir(exist_ok=True)
            (in_dir / "props" / f"{pr.id}.glb").write_bytes(await asyncio.to_thread(store.get, pr.key))
            props[pr.id] = {"name": pr.name, "credit": pr.credit}
        (in_dir / "job.json").write_text(json.dumps({"mode": inp.mode, "entities": inp.entities, "fps": 30,
                                                     "stills": inp.stills if inp.mode == "dress" else 0,
                                                     "props": props}), encoding="utf-8")
        if inp.scripts:
            (in_dir / "scripts").mkdir()
            for i, sc in enumerate(inp.scripts):
                code = sc.code
                store.put(job_key(inp.job_id, f"{inp.prefix}/{i:02d}_{sc.agent}.py"), code.encode("utf-8"),
                          "text/x-python")
                (in_dir / "scripts" / f"{i:02d}_{sc.agent}.py").write_text(code, encoding="utf-8")
        try:
            res = await asyncio.to_thread(get_blender_backend().run, in_dir, out_dir, inp.timeout_s)
        except Exception as e:  # noqa: BLE001 - infrastructure failure: report it, the workflow decides
            return BlenderRunOutput(ok=False, stderr=truncate(f"Blender sandbox error: {e!r}"))
        if not res.ok or res.result_path is None:
            return BlenderRunOutput(ok=False, stdout=res.stdout, stderr=res.stderr, duration_s=res.duration_s)
        out = BlenderRunOutput(ok=True, stdout=res.stdout, stderr=res.stderr, duration_s=res.duration_s)
        try:
            files = await asyncio.to_thread(read_blender_result, res.result_path, inp.mode)
        except (zipfile.BadZipFile, OSError, KeyError) as e:
            return BlenderRunOutput(ok=False, stdout=res.stdout, stderr=truncate(f"invalid Blender result: {e}"),
                                    duration_s=res.duration_s)
        for name, data in files.items():  # every member read and checked before anything is stored
            key = job_key(inp.job_id, f"{inp.prefix}/{name}")
            await asyncio.to_thread(store.put, key, data, BLENDER_FILES.get(name, "image/png"))
            if name == "report.json":
                out.report_key = key
            elif name == "scene.gltf":
                out.scene_key = key
            elif name == "scene.blend":
                out.blend_key = key
            else:
                out.still_keys.append(key)
    return out


def read_blender_result(path: Path, mode: str) -> dict[str, bytes]:
    """The known members of a Blender result archive, bounded. A dress run must return the report, the dressed scene
    and the .blend; an export the report and the .blend. Stills are optional (rendering may fail)."""
    with zipfile.ZipFile(path) as z:
        infos = {i.filename: i for i in z.infolist()}
        stills = sorted(n for n in infos if n.startswith("stills/") and n.endswith(".png") and n.count("/") == 1)[:8]
        allowed = [n for n in infos if n in BLENDER_FILES] + stills
        need = {"report.json", "scene.blend"} | ({"scene.gltf"} if mode == "dress" else set())
        if not need <= set(infos):
            raise zipfile.BadZipFile(f"Blender result is missing {sorted(need - set(infos))}")
        if sum(infos[n].file_size for n in allowed) > MAX_BLENDER_UNPACKED:
            raise zipfile.BadZipFile(f"Blender result exceeds {MAX_BLENDER_UNPACKED // 2**20} MiB unpacked")
        return {n: z.read(n) for n in allowed}
