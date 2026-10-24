from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from omnific_common.contracts import RunInput, SolverCode
from omnific_common.storage import LocalStore
from omnific_sandbox import activities
from omnific_sandbox.backends import DockerBackend, SubprocessBackend, truncate
from omnific_sim.result import SimResult
from omnific_sim.spec import Domain, Fidelity, Parameter, SimSpec, StateVariable, TimeSpan

N = 50

GOOD = '''
import numpy as np
from omnific_sim.result import SimResult

def simulate(spec):
    g = spec.parameter("g").value
    v0 = spec.parameter("v0").value
    t = np.linspace(spec.time.start, spec.time.end, spec.time.output_samples)
    return SimResult(t=t, state={"x": 0.5 * v0 * t, "y": v0 * t - 0.5 * g * t**2},
                     units={"x": "m", "y": "m"})
'''


def make_spec() -> SimSpec:
    return SimSpec(
        title="Projectile", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.ANALYTIC,
        assumptions=[],
        parameters=[Parameter(name="g", value=9.81, unit="m/s**2"),
                    Parameter(name="v0", value=10.0, unit="m/s")],
        state=[StateVariable(name="x", unit="m", initial_value=0),
               StateVariable(name="y", unit="m", initial_value=0)],
        equations=[], time=TimeSpan(end=1.0, output_samples=N),
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "store")
    monkeypatch.setattr(activities, "get_store", lambda: store)
    monkeypatch.setattr(activities, "get_backend", lambda: SubprocessBackend())
    return store


def run(code: str, timeout_s: int = 60, refine: int = 1):
    inp = RunInput(job_id="j1", attempt=2, spec=make_spec(), code=SolverCode(code=code),
                   timeout_s=timeout_s, refine=refine)
    return activities.run_solver(inp)


async def test_happy_path(env, tmp_path):
    out = await run(GOOD)
    assert out.ok, out.stderr
    assert out.code_key == "jobs/j1/attempt-2/solver.py"
    assert out.result_key == "jobs/j1/attempt-2/result.npz"
    assert env.get(out.code_key).decode().strip().startswith("import numpy")
    p = tmp_path / "r.npz"
    p.write_bytes(env.get(out.result_key))
    res = SimResult.load_npz(str(p))
    assert res.t.shape == (N,)
    assert np.isclose(res.state["y"][-1], 10.0 - 0.5 * 9.81)


REFINE_CODE = '''
import numpy as np
from omnific_sim.result import SimResult

def simulate(spec, refine=1):
    t = np.linspace(spec.time.start, spec.time.end, spec.time.output_samples)
    return SimResult(t=t, state={"x": 0 * t + refine, "y": 0 * t}, units={"x": "m", "y": "m"})
'''


async def test_refine_plumbing_and_keys(env, tmp_path):
    for r, key in ((1, "attempt-2"), (3, "attempt-2-refine3")):
        out = await run(REFINE_CODE, refine=r)
        assert out.ok, out.stderr
        assert out.code_key == f"jobs/j1/{key}/solver.py"
        assert out.result_key == f"jobs/j1/{key}/result.npz"
        p = tmp_path / "r.npz"
        p.write_bytes(env.get(out.result_key))
        assert SimResult.load_npz(str(p)).state["x"][0] == r


EULER = '''
import numpy as np
from omnific_sim.result import SimResult

def simulate(spec, refine=1):
    t = np.linspace(spec.time.start, spec.time.end, spec.time.output_samples)
    n = 20 * refine * (spec.time.output_samples - 1)  # fixed-step explicit Euler for dx/dt = -x
    h = (spec.time.end - spec.time.start) / n
    x = np.empty(n + 1)
    x[0] = 1.0
    for i in range(n):
        x[i + 1] = x[i] * (1 - h)
    grid = np.linspace(spec.time.start, spec.time.end, n + 1)
    xs = np.interp(t, grid, x)
    return SimResult(t=t, state={"x": xs, "y": 0 * t}, units={"x": "m", "y": "m"},
                     meta={"refine": float(refine)})
'''


