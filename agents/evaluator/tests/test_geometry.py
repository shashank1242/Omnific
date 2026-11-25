"""The picture must match the physics: nothing sinks into the ground or rolls through a ramp."""

import math

import numpy as np
from omnific_evaluator.checks import check_geometry
from omnific_sim.result import SimResult
from omnific_sim.spec import (
    Domain,
    Entity,
    EntityGeometry,
    Environment,
    Fidelity,
    SimSpec,
    StateVariable,
    TimeSpan,
)

R = 0.1
THETA = math.radians(30)
L = 2.0
RUN, RISE = L * math.cos(THETA), L * math.sin(THETA)


def spec(entities, ground="flat"):
    env = Environment(name="Park", description="grass", kind="outdoor", ground=ground, ground_material="grass",
                      lighting="day", sky_color="#88aaff", ground_color="#448844", fog=0.0, parameters=[])
    return SimSpec(title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
                   parameters=[], state=[StateVariable(name="x", unit="meter", initial_value=0.0),
                                         StateVariable(name="y", unit="meter", initial_value=0.0)],
                   equations=[], time=TimeSpan(end=1.0), entities=entities, environment=env)


def ball():
    return Entity(name="ball", kind="sphere", bound_state=["x", "y"],
                  geometry=EntityGeometry(shape="sphere", size=[2 * R] * 3, position=[]))


def ramp(centre_y=RISE / 2):
    return Entity(name="ramp", kind="ramp",
                  geometry=EntityGeometry(shape="wedge", size=[RUN, RISE, 1.0], position=[-RUN / 2, centre_y, 0.0]))


def rolling(offset: float) -> SimResult:
    """A ball rolling down the ramp (foot at x = 0) then along the ground; `offset` = centre height above the
    surface, R when modelled right, 0 when the contact point was bound instead of the centre."""
    s = np.linspace(0.0, L + 1.0, 60)  # distance travelled
    on_ramp = s <= L
    x = np.where(on_ramp, -RUN + s * math.cos(THETA), s - L)
    y_surf = np.where(on_ramp, RISE - s * math.sin(THETA), 0.0)
    nx, ny = np.where(on_ramp, math.sin(THETA), 0.0), np.where(on_ramp, math.cos(THETA), 1.0)
    return SimResult(t=s, state={"x": x + offset * nx, "y": y_surf + offset * ny}, units={"x": "m", "y": "m"})


def by_name(checks):
    return {c.name: c for c in checks}


def test_a_well_placed_ball_and_ramp_pass():
    out = by_name(check_geometry(spec([ball(), ramp()]), rolling(R)))
    assert out["geometry[ball]"].passed and out["geometry[ramp]"].passed


def test_a_ball_tracked_by_its_contact_point_sinks_and_cuts_the_ramp():
    c = by_name(check_geometry(spec([ball(), ramp()]), rolling(0.0)))["geometry[ball]"]
    assert not c.passed and c.blame == "modeler"
    assert "sinks" in c.detail and "into the ground" in c.detail and "passes" in c.detail and "ramp" in c.detail


def test_a_ramp_half_in_the_ground_fails():
    c = by_name(check_geometry(spec([ball(), ramp(centre_y=0.0)]), rolling(R)))["geometry[ramp]"]
    assert not c.passed and "below the ground" in c.detail


def test_no_ground_no_ground_check_and_no_geometry_no_check():
    out = by_name(check_geometry(spec([ball()], ground="none"), rolling(0.0)))
    assert out["geometry[ball]"].passed  # in space nothing to sink into
    plain = Entity(name="ball", kind="sphere", bound_state=["x", "y"])
    assert check_geometry(spec([plain]), rolling(0.0)) == []


def test_an_explosion_that_never_happens_fails():
    from omnific_evaluator.checks import check_effects
    from omnific_sim.spec import VisualEffect

    fx = VisualEffect(name="fireball", kind="explosion", anchor="ball", radius_state="x", intensity_state="y")
    s = spec([ball()]).model_copy(update={"effects": [fx]})
    t = np.linspace(0, 1, 11)
    lit = SimResult(t=t, state={"x": t * 3, "y": np.sin(np.pi * t)}, units={"x": "m", "y": ""})
    dud = SimResult(t=t, state={"x": t * 3, "y": np.zeros(11)}, units={"x": "m", "y": ""})
    assert check_effects(s, lit)[0].passed
    c = check_effects(s, dud)[0]
    assert not c.passed and c.blame == "modeler" and "never appears" in c.detail


def test_an_effect_whose_size_and_light_never_overlap_fails():
    from omnific_evaluator.checks import check_effects
    from omnific_sim.spec import VisualEffect

    fx = VisualEffect(name="fireball", kind="explosion", anchor="ball", radius_state="x", intensity_state="y")
    s = spec([ball()]).model_copy(update={"effects": [fx]})
    t = np.array([0.0, 1.0, 2.0])
    apart = SimResult(t=t, state={"x": np.array([0.0, 0.0, 1.0]), "y": np.array([1.0, 0.0, 0.0])},
                      units={"x": "m", "y": ""})
    c = check_effects(s, apart)[0]
    assert not c.passed and "same time" in c.detail
