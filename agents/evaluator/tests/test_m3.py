from omnific_common.llm import Role
from omnific_evaluator import activities
from omnific_sim.result import SimResult
from omnific_sim.spec import AnalyticCheck
from openai.lib._pydantic import to_strict_json_schema
from test_evaluate import by_name, make_result, make_spec, run


def perturbed(eps):
    r = make_result()
    return SimResult(t=r.t, state={k: v * (1 + eps) for k, v in r.state.items()}, units=r.units)


def test_critic_schema_strict():
    assert to_strict_json_schema(activities.CriticReport)


async def test_convergence_pass(tmp_path, monkeypatch):
    c = by_name(await run(make_spec(), make_result(), tmp_path, monkeypatch, fine=perturbed(1e-3)))
    assert c["convergence"].passed and "rel. diff" in c["convergence"].detail


async def test_convergence_fail(tmp_path, monkeypatch):
    rep = await run(make_spec(invariants=[]), make_result(), tmp_path, monkeypatch, fine=perturbed(0.1))
    c = by_name(rep)["convergence"]
    assert not c.passed and c.blame == "solver_coder" and not rep.passed
    assert "y:" in c.detail and "v:" in c.detail and "Not converged" in c.detail


async def test_convergence_tol_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_CONVERGENCE_TOL", "0.5")
    c = by_name(await run(make_spec(), make_result(), tmp_path, monkeypatch, fine=perturbed(0.1)))
    assert c["convergence"].passed


async def test_convergence_missing(tmp_path, monkeypatch):
    c = by_name(await run(make_spec(), make_result(), tmp_path, monkeypatch, fine=False))["convergence"]
    assert not c.passed and c.blame == "solver_coder"


def ac(**kw):
    base = dict(variable="y", expression="100 - g*t**2/2")  # noqa: C408
    base.update(kw)
    return make_spec(analytic_checks=[AnalyticCheck(**base)])


async def test_analytic_pass(tmp_path, monkeypatch):
    c = by_name(await run(ac(), make_result(), tmp_path, monkeypatch))["analytic[y]"]
    assert c.passed


async def test_analytic_tmax_limits_range(tmp_path, monkeypatch):
    # the reference is wrong for t > 2 only; t_max restricts the comparison to the correct early range
    spec = ac(expression="100 - g*t**2/2 + 5*Max(t - 2, 0)", t_max=2.0)
    assert by_name(await run(spec, make_result(), tmp_path, monkeypatch))["analytic[y]"].passed
    spec = ac(expression="100 - g*t**2/2 + 5*Max(t - 2, 0)")
    assert not by_name(await run(spec, make_result(), tmp_path, monkeypatch))["analytic[y]"].passed


async def test_analytic_mismatch_blames_solver(tmp_path, monkeypatch):
    spec = ac(expression="100 - g*t**2/2*1.1")
    c = by_name(await run(spec, make_result(), tmp_path, monkeypatch))["analytic[y]"]
    assert not c.passed and c.blame == "solver_coder"


async def test_analytic_bad_expression_blames_modeler(tmp_path, monkeypatch):
    for expr in ("100 - zeta*t", "100 - (t"):
        c = by_name(await run(ac(expression=expr), make_result(), tmp_path, monkeypatch))["analytic[y]"]
        assert not c.passed and c.blame == "modeler"
    c = by_name(await run(ac(variable="nope"), make_result(), tmp_path, monkeypatch))["analytic[nope]"]
    assert not c.passed and c.blame == "modeler"


class FakeLLM:
    def __init__(self, report):
        self.report, self.calls = report, []

    async def structured(self, role, instructions, input, schema, tools=None):
        self.calls.append((role, instructions, input, schema))
        return self.report


def issue(sev, blame, detail):
    return activities.CriticIssue(severity=sev, blame=blame, detail=detail)


async def test_critic_major_fails_minor_passes(tmp_path, monkeypatch):
    fake = FakeLLM(activities.CriticReport(summary="looks off", issues=[
        issue("minor", "solver_coder", "nit"), issue("major", "modeler", "bad")]))
    monkeypatch.setattr(activities, "llm", fake)
    rep = await run(make_spec(), make_result(), tmp_path, monkeypatch, critic=True,
                    prompt="drop a ball", code="CODE_X")
    c = by_name(rep)
    assert c["critic[0]"].passed and c["critic[0]"].blame is None and "nit" in c["critic[0]"].detail
    assert not c["critic[1]"].passed and c["critic[1]"].blame == "modeler"
    assert not rep.passed and rep.critic_summary == "looks off"
    role, _, text, schema = fake.calls[0]
    assert role is Role.GENERAL and schema is activities.CriticReport
    assert "drop a ball" in text and "CODE_X" in text and "assumptions" in text and "min=" in text


async def test_critic_clean_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(activities, "llm", FakeLLM(activities.CriticReport(summary="ok", issues=[])))
    rep = await run(make_spec(), make_result(), tmp_path, monkeypatch, critic=True)
    assert rep.passed and rep.critic_summary == "ok"


async def test_critic_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(activities, "llm", FakeLLM(None))
    rep = await run(make_spec(), make_result(), tmp_path, monkeypatch)
    c = by_name(rep)["critic"]
    assert c.passed and c.detail == "disabled" and rep.passed


async def test_critic_skipped_when_deterministic_fails(tmp_path, monkeypatch):
    fake = FakeLLM(activities.CriticReport(summary="x", issues=[]))
    monkeypatch.setattr(activities, "llm", fake)
    rep = await run(make_spec(), make_result(drift=0.05), tmp_path, monkeypatch, critic=True)
    assert not fake.calls and not rep.passed
    assert "skipped" in rep.critic_summary and "invariant[energy]" in rep.critic_summary
