"""Sandbox backends: run harness.py against a prepared work dir.

The k8s backend (k8s_backend.py) runs the runner image as a Job under gVisor; the docker backend here
is the local/compose isolation.
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
    result_path: Path | None = None  # npz inside out_dir when ok


class SandboxBackend(Protocol):
    def run(self, in_dir: Path, out_dir: Path, timeout_s: int) -> BackendResult: ...


def _result(ok: bool, out: str, err: str, t0: float, out_dir: Path) -> BackendResult:
    npz = out_dir / "result.npz"
    ok = ok and npz.exists()
    if not ok and not err:
        err = "run failed: no result.npz produced"
    return BackendResult(ok, truncate(out), truncate(err), time.monotonic() - t0,
                         npz if ok else None)


def _dec(x) -> str:
    return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")


class SubprocessBackend:
    """Local dev/tests only. NOT isolated: the solver runs with the worker's privileges."""

    def run(self, in_dir: Path, out_dir: Path, timeout_s: int) -> BackendResult:
        log.warning(
            "SANDBOX_BACKEND=subprocess is NOT ISOLATED: generated code runs with full network and "
            "filesystem access. Use only for local development and tests."
        )
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="omnific-run-") as cwd:
            try:
                p = subprocess.run(
                    [sys.executable, str(HARNESS_PATH), str(in_dir), str(out_dir)],
                    check=False, cwd=cwd, capture_output=True, timeout=timeout_s,
                )
            except subprocess.TimeoutExpired as e:
                err = _dec(e.stderr) + f"\nTimeoutError: run exceeded timeout_s={timeout_s}"
                return _result(False, _dec(e.stdout), err, t0, out_dir)
        return _result(p.returncode == 0, _dec(p.stdout), _dec(p.stderr), t0, out_dir)


class DockerBackend:
    def __init__(self, image: str, client=None):
        self.image = image
        self._client = client

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
            tmpfs={"/tmp": "rw,noexec,nosuid,size=256m"},
            mem_limit="2g",
            memswap_limit="2g",
            nano_cpus=2_000_000_000,
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
        return _result(code == 0 and not timed_out, out, err, t0, out_dir)


def backend_from_env() -> SandboxBackend:
    name = os.environ.get("SANDBOX_BACKEND", "docker")
    if name == "subprocess":
        return SubprocessBackend()
    if name == "docker":
        return DockerBackend(os.environ.get("SANDBOX_IMAGE", "omnific-runner:latest"))
    if name == "k8s":
        from .k8s_backend import K8sJobBackend

        return K8sJobBackend(
            namespace=os.environ.get("SANDBOX_NAMESPACE", "omnific-sandbox-runs"),
            image=os.environ["SANDBOX_IMAGE"],
            runtime_class=os.environ.get("SANDBOX_RUNTIME_CLASS", "gvisor"),
        )
    raise ValueError(f"unknown SANDBOX_BACKEND {name!r}")
