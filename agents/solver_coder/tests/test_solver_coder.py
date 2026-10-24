import pytest
from omnific_common.contracts import SolverCode, SolverCodeInput
from omnific_sim.spec import SimSpec
from omnific_solver_coder import activities
from omnific_solver_coder.prompts import primitive_catalog, solver_instructions
from omnific_solver_coder.validation import validate_code
from openai.lib._pydantic import to_strict_json_schema
from temporalio.exceptions import ApplicationError

GOOD_CODE = '''
import numpy as np
from omnific_sim.primitives import projectile
from omnific_sim.result import SimResult
from omnific_sim.spec import SimSpec


def simulate(spec: SimSpec, refine: int = 1) -> SimResult:
    r = projectile(
        v0=spec.parameter("v0").value, angle_deg=spec.parameter("angle").value,
        g=spec.parameter("g").value, n=spec.time.output_samples,
    )
    return SimResult(t=r.t, state={s.name: r.state[s.name] for s in spec.state},
                     units={s.name: s.unit for s in spec.state}, meta=r.meta)
'''

SPEC = SimSpec.model_validate({
    "title": "p", "prompt": "p", "domains": ["mechanics"], "fidelity": "validated_numerical",
    "assumptions": ["no drag"],
    "parameters": [{"name": "v0", "value": 10.0, "unit": "m/s"},
                   {"name": "angle", "value": 45.0, "unit": "degree"},
                   {"name": "g", "value": 9.8, "unit": "m/s**2"}],
    "state": [{"name": "x", "unit": "meter", "initial_value": 0.0}],
    "equations": [{"label": "a", "expression": "dx_dt = v0"}],
    "time": {"end": 2.0},
})


class FakeLLM:
    def __init__(self, *outs):
        self.outs, self.calls = list(outs), []

    async def structured(self, role, instructions, input, schema, tools=None):
        self.calls.append((role, instructions, input))
        return self.outs.pop(0)


def inp(**kw):
    return SolverCodeInput(job_id="j", spec=SPEC, **kw)


def test_accepts_realistic():
    assert validate_code(GOOD_CODE) == []


@pytest.mark.parametrize("code", [
    "import os\ndef simulate(spec):\n    return 1",
    "from os import path\ndef simulate(spec):\n    return 1",
    "def simulate(spec):\n    open('x')\n",
    "def simulate(spec):\n    return __import__('os')\n",
    "def simulate(spec):\n    return eval('1')\n",
    "def simulate(spec):\n    return ().__class__.__bases__\n",
    "import numpy as np\ndef other(spec):\n    return 1\n",
    "def simulate(spec) return 1",
    "def simulate(spec):\n    return 1",
    "def simulate(spec, refine=2):\n    return 1",
    "def simulate(spec, refine):\n    return 1",
    "def simulate(spec, *, refine=1.0):\n    return 1",
    "from . import x\ndef simulate(spec):\n    return 1",
])
def test_rejects(code):
    assert validate_code(code)


def test_accepts_kwonly_refine():
    assert validate_code("def simulate(spec, *, refine=1):\n    return 1") == []


def test_prompt_explains_refine():
    assert "refine: int = 1" in solver_instructions() and "max_step" in solver_instructions()


def test_catalog_generated():
    cat = primitive_catalog()
    assert "projectile(v0: float" in cat and "dimension_check(" in cat
    assert "projectile(v0" in solver_instructions()


async def test_happy_with_feedback(monkeypatch):
    fake = FakeLLM(SolverCode(code=GOOD_CODE))
    monkeypatch.setattr(activities, "llm", fake)
    out = await activities.write_solver(inp(feedback=["fix drift"], previous_code="# old"))
    assert out.code == GOOD_CODE
    assert "fix drift" in fake.calls[0][2] and "# old" in fake.calls[0][2]


async def test_repair(monkeypatch):
    fake = FakeLLM(SolverCode(code="import os\ndef simulate(spec): pass"), SolverCode(code=GOOD_CODE))
    monkeypatch.setattr(activities, "llm", fake)
    out = await activities.write_solver(inp())
    assert out.code == GOOD_CODE and "not allowed" in fake.calls[1][2]


async def test_repeated_failure(monkeypatch):
    bad = SolverCode(code="import os\ndef simulate(spec): pass")
    monkeypatch.setattr(activities, "llm", FakeLLM(bad, bad))
    with pytest.raises(ApplicationError) as ei:
        await activities.write_solver(inp())
    assert ei.value.non_retryable


def test_strict_schema():
    assert to_strict_json_schema(SolverCode)
