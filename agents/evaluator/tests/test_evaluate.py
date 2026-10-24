import numpy as np
from omnific_common.contracts import EvalInput, SolverCode
from omnific_common.storage import LocalStore
from omnific_evaluator import activities
from omnific_sim.result import SimResult
from omnific_sim.spec import (
    Bound,
    Domain,
    Equation,
    Fidelity,
    Invariant,
    Parameter,
    SimSpec,
    StateVariable,
    TimeSpan,
)

G, M = 9.81, 2.0
N = 101


def make_spec(**over) -> SimSpec:
    base = dict(  # noqa: C408
        title="Fall", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.ANALYTIC,
        assumptions=[],
        parameters=[Parameter(name="g", value=G, unit="m/s**2"),
                    Parameter(name="m", value=M, unit="kg")],
        state=[StateVariable(name="y", unit="m", initial_value=100.0),
               StateVariable(name="v", unit="m/s", initial_value=0.0)],
        equations=[Equation(label="newton", expression="dv_dt = -g"),
                   Equation(label="kin", expression="dy_dt = v")],
        time=TimeSpan(end=4.0, output_samples=N),
        invariants=[Invariant(name="energy", expression="m*g*y + m*v**2/2", rel_tolerance=1e-6)],
        bounds=[Bound(variable="y", min=0.0)],
    )
    base.update(over)
    return SimSpec(**base)


def make_result(drift=0.0) -> SimResult:
    t = np.linspace(0, 4.0, N)
    y = 100 - 0.5 * G * t**2
    v = -G * t * (1 + drift)
    return SimResult(t=t, state={"y": y, "v": v}, units={"y": "m", "v": "m/s"})


def by_name(report):
    return {c.name: c for c in report.checks}


async def run(spec, result, tmp_path, monkeypatch, fine=True, critic=False, prompt="p", code=""):
    """fine: True = same as result, False/None = no refined result, SimResult = that result."""
    store = LocalStore(tmp_path)
    p = tmp_path / "r.npz"
    result.save_npz(str(p))
    store.put("jobs/j/r.npz", p.read_bytes())
    fine_key = None
    if fine is not None and fine is not False:
        (result if fine is True else fine).save_npz(str(p))
        store.put("jobs/j/r2.npz", p.read_bytes())
        fine_key = "jobs/j/r2.npz"
    monkeypatch.setattr(activities, "get_store", lambda: store)
    monkeypatch.setenv("EVAL_CRITIC", "on" if critic else "off")
    inp = EvalInput(job_id="j", prompt=prompt, spec=spec, code=SolverCode(code=code),
                    result_key="jobs/j/r.npz", fine_result_key=fine_key)
    return await activities.evaluate(inp)


async def test_all_pass(tmp_path, monkeypatch):
    rep = await run(make_spec(), make_result(), tmp_path, monkeypatch)
    assert rep.passed, [c for c in rep.checks if not c.passed]
    names = by_name(rep)
    assert {"dimensions[newton]", "dimensions[kin]", "finiteness", "bounds[y]",
            "invariant[energy]", "convergence", "critic"} <= names.keys()


async def test_dimension_error_blames_modeler(tmp_path, monkeypatch):
    spec = make_spec(equations=[Equation(label="bad", expression="dv_dt = -g*m")])
    rep = await run(spec, make_result(), tmp_path, monkeypatch)
    c = by_name(rep)["dimensions[bad]"]
    assert not rep.passed and not c.passed and c.blame == "modeler"


async def test_unknown_symbol_blames_modeler(tmp_path, monkeypatch):
    spec = make_spec(equations=[Equation(label="u", expression="v = zeta*t")])
    c = by_name(await run(spec, make_result(), tmp_path, monkeypatch))["dimensions[u]"]
    assert not c.passed and c.blame == "modeler"


async def test_bound_violation_blames_solver(tmp_path, monkeypatch):
    spec = make_spec(time=TimeSpan(end=6.0, output_samples=N))
    t = np.linspace(0, 6.0, N)
    res = SimResult(t=t, state={"y": 100 - 0.5 * G * t**2, "v": -G * t},
                    units={"y": "m", "v": "m/s"})
    c = by_name(await run(spec, res, tmp_path, monkeypatch))["bounds[y]"]
    assert not c.passed and c.blame == "solver_coder"


async def test_bound_violated_by_initial_condition_blames_modeler(tmp_path, monkeypatch):
    spec = make_spec(bounds=[Bound(variable="y", max=50.0)])
    c = by_name(await run(spec, make_result(), tmp_path, monkeypatch))["bounds[y]"]
    assert not c.passed and c.blame == "modeler"


async def test_invariant_drift_blames_solver(tmp_path, monkeypatch):
    rep = await run(make_spec(), make_result(drift=0.05), tmp_path, monkeypatch)
    c = by_name(rep)["invariant[energy]"]
    assert not c.passed and c.blame == "solver_coder" and not rep.passed


async def test_invariant_bad_expression_blames_modeler(tmp_path, monkeypatch):
    spec = make_spec(invariants=[Invariant(name="e", expression="m*nope")])
    c = by_name(await run(spec, make_result(), tmp_path, monkeypatch))["invariant[e]"]
    assert not c.passed and c.blame == "modeler"


async def test_unit_conversion_in_bounds(tmp_path, monkeypatch):
    t = np.linspace(0, 4.0, N)
    y_km = (100 - 0.5 * G * t**2) / 1000
    res = SimResult(t=t, state={"y": y_km, "v": -G * t}, units={"y": "km", "v": "m/s"})
    rep = await run(make_spec(invariants=[]), res, tmp_path, monkeypatch)
    assert by_name(rep)["bounds[y]"].passed


async def test_nonfinite(tmp_path, monkeypatch):
    res = make_result()
    res.state["v"][5] = np.nan
    rep = await run(make_spec(invariants=[]), res, tmp_path, monkeypatch)
    c = by_name(rep)["finiteness"]
    assert not c.passed and c.blame == "solver_coder"
