"""SimSpec: the contract between the Modeler, Solver-coder, Evaluators and Exporters.

The Modeler agent emits a SimSpec via OpenAI structured outputs (`SimSpec.model_json_schema()`),
so keep fields JSON-schema friendly: no unions of complex types, no free-form dicts of objects.
"""

import math
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


GeometryShape = Literal["sphere", "box", "cylinder", "wedge", "disk", "plane"]


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class EntityGeometry(BaseModel):
    """The object's true shape, size and (for things that don't move) place, in metres: the scene draws it exactly
    so, on the same scale as the motion, so a ball sits on the ground and rolls on its ramp instead of sinking."""

    shape: GeometryShape = Field(
        description="sphere | box | cylinder (axis vertical) | wedge (a ramp: a right-angled prism whose sloped face "
        "goes DOWN toward +x, vertical back face at -x) | disk (flat cylinder) | plane (thin floor or table top)"
    )
    size: list[float] = Field(
        description="[x, y, z] extent in metres, y up. sphere: [d, d, d] (d = diameter); box: [width, height, "
        "depth]; cylinder/disk: [diameter, height, diameter]; plane: [width, 0, depth]; wedge: [run along x, rise "
        "along y, depth along z] (slope angle = atan(rise / run))"
    )
    position: list[float] = Field(
        description="[x, y, z] in metres of the centre of its bounding box when it does NOT move (a ramp, table, "
        "wall or step, in the same frame as the state variables, ground y = 0, so a ramp standing on the ground has "
        "centre y = rise / 2); [] for bodies placed by their position states or by pivot/arm"
    )
    yaw_deg: float = Field(
        default=0.0, description="Turn about the vertical axis in degrees; 180 makes a wedge slope down toward -x"
    )

    def valid_size(self) -> list[float] | None:
        """The size if it is three finite numbers, none negative and at least two positive; else None."""
        s = self.size
        if len(s) != 3 or not all(_finite(v) for v in s) or any(v < 0 for v in s) or sum(v > 0 for v in s) < 2:
            return None
        return [float(v) for v in s]

    def valid_position(self) -> list[float] | None:
        p = self.position
        return [float(v) for v in p] if len(p) == 3 and all(_finite(v) for v in p) else None


EffectKind = Literal["explosion", "flash", "shockwave", "fire", "smoke", "glow", "accretion_disk", "jet", "splash"]
OutputMeasure = Literal["max", "min", "final", "initial", "mean", "time_of_max", "time_of_min"]


class VisualEffect(BaseModel):
    """Something that happens rather than an object: a fireball, flash, shock front, smoke, a glow, an accretion disk.
    It is drawn from solved states (its radius, brightness and temperature over time), so the solver stays the truth:
    the effect only shows what the equations computed."""

    name: str = Field(description="Unique, e.g. 'fireball', 'shock front', 'detonation flash'")
    kind: EffectKind = Field(
        description="explosion (fireball with debris) | flash (a burst of light) | shockwave (expanding shell) | "
        "fire (flames) | smoke (billowing cloud) | glow (soft light halo) | accretion_disk (hot spinning ring, its "
        "`size` = outer radius) | jet (twin beams along y) | splash (droplets and a crown thrown out of a liquid by an "
        "impact; its radius = how far they reach)"
    )
    anchor: str = Field(
        default="", description="Entity the effect is centred on and follows; '' to place it at `position`"
    )
    position: list[float] = Field(
        default_factory=list, description="[x, y, z] in metres when anchor is ''; else []"
    )
    radius_state: str = Field(
        default="", description="State (a length) that IS the effect's radius over time (fireball, shock front, cloud); "
        "'' to use the fixed `size`"
    )
    size: float = Field(default=0.0, description="Fixed radius in metres when radius_state is ''; else 0")
    intensity_state: str = Field(
        default="", description="State whose value is the effect's brightness over time; the effect is drawn at value / "
        "its peak, invisible while it is 0 (before ignition, after it fades). '' = always fully on"
    )
    temperature_state: str = Field(
        default="", description="State in kelvin that sets the colour (blackbody: deep red, orange, white, blue-white); "
        "'' to use `color`"
    )
    color: str = Field(default="", description="Hex colour when temperature_state is '', e.g. '#ff8a2a'")
    description: str = Field(default="", description="One sentence the user reads: what it shows")


class Phase(BaseModel):
    """A named stage of the run (falling, detonation, fireball, fade): the timeline shows them and the .blend gets
    a marker at each start."""

    name: str = Field(description="Two or three words, e.g. 'Free fall', 'Detonation', 'Fireball grows'")
    start: float = Field(description="When it starts, in the time unit; it lasts until the next phase starts")
    description: str = Field(default="", description="One sentence: what happens in this phase")


class KeyOutput(BaseModel):
    """A headline result the user cares about for this kind of simulation, computed from a state's solved values."""

    label: str = Field(description="What it is, in plain words, e.g. 'Peak fireball radius', 'Time to the horizon'")
    state: str = Field(description="The state it is computed from")
    measure: OutputMeasure = Field(
        description="max | min | final | initial | mean of the state, or time_of_max / time_of_min = WHEN it peaks"
    )
    why: str = Field(default="", description="One sentence: why this number matters for this simulation")


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
    look: str = Field(
        default="",
        description="How it should look, in a few words, when the user said so ('a red vintage race car', 'a "
        "bowling ball'); empty otherwise. Visual only: never a physical value (those are parameters)",
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
    geometry: EntityGeometry | None = Field(
        default=None,
        description="Its true shape, size and (if it doesn't move) position in metres. Set it for every object whose "
        "size matters to the picture (balls, ramps, blocks, tables, walls); null only for abstract things",
    )
    faces: str = Field(
        default="", description="Name of another entity this one points its front at: a gun's muzzle at its target, "
        "a camera or a lamp at its subject, a person at whoever they face. Empty when its heading does not matter or "
        "follows from its motion"
    )
    appears_at: float | None = Field(
        default=None, description="Time (in the time unit) it appears, e.g. debris after a blast; null = from the start"
    )
    disappears_at: float | None = Field(
        default=None, description="Time it disappears, e.g. a bomb at detonation, a probe crossing the horizon; null = "
        "stays to the end"
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
    effects: list[VisualEffect] = Field(
        default_factory=list, description="Explosions, flashes, shock fronts, smoke, glows, disks: drawn from states"
    )
    phases: list[Phase] = Field(default_factory=list, description="Named stages of the run, in time order")
    outputs: list[KeyOutput] = Field(
        default_factory=list, description="2-6 headline results that matter most for this kind of simulation"
    )
    departures: list[str] = Field(
        default_factory=list,
        description="Everything the user asked for that this model does NOT simulate as asked (left out, replaced or "
        "simplified away), one plain sentence each saying what and why. Shown to the user above the simulation. [] "
        "when everything asked for is simulated",
    )

    def parameter(self, name: str) -> Parameter:
        for p in self.parameters:
            if p.name == name:
                return p
        raise KeyError(name)
