import pytest
from omnific_common.contracts import ModelInput, ResearchOutput
from omnific_modeler import activities
from omnific_modeler.validation import validate_spec
from omnific_sim.spec import SimSpec
from openai.lib._pydantic import to_strict_json_schema
from temporalio.exceptions import ApplicationError

GOOD = {
    "title": "Cooling body", "prompt": "cool a body", "domains": ["thermal"], "fidelity": "heuristic",
    "assumptions": ["lumped"],
    "parameters": [
        {"name": "k", "value": 0.1, "unit": "1/second"},
        {"name": "T_inf", "value": 293.0, "unit": "kelvin"},
        {"name": "g", "value": 9.8, "unit": "meter/second**2"},
    ],
    "state": [{"name": "T", "unit": "kelvin", "initial_value": 350.0}],
    "equations": [{"label": "cool", "expression": "dT_dt = -k*(T - T_inf)"}],
    "time": {"end": 60.0, "unit": "second"},
    "bounds": [{"variable": "T", "min": 0}],
    "environment": {"name": "Lab bench", "description": "A room-temperature lab.", "kind": "indoor", "ground": "flat",
                    "ground_material": "bench top", "lighting": "studio", "sky_color": "#e9ebef",
                    "ground_color": "#8c8c8c", "fog": 0, "parameters": ["T_inf"]},
}


def spec(**over) -> SimSpec:
    return SimSpec.model_validate({**GOOD, **over})


def bad(expr: str) -> SimSpec:
    return spec(equations=[{"label": "x", "expression": expr}])


class FakeLLM:
    def __init__(self, *outs):
        self.outs, self.calls = list(outs), []

    async def structured(self, role, instructions, input, schema, tools=None):
        self.calls.append((role, input))
        return self.outs.pop(0)


def inp():
    return ModelInput(job_id="j", prompt="cool a body",
                      research=ResearchOutput(summary="s", phenomena=[], facts=[]), feedback=["fix X"])


def test_good_spec_valid():
    assert validate_spec(spec()) == []


def test_pi_and_second_order_ok():
    s = spec(
        parameters=[{"name": "w", "value": 2.0, "unit": "1/second"}],
        state=[{"name": "x", "unit": "meter", "initial_value": 1.0},
               {"name": "v", "unit": "meter/second", "initial_value": 0.0}],
        equations=[{"label": "a", "expression": "dx_dt = v"},
                   {"label": "b", "expression": "dv_dt = -w**2*x*sin(2*pi*t/t)"}],
        environment={**GOOD["environment"], "parameters": ["w"]},
        bounds=[],
    )
    assert validate_spec(s) == [], validate_spec(s)


@pytest.mark.parametrize("expr,frag", [
    ("dT_dt = -k*(T - Tx)", "undeclared"),
    ("dT_dt = -k*(T - g)", "dimensionally"),
    ("dT_dt = -k*T**2", "dimensionally"),
    ("dT_dt = exp(T)", "dimensionally"),
    ("dT_dt == 1", "exactly one"),
    ("d2T_dt2 = 1", "undeclared"),
])
def test_bad(expr, frag):
    errs = validate_spec(bad(expr))
    assert any(frag in e for e in errs), errs


def test_missing_state_equation_and_bounds():
    errs = validate_spec(spec(equations=[{"label": "a", "expression": "k = k"}],
                              bounds=[{"variable": "zz", "min": 0}]))
    assert any("not on the left" in e for e in errs) and any("zz" in e for e in errs)


def ac(**kw):
    base = {"variable": "T", "expression": "T_inf + 57*exp(-k*t)", "t_max": 30.0}
    return spec(analytic_checks=[{**base, **kw}])


def test_analytic_check_valid():
    assert validate_spec(ac()) == []
    assert validate_spec(ac(t_max=None)) == []


@pytest.mark.parametrize("kw,frag", [
    ({"variable": "Q"}, "declared state"),
    ({"expression": "T_inf + (T - T_inf)*exp(-k*t)"}, "['T']"),
    ({"expression": "T_inf + zeta*t"}, "zeta"),
    ({"expression": "dT_dt*t"}, "dT_dt"),
    ({"expression": "T_inf + ("}, "cannot parse"),
    ({"t_max": 600.0}, "t_max"),
    ({"t_max": 0.0}, "t_max"),
])
def test_analytic_check_invalid(kw, frag):
    errs = validate_spec(ac(**kw))
    assert any(frag in e for e in errs), errs