async def test_refine_end_to_end_convergence(env, tmp_path):
    from omnific_sim.primitives.checks import convergence_ratio

    res = {}
    for r in (1, 2):
        out = await run(EULER, refine=r)
        assert out.ok, out.stderr
        p = tmp_path / f"r{r}.npz"
        p.write_bytes(env.get(out.result_key))
        res[r] = SimResult.load_npz(str(p))
    assert res[1].t.shape == res[2].t.shape == (N,)
    assert not np.array_equal(res[1].state["x"], res[2].state["x"])
    assert convergence_ratio(res[1], res[2], "x") < 1e-2


async def test_solver_without_refine_param(env):
    out = await run(GOOD, refine=2)
    assert not out.ok and "refine" in out.stderr


async def test_solver_raises(env):
    out = await run("def simulate(spec):\n    raise ValueError('boom')\n")
    assert not out.ok and out.result_key is None
    assert "Traceback" in out.stderr and "ValueError: boom" in out.stderr


async def test_timeout(env):
    out = await run("def simulate(spec):\n    while True:\n        pass\n", timeout_s=2)
    assert not out.ok
    assert "Timeout" in out.stderr
    assert out.duration_s < 30


async def test_missing_state_variable(env):
    code = GOOD.replace('"y": v0 * t - 0.5 * g * t**2', '"z": t').replace('"y": "m"', '"z": "m"')
    out = await run(code)
    assert not out.ok
    assert "'y' missing" in out.stderr


async def test_wrong_units_and_length_and_nan(env):
    out = await run(GOOD.replace('"x": "m"', '"x": "s"'))
    assert not out.ok and "dimensionally" in out.stderr
    out = await run(GOOD.replace("spec.time.output_samples", "10"))
    assert not out.ok and "shape" in out.stderr
    out = await run(GOOD.replace("0.5 * v0 * t", "np.nan * t"))
    assert not out.ok and "non-finite" in out.stderr


async def test_not_simresult(env):
    out = await run("def simulate(spec):\n    return 3\n")
    assert not out.ok and "SimResult" in out.stderr


def test_truncate():
    s = truncate("a" * 50_000)
    assert len(s) < 20_100 and "truncated" in s


def test_docker_kwargs(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    kw = DockerBackend("img:1").container_kwargs(tmp_path / "in", tmp_path / "out")
    assert kw["image"] == "img:1"
    assert kw["network_mode"] == "none" and kw["read_only"] is True
    assert kw["tmpfs"].keys() == {"/tmp"}
    assert kw["mem_limit"] == "2g" and kw["nano_cpus"] == 2_000_000_000
    assert kw["pids_limit"] == 256 and kw["cap_drop"] == ["ALL"]
    assert "no-new-privileges" in kw["security_opt"]
    assert kw["user"] not in ("", "0", "root", "0:0")
    modes = {v["bind"]: v["mode"] for v in kw["volumes"].values()}
    assert modes == {"/work/in": "ro", "/work/out": "rw"}


def _mock_client(wait_side_effect=None, code=0, write: Path | None = None):
    c = MagicMock()
    cont = c.containers.run.return_value
    if wait_side_effect:
        cont.wait.side_effect = wait_side_effect
    else:
        cont.wait.return_value = {"StatusCode": code}
    cont.logs.side_effect = lambda stdout, stderr: b"out" if stdout else b"err"
    return c, cont


def test_docker_run_success_and_cleanup(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "result.npz").write_bytes(b"x")
    c, cont = _mock_client()
    r = DockerBackend("img", client=c).run(tmp_path / "in", tmp_path / "out", 5)
    assert r.ok and r.stdout == "out"
    cont.remove.assert_called_once_with(force=True)
    cont.wait.assert_called_once_with(timeout=5)


def test_docker_run_timeout_kills(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    c, cont = _mock_client(wait_side_effect=TimeoutError())
    r = DockerBackend("img", client=c).run(tmp_path / "in", tmp_path / "out", 5)
    assert not r.ok and "Timeout" in r.stderr
    cont.kill.assert_called_once()
    cont.remove.assert_called_once_with(force=True)
