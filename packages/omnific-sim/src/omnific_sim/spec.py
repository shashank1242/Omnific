"""SimSpec: the contract between the Modeler, Solver-coder, Evaluators and Exporters.

The Modeler agent emits a SimSpec via OpenAI structured outputs (`SimSpec.model_json_schema()`),
so keep fields JSON-schema friendly: no unions of complex types, no free-form dicts of objects.
"""

from enum import Enum

from pydantic import BaseModel, Field, field_validator

from .units import ureg


def _check_unit(v: str) -> str:
    try:
        ureg.parse_units(v)
    except Exception as e:  # pint raises several error types; surface them as pydantic errors
        raise ValueError(f"invalid unit {v!r}: {e}") from e
    return v


class Fidelity(str, Enum):
    ANALYTIC = "analytic"
    VALIDATED_NUMERICAL = "validated_numerical"
    HEURISTIC = "heuristic"


class Domain(str, Enum):
    MECHANICS = "mechanics"
    THERMAL = "thermal"
    CHEMISTRY = "chemistry"
    FLUIDS = "fluids"
    ELECTROMAGNETISM = "electromagnetism"
    MATHEMATICS = "mathematics"
    OTHER = "other"


class Citation(BaseModel):
    title: str
    url: str
    note: str = ""


class Parameter(BaseModel):
    name: str = Field(description="Identifier used in equations and solver code, e.g. 'T_surface'")
    value: float
    unit: str = Field(description="pint-parsable unit string, e.g. 'kelvin', 'm/s**2'. Use '' for dimensionless")
    description: str = ""
    source: Citation | None = None

    @field_validator("unit")
    @classmethod
    def unit_must_parse(cls, v: str) -> str:
        return _check_unit(v)

    def quantity(self):
        return self.value * ureg.parse_units(self.unit)


class StateVariable(BaseModel):
    name: str
    unit: str
    initial_value: float
    description: str = ""

    @field_validator("unit")
    @classmethod
    def unit_must_parse(cls, v: str) -> str:
        return _check_unit(v)


class Equation(BaseModel):
    """A governing equation. `expression` is SymPy-parsable, written as 'lhs = rhs'."""

    label: str
    expression: str
    description: str = ""


class Entity(BaseModel):
    """Something visible in the scene; the Mesh agent maps `kind` to an asset."""

    name: str
    kind: str = Field(description="Asset hint, e.g. 'sphere', 'pizza', 'planet_surface'")
    bound_state: list[str] = Field(
        default_factory=list, description="State variable names that drive this entity's visuals"
    )


class TimeSpan(BaseModel):
    start: float = 0.0
    end: float
    unit: str = "second"
    output_samples: int = Field(default=200, ge=2, le=100_000)


class Invariant(BaseModel):
    """A quantity evaluators should check for conservation (e.g. total energy)."""

    name: str
    expression: str = Field(description="SymPy expression over state variables and parameters")
    rel_tolerance: float = 1e-3


class Bound(BaseModel):
    """A sanity bound on a state variable, in that variable's unit."""

    variable: str
    min: float | None = None
    max: float | None = None


class AnalyticCheck(BaseModel):
    """A closed-form reference the Evaluator compares the numerical result against."""

    variable: str = Field(description="State variable name")
    expression: str = Field(description="SymPy expression in t and parameter names, in the variable's unit")
    t_max: float | None = Field(
        default=None, description="Only compare for t <= t_max (e.g. an early-time asymptotic solution)"
    )
    rel_tolerance: float = 1e-2
    description: str = ""


class SimSpec(BaseModel):
    title: str
    prompt: str
    domains: list[Domain]
    fidelity: Fidelity
    assumptions: list[str]
    parameters: list[Parameter]
    state: list[StateVariable]
    equations: list[Equation]
    entities: list[Entity] = Field(default_factory=list)
    time: TimeSpan
    invariants: list[Invariant] = Field(default_factory=list)
    bounds: list[Bound] = Field(default_factory=list)
    analytic_checks: list[AnalyticCheck] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)

    def parameter(self, name: str) -> Parameter:
        for p in self.parameters:
            if p.name == name:
                return p
        raise KeyError(name)
