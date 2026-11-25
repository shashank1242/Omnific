"""GPU model backends behind small interfaces. Each upstream model runs as a subprocess in its own environment
(their CUDA/PyTorch pins conflict with each other and with our Python 3.11 venv), exchanging files in a temp dir.

Configured by env:
  MESH_GEN_BACKEND   trellis2 (default) | fake    (fake = deterministic stand-ins: exercise the pipeline on CPU)
  MESH_RIG_BACKEND   unirig (default) | none | fake
  MESH_ANIM_BACKEND  none (default) | fake    (no text-to-motion model with a compatible licence yet; future.md)
  TRELLIS_PYTHON / TRELLIS_DIR, UNIRIG_DIR    interpreter and checkouts inside the GPU image
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

RUNNERS = Path(__file__).parent / "runners"


class Unavailable(RuntimeError):
    """The stage has no backend configured on this worker (the workflow falls back without retrying)."""


class Generator(Protocol):
    async def generate(self, image_png: bytes, seed: int, decimation_target: int, texture_size: int) -> bytes: ...


class Rigger(Protocol):
    async def rig(self, glb: bytes) -> bytes: ...


class Animator(Protocol):
    async def animate(self, rigged_glb: bytes, clips: list[dict]) -> bytes: ...


async def _run(cmd: list[str], cwd: str | None = None, timeout_s: float = 3600,
               env: dict[str, str] | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, env=env)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(f"{Path(cmd[0]).name} timed out after {timeout_s:.0f}s") from None
    text = out.decode(errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} exited {proc.returncode}: {text[-2000:]}")
    log.info("%s ok: %s", Path(cmd[1] if len(cmd) > 1 else cmd[0]).name, text[-500:])


class Trellis2:
    """microsoft/TRELLIS.2 (MIT) image-to-3D, via runners/trellis_generate.py in the trellis2 conda env."""

    def __init__(self):
        self.python = os.environ.get("TRELLIS_PYTHON", "/opt/conda/envs/trellis2/bin/python")
        self.cwd = os.environ.get("TRELLIS_DIR", "/opt/TRELLIS.2")

    def env(self) -> dict[str, str]:
        """The runner is a script outside the checkout, so `cwd` alone doesn't make `trellis2` importable
        (Python puts the script's own directory on sys.path, not the working directory)."""
        prev = os.environ.get("PYTHONPATH", "")
        return {**os.environ, "PYTHONPATH": self.cwd + (os.pathsep + prev if prev else "")}

    async def generate(self, image_png: bytes, seed: int, decimation_target: int, texture_size: int) -> bytes:
        with tempfile.TemporaryDirectory() as d:
            img, out = Path(d, "in.png"), Path(d, "out.glb")
            img.write_bytes(image_png)
            await _run([self.python, str(RUNNERS / "trellis_generate.py"), "--image", str(img), "--out", str(out),
                        "--seed", str(seed), "--decimation-target", str(decimation_target),
                        "--texture-size", str(texture_size)], cwd=self.cwd, env=self.env())
            return out.read_bytes()


class UniRig:
    """VAST-AI-Research/UniRig (MIT): skeleton, then skinning, then merge back onto the mesh -> rigged GLB."""

    def __init__(self):
        self.cwd = os.environ.get("UNIRIG_DIR", "/opt/UniRig")
        self.bash = shutil.which("bash") or "bash"
        # its launch scripts call `python`: resolve it to UniRig's env, not our venv
        py = os.environ.get("UNIRIG_PYTHON", "/opt/conda/envs/unirig/bin/python")
        # torch >= 2.6 loads checkpoints weights-only; UniRig's skin checkpoint pickles its config (box.Box). It comes
        # from the same UniRig release whose code we run, so full loading adds no new trust; set for UniRig only.
        self.env = {**os.environ, "PATH": f"{Path(py).parent}{os.pathsep}{os.environ.get('PATH', '')}",
                    "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1"}

    async def rig(self, glb: bytes) -> bytes:
        with tempfile.TemporaryDirectory() as d:
            src, skel, skin, out = (Path(d, n) for n in ("in.glb", "skeleton.fbx", "skin.fbx", "rigged.glb"))
            src.write_bytes(glb)
            await _run([self.bash, "launch/inference/generate_skeleton.sh", "--input", str(src), "--output", str(skel)],
                       cwd=self.cwd, env=self.env)
            await _run([self.bash, "launch/inference/generate_skin.sh", "--input", str(skel), "--output", str(skin)],
                       cwd=self.cwd, env=self.env)
            # merge the SKIN result (merging the skeleton file would carry no weights)
            await _run([self.bash, "launch/inference/merge.sh", "--source", str(skin), "--target", str(src),
                        "--output", str(out)], cwd=self.cwd, env=self.env)
            return out.read_bytes()


class Disabled:
    def __init__(self, what: str):
        self.what = what

    async def rig(self, glb: bytes) -> bytes:
        raise Unavailable(f"no {self.what} backend configured")

    async def animate(self, rigged_glb: bytes, clips: list[dict]) -> bytes:
        raise Unavailable(f"no {self.what} backend configured")

    async def generate(self, *a, **k) -> bytes:
        raise Unavailable(f"no {self.what} backend configured")


def generator() -> Generator:
    name = os.environ.get("MESH_GEN_BACKEND", "trellis2")
    if name == "trellis2":
        return Trellis2()
    if name == "fake":
        from .fakes import FakeGenerator
        return FakeGenerator()
    return Disabled("3D generation")


def rigger() -> Rigger:
    name = os.environ.get("MESH_RIG_BACKEND", "unirig")
    if name == "unirig":
        return UniRig()
    if name == "fake":
        from .fakes import FakeRigger
        return FakeRigger()
    return Disabled("rigging")


def animator() -> Animator:
    name = os.environ.get("MESH_ANIM_BACKEND", "none")
    if name == "fake":
        from .fakes import FakeAnimator
        return FakeAnimator()
    return Disabled("ML animation")

