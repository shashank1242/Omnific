"""Sandbox backends: run a harness against a prepared work dir.

Three harnesses share these backends: harness.py (solver code -> result.npz, the runner image), cad_harness.py
(CAD code -> result.zip, the CAD runner image) and blender_harness.py (Blender scene dressing -> result.zip, the
Blender runner image). The k8s backend (k8s_backend.py) runs the image as a Job under
gVisor; the docker backend here is the local/compose isolation.
"""

import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

MAX_LOG_BYTES = 20_000
HARNESS_PATH = Path(__file__).with_name("harness.py")
CAD_HARNESS_PATH = Path(__file__).with_name("cad_harness.py")
BLENDER_HARNESS_PATH = Path(__file__).with_name("blender_harness.py")
RESULT, CAD_RESULT = "result.npz", "result.zip"


def truncate(text: str, limit: int = MAX_LOG_BYTES) -> str:
    b = text.encode("utf-8", errors="replace")
    if len(b) <= limit:
        return text
    return b[:limit].decode("utf-8", errors="ignore") + f"\n... [truncated {len(b) - limit} bytes]"


@dataclass
class BackendResult:
    ok: bool
    stdout: str
    stderr: str
    duration_s: float
    result_path: Path | None = None  # the result file inside out_dir when ok


class SandboxBackend(Protocol):
    def run(self, in_dir: Path, out_dir: Path, timeout_s: int) -> BackendResult: ...


def _result(ok: bool, out: str, err: str, t0: float, out_dir: Path, result: str = RESULT) -> BackendResult:
    path = out_dir / result
    ok = ok and path.exists()
    if not ok and not err:
        err = f"run failed: no {result} produced"
    return BackendResult(ok, truncate(out), truncate(err), time.monotonic() - t0,
                         path if ok else None)


def _dec(x) -> str:
    return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")


class SubprocessBackend:
    """Local dev/tests only. NOT isolated: the solver runs with the worker's privileges."""

    def __init__(self, harness: Path = HARNESS_PATH, result: str = RESULT, python: str | None = None):
        self.harness, self.result, self.python = harness, result, python or sys.executable

    def run(self, in_dir: Path, out_dir: Path, timeout_s: int) -> BackendResult:
        log.warning(
            "SANDBOX_BACKEND=subprocess is NOT ISOLATED: generated code runs with full network and "
            "filesystem access. Use only for local development and tests."
        )
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="omnific-run-") as cwd:
            try:
                p = subprocess.run(
                    [self.python, str(self.harness), str(in_dir), str(out_dir)],
                    check=False, cwd=cwd, capture_output=True, timeout=timeout_s,
                )
            except subprocess.TimeoutExpired as e:
                err = _dec(e.stderr) + f"\nTimeoutError: run exceeded timeout_s={timeout_s}"
                return _result(False, _dec(e.stdout), err, t0, out_dir, self.result)
        return _result(p.returncode == 0, _dec(p.stdout), _dec(p.stderr), t0, out_dir, self.result)