def test_prompt_mentions_analytic_and_refine():
    from omnific_modeler.prompts import MODEL_INSTRUCTIONS
    assert "analytic_checks" in MODEL_INSTRUCTIONS and "refine=2" in MODEL_INSTRUCTIONS


async def test_happy(monkeypatch):
    fake = FakeLLM(spec())
    monkeypatch.setattr(activities, "llm", fake)
    out = await activities.build_spec(inp())
    assert out.title == "Cooling body" and len(fake.calls) == 1
    assert "fix X" in fake.calls[0][1]


async def test_repair(monkeypatch):
    fake = FakeLLM(bad("dT_dt = -k*(T - Tx)"), spec())
    monkeypatch.setattr(activities, "llm", fake)
    out = await activities.build_spec(inp())
    assert out.equations[0].expression == "dT_dt = -k*(T - T_inf)"
    assert "undeclared" in fake.calls[1][1]


async def test_repeated_failure(monkeypatch):
    # the first answer and every repair round stay invalid: it gives up after MAX_REPAIRS rounds, not before
    answers = [bad("dT_dt = Tx") for _ in range(1 + activities.MAX_REPAIRS)]
    fake = FakeLLM(*answers)
    monkeypatch.setattr(activities, "llm", fake)
    with pytest.raises(ApplicationError) as ei:
        await activities.build_spec(inp())
    assert ei.value.non_retryable and f"after {activities.MAX_REPAIRS} self-repairs" in str(ei.value)


def test_strict_schema():
    assert to_strict_json_schema(SimSpec)


def test_default_assumption_in_prompt_and_input():
    from omnific_common.contracts import Clarification
    from omnific_modeler.prompts import MODEL_INSTRUCTIONS

    assert "Assumed <answer> for '<question>' (not specified by the user)" in MODEL_INSTRUCTIONS
    i = inp()
    i.clarifications = [
        Clarification(question="Altitude?", answer="100", unit="m", used_default=False),
        Clarification(question="Wind?", answer="none", used_default=True),
    ]
    text = activities._render_input(i)
    assert "BINDING user answer" in text and "DEFAULT used" in text and "Wind?" in text


def test_change_requests_rendered_with_previous_spec():
    i = inp()
    assert "asked for changes" not in activities._render_input(i)
    i.previous_spec = spec()
    i.change_requests = ["make the ramp 30 degrees"]
    text = activities._render_input(i)
    assert "keep everything they did not ask to change" in text
    assert "- make the ramp 30 degrees" in text and i.previous_spec.title in text


def test_unmatched_clarifications():
    from omnific_common.contracts import Clarification

    cl = [
        Clarification(question="Temp?", answer="20", unit="degC", used_default=False),
        Clarification(question="Len?", answer="2", unit="km", used_default=False),
        Clarification(question="Def?", answer="5", unit="m", used_default=True),
        Clarification(question="Text?", answer="sea level", used_default=False),
    ]
    s = spec(parameters=GOOD["parameters"] + [{"name": "T0", "value": 293.15, "unit": "kelvin"}])
    assert [c.question for c in activities.unmatched_clarifications(s, cl)] == ["Len?"]
    s2 = spec(parameters=GOOD["parameters"] + [{"name": "L", "value": 2000.0, "unit": "meter"},
                                               {"name": "T0", "value": 20.0, "unit": "degC"}])
    assert activities.unmatched_clarifications(s2, cl) == []


def test_entity_properties_must_name_declared_parameters():
    ok = spec(entities=[{"name": "body", "kind": "block", "bound_state": ["T"], "properties": ["k"]}])
    assert validate_spec(ok) == []
    errs = validate_spec(spec(entities=[{"name": "body", "kind": "block", "properties": ["mass"]}]))
    assert any("property 'mass' is not a declared parameter" in e for e in errs)


def test_environment_is_required_and_checked():
    errs = validate_spec(spec(environment=None))
    assert any("environment is missing" in e for e in errs)
    env = {**GOOD["environment"], "parameters": ["rho_air"], "sky_color": "blue"}
    errs = validate_spec(spec(environment=env))
    assert any("environment parameter 'rho_air'" in e for e in errs)
    assert any("sky_color must be a hex colour" in e for e in errs)
