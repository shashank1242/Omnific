"""Runner harness. Executed INSIDE the isolated runner (container or subprocess), not in the worker.

Usage: python harness.py <input_dir> <output_dir>
  input_dir:  spec.json, solver.py, refine (optional text file with an integer >= 1, default 1)
  output_dir: result.npz is written here on success
Exit code 0 = success. Any failure prints a traceback / message to stderr and exits non-zero.
Keep this file dependency-light: it only needs omnific_sim and numpy.
"""

import base64
import importlib.util
import inspect
import json
import math
import os
import sys
import traceback
from pathlib import Path

# Kubernetes mode: the runner has no network or volume to the worker, so OMNIFIC_EMIT_RESULT=stdout makes
# the harness print the result npz base64-encoded between markers. Pod logs combine stdout and stderr, so
# error text is preceded by STDERR_MARKER.
BEGIN_MARKER = "-----OMNIFIC-RESULT-BEGIN-----"
END_MARKER = "-----END-----"
STDERR_MARKER = "-----OMNIFIC-STDERR-----"
# 6 MiB of npz is ~8 MiB of base64, under the kubelet default containerLogMaxSize (10Mi)
MAX_RESULT_BYTES = 6 * 1024 * 1024


def emit_mode() -> bool:
    return os.environ.get("OMNIFIC_EMIT_RESULT") == "stdout"


def _mark_stderr() -> None:
    if emit_mode():
        sys.stdout.flush()
        print(chr(10) + STDERR_MARKER, file=sys.stderr, flush=True)


def _excepthook(tp, val, tb) -> None:
    _mark_stderr()
    traceback.print_exception(tp, val, tb)


def emit_result(npz: Path) -> None:
    data = npz.read_bytes()
    if len(data) > MAX_RESULT_BYTES:
        fail(f"result.npz is {len(data)} bytes, over the {MAX_RESULT_BYTES} byte limit", 3)
    b64 = base64.b64encode(data).decode("ascii")
    nl = chr(10)
    sys.stdout.write(nl + BEGIN_MARKER + nl + b64 + nl + END_MARKER + nl)
    sys.stdout.flush()


def fail(msg: str, code: int = 2) -> None:
    _mark_stderr()
    print(msg, file=sys.stderr)
    sys.exit(code)


def validate(spec, result) -> None:
    import numpy as np
    from omnific_sim.result import SimResult
    from omnific_sim.units import same_dimension

    if not isinstance(result, SimResult):
        fail(f"simulate() must return SimResult, got {type(result).__name__}")
    t = np.asarray(result.t)
    n = spec.time.output_samples
    if t.shape != (n,):
        fail(f"result.t must have shape ({n},) (spec.time.output_samples), got {t.shape}")
    if not np.all(np.isfinite(t)):
        fail("result.t contains non-finite values")
    for sv in spec.state:
        if sv.name not in result.state:
            fail(f"state variable {sv.name!r} missing from result.state")
        if sv.name not in result.units:
            fail(f"units for state variable {sv.name!r} missing from result.units")
        unit = result.units[sv.name]
        try:
            ok = same_dimension(unit, sv.unit)
        except Exception as e:  # noqa: BLE001
            fail(f"invalid unit {unit!r} for {sv.name!r}: {e}")
        if not ok:
            fail(f"unit {unit!r} for {sv.name!r} is not dimensionally compatible with {sv.unit!r}")
        v = np.asarray(result.state[sv.name], dtype=float)
        if v.ndim == 0 or v.shape[0] != n:
            fail(f"state {sv.name!r} first axis must have length {n}, got shape {v.shape}")
        if not np.all(np.isfinite(v)):
            fail(f"state {sv.name!r} contains non-finite values")
    for k, v in result.meta.items():
        if isinstance(v, float) and not math.isfinite(v):
            fail(f"meta {k!r} is not finite")


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        fail("usage: harness.py <input_dir> <output_dir>", 64)
    in_dir, out_dir = Path(argv[1]), Path(argv[2])
    from omnific_sim.spec import SimSpec

    spec = SimSpec.model_validate(json.loads((in_dir / "spec.json").read_text()))
    refine = 1
    refine_file = in_dir / "refine"
    if refine_file.exists():
        try:
            refine = int(refine_file.read_text().strip())
            if refine < 1:
                raise ValueError
        except ValueError:
            fail(f"invalid refine file contents: {refine_file.read_text()!r}")
    sys.path.insert(0, str(in_dir))
    mod_spec = importlib.util.spec_from_file_location("solver", in_dir / "solver.py")
    module = importlib.util.module_from_spec(mod_spec)
    try:
        mod_spec.loader.exec_module(module)
        sim = getattr(module, "simulate", None)
        if sim is None:
            fail("solver.py does not define simulate(spec)")
        if "refine" in inspect.signature(sim).parameters:
            result = sim(spec, refine=refine)
        elif refine == 1:
            result = sim(spec)
        else:
            fail(f"simulate() must accept a `refine` parameter (called with refine={refine})")
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        _mark_stderr()
        traceback.print_exc()
        sys.exit(1)
    validate(spec, result)
    out_dir.mkdir(parents=True, exist_ok=True)
    result.save_npz(str(out_dir / "result.npz"))
    if emit_mode():
        emit_result(out_dir / "result.npz")


if __name__ == "__main__":
    sys.excepthook = _excepthook
    main(sys.argv)