class DockerBackend:
    def __init__(self, image: str, client=None, result: str = RESULT, mem: str = "2g", tmp: str = "256m",
                 cpus: float = 2.0):
        self.image = image
        self._client = client
        self.result = result
        self.mem, self.tmp, self.cpus = mem, tmp, cpus

    @property
    def client(self):
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def container_kwargs(self, in_dir: Path, out_dir: Path) -> dict:
        return dict(  # noqa: C408
            image=self.image,
            command=["/work/in", "/work/out"],
            detach=True,
            network_mode="none",
            read_only=True,
            tmpfs={"/tmp": f"rw,noexec,nosuid,size={self.tmp}"},
            mem_limit=self.mem,
            memswap_limit=self.mem,
            nano_cpus=int(self.cpus * 1_000_000_000),
            pids_limit=256,
            user="65534:65534",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            volumes={
                str(in_dir.resolve()): {"bind": "/work/in", "mode": "ro"},
                str(out_dir.resolve()): {"bind": "/work/out", "mode": "rw"},
            },
            working_dir="/tmp",
            environment={"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def run(self, in_dir: Path, out_dir: Path, timeout_s: int) -> BackendResult:
        t0 = time.monotonic()
        try:
            os.chmod(out_dir, 0o777)  # container user is non-root
        except OSError:
            pass
        container = self.client.containers.run(**self.container_kwargs(in_dir, out_dir))
        timed_out = False
        code = -1
        try:
            try:
                code = container.wait(timeout=timeout_s).get("StatusCode", -1)
            except Exception:  # noqa: BLE001 - requests timeout / connection error while waiting
                timed_out = True
                try:
                    container.kill()
                except Exception:
                    log.exception("failed to kill container")
            out = _dec(container.logs(stdout=True, stderr=False))
            err = _dec(container.logs(stdout=False, stderr=True))
        finally:
            try:
                container.remove(force=True)
            except Exception:
                log.exception("failed to remove container")
        if timed_out:
            err += f"\nTimeoutError: run exceeded timeout_s={timeout_s}; container killed"
        return _result(code == 0 and not timed_out, out, err, t0, out_dir, self.result)


def backend_from_env(cad: bool = False, blender: bool = False) -> SandboxBackend:
    """The solver backend, or with cad=True the CAD backend: same isolation, the CAD runner image
    (SANDBOX_CAD_IMAGE) and cad_harness.py. SANDBOX_CAD_BACKEND overrides the backend for CAD only (e.g. docker CAD
    runs next to subprocess solver runs in local dev). Subprocess CAD runs need build123d in CAD_PYTHON's env.
    blender=True: the Blender runner (SANDBOX_BLENDER_IMAGE, blender_harness.py, more memory); SANDBOX_BLENDER_BACKEND
    overrides the backend, and subprocess runs need `bpy` in BLENDER_PYTHON's env (Python 3.11)."""
    if blender:
        return _blender_backend()
    name = (os.environ.get("SANDBOX_CAD_BACKEND") if cad else None) or os.environ.get("SANDBOX_BACKEND", "docker")
    image_var, default_image = ("SANDBOX_CAD_IMAGE", "omnific-cad-runner:latest") if cad else (
        "SANDBOX_IMAGE", "omnific-runner:latest")
    result = CAD_RESULT if cad else RESULT
    if name == "subprocess":
        if cad:
            return SubprocessBackend(CAD_HARNESS_PATH, CAD_RESULT, os.environ.get("CAD_PYTHON") or None)
        return SubprocessBackend()
    if name == "docker":
        return DockerBackend(os.environ.get(image_var, default_image), result=result)
    if name == "k8s":
        from .k8s_backend import K8sJobBackend

        return K8sJobBackend(
            namespace=os.environ.get("SANDBOX_NAMESPACE", "omnific-sandbox-runs"),
            image=os.environ[image_var],
            runtime_class=os.environ.get("SANDBOX_RUNTIME_CLASS", "gvisor"),
            result=result,
        )
    raise ValueError(f"unknown SANDBOX_BACKEND {name!r}")


def _blender_backend() -> SandboxBackend:
    name = os.environ.get("SANDBOX_BLENDER_BACKEND") or os.environ.get("SANDBOX_BACKEND", "docker")
    image = os.environ.get("SANDBOX_BLENDER_IMAGE", "omnific-blender-runner:latest")
    if name == "subprocess":
        return SubprocessBackend(BLENDER_HARNESS_PATH, CAD_RESULT, os.environ.get("BLENDER_PYTHON") or None)
    if name == "docker":
        # Blender needs more room than a solver: ~1 GB resident, temp files for the .blend and renders
        return DockerBackend(image, result=CAD_RESULT, mem=os.environ.get("SANDBOX_BLENDER_MEMORY", "4g"),
                             tmp="1g", cpus=float(os.environ.get("SANDBOX_BLENDER_CPUS", "2")))
    if name == "k8s":
        from .k8s_backend import K8sJobBackend

        return K8sJobBackend(
            namespace=os.environ.get("SANDBOX_NAMESPACE", "omnific-sandbox-runs"),
            image=os.environ["SANDBOX_BLENDER_IMAGE"],
            runtime_class=os.environ.get("SANDBOX_RUNTIME_CLASS", "gvisor"),
            result=CAD_RESULT,
            memory_limit=os.environ.get("SANDBOX_BLENDER_MEMORY_K8S", "4Gi"),
        )
    raise ValueError(f"unknown SANDBOX_BLENDER_BACKEND {name!r}")
