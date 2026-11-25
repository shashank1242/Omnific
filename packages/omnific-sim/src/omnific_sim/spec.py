"""SimSpec: the contract between the Modeler, Solver-coder, Evaluators and Exporters.

The Modeler agent emits a SimSpec via OpenAI structured outputs (`SimSpec.model_json_schema()`),
so keep fields JSON-schema friendly: no unions of complex types, no free-form dicts of objects.
"""

from enum import Enum
from typing import Literal

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
    properties: list[str] = Field(
        default_factory=list,
        description="Names of the parameters that describe this object (its mass, radius, drag coefficient, "
        "friction with the ground...), so the user can inspect and change them on the object",
    )
    # Articulation (playback format v2). With `arm` set, a bound angle state swings the entity about its pivot:
    # position = pivot + Rz(angle) · arm, where the angle is absolute (from the arm's rest direction).
    parent: str | None = Field(
        default=None, description="Entity whose position the pivot is relative to (e.g. bob 2 hangs from bob 1)"
    )
    pivot: list[float] | None = Field(
        default=None,
        description="[x, y, z] in metres of the rotation point: world position, or offset from `parent`. y is up",
    )
    arm: list[float] | None = Field(
        default=None,
        description="[x, y, z] in metres from pivot to this entity at angle 0, e.g. [0, -L, 0] for a "
        "pendulum of length L hanging straight down",
    )


class TimeSpan(BaseModel):
    start: float = 0.0
    end: float = Field(
        description="stop='settle': the longest the run may go, a safety cap (about 2-3x when you expect everything "
        "to have come to rest). stop='fixed': exactly when the run ends"
    )
    unit: str = "second"
    output_samples: int = Field(default=200, ge=2, le=100_000)
    stop: Literal["settle", "fixed"] = Field(
        default="settle",
        description="settle: the run stops once no state has changed for settle_window_s (then that buffer), so the "
        "duration follows the physics. fixed: run exactly to `end` - only when the user asked for a duration or "
        "the motion never comes to rest (undamped oscillation, orbit, steady flow)",
    )
    settle_window_s: float = Field(
        default=3.0, gt=0,
        description="Seconds of no change in every state before a settle run stops. Default 3; raise it (with "
        "stop_reason) for slow processes where 3 s of no visible change is not rest, e.g. heating over hours",
    )
    stop_reason: str = Field(
        default="", description="Why stop='fixed' or a settle window other than 3 s (shown to the user); else empty"
    )


class Environment(BaseModel):
    """Where the simulation happens: it shapes the physics (through the parameters it names) and the scene."""

    name: str = Field(description="Short name, e.g. 'Lunar surface', 'Lab bench', 'Open sea', 'Low Earth orbit'")
    description: str = Field(description="One sentence the user reads: what the place is and what it changes")
    kind: Literal["outdoor", "indoor", "space", "underwater", "planetary"]
    ground: Literal["flat", "none"] = Field(description="'flat' draws a ground plane at y=0; 'none' for space or open water")
    ground_material: str = Field(description="e.g. 'grass', 'regolith', 'concrete', 'ice', 'water'; '' when ground='none'")
    lighting: Literal["day", "dusk", "night", "studio", "space"]
    sky_color: str = Field(description="Hex colour of the sky or backdrop, e.g. '#9ec9ff' (day), '#000000' (space)")
    ground_color: str = Field(description="Hex colour of the ground, e.g. '#4f7d3a' (grass), '#8c8c8c' (regolith)")
    fog: float = Field(ge=0, le=1, description="0 = clear, 1 = thick (haze, underwater murk)")
    parameters: list[str] = Field(
        description="Names of the declared parameters this environment sets (gravity, air or water density, ground "
        "friction, ambient temperature...). The model must use every one of them"
    )


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
    environment: Environment | None = Field(
        default=None, description="The setting: always set it (lab bench, park, the Moon...); its parameters drive the physics"
    )
    invariants: list[Invariant] = Field(default_factory=list)
    bounds: list[Bound] = Field(default_factory=list)
    analytic_checks: list[AnalyticCheck] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)

    def parameter(self, name: str) -> Parameter:
        for p in self.parameters:
            if p.name == name:
                return p
        raise KeyError(name)
