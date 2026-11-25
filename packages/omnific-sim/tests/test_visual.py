"""Where things are in the picture: position states, true sizes, and distances to static solids."""

import math

import numpy as np
import pytest

from omnific_sim import visual
from omnific_sim.spec import (
    Domain,
    Entity,
    EntityGeometry,
    Fidelity,
    SimSpec,
    StateVariable,
    TimeSpan,
)

THETA = math.radians(30)
L = 2.0
RUN, RISE = L * math.cos(THETA), L * math.sin(THETA)
RAMP = Entity(name="ramp", kind="ramp", geometry=EntityGeometry(
    shape="wedge", size=[RUN, RISE, 1.0], position=[-RUN / 2, RISE / 2, 0.0]))  # foot at x = 0
BALL = Entity(name="ball", kind="sphere", bound_state=["x", "y", "phi"],
              geometry=EntityGeometry(shape="sphere", size=[0.2, 0.2, 0.2], position=[]))
UNITS = {"x": "meter", "y": "meter", "phi": "radian"}


def test_length_axes_follow_bound_order_and_skip_non_lengths():
    assert visual.length_axes(BALL, UNITS) == [("x", 0), ("y", 1)]
    assert visual.length_axes(RAMP, UNITS) == []


def test_static_place_and_extent_peaks():
    assert visual.static_place(RAMP, UNITS) == pytest.approx([-RUN / 2, RISE / 2, 0.0])
    assert visual.static_place(BALL, UNITS) is None  # it moves
    spec = SimSpec(title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
                   parameters=[], state=[StateVariable(name="x", unit="meter", initial_value=0.0)], equations=[],
                   time=TimeSpan(end=1.0), entities=[RAMP, BALL])
    assert max(visual.extent_peaks(spec, UNITS)) == pytest.approx(RUN)


def test_centre_track_converts_units():
    track = visual.centre_track(BALL, {"x": np.array([0.0, 100.0]), "y": np.array([10.0, 20.0])},
                                {"x": "centimeter", "y": "centimeter"}, 2)
    assert np.allclose(track, [[0.0, 0.1, 0.0], [1.0, 0.2, 0.0]])


def test_wedge_distance_matches_the_slope():
    g, size = RAMP.geometry, RAMP.geometry.valid_size()
    place = RAMP.geometry.valid_position()
    # a point 0.1 m above the middle of the slope (along its normal) is 0.1 m away; 0.1 m below is inside
    mid = np.array([-RUN / 2, RISE / 2, 0.0])
    n = np.array([math.sin(THETA), math.cos(THETA), 0.0])
    d = visual.solid_distance(np.array([mid + 0.1 * n, mid - 0.1 * n]), g, size, place)
    assert d[0] == pytest.approx(0.1, abs=1e-9) and d[1] == pytest.approx(-0.1, abs=1e-9)
    # beside the ramp (past its depth) and far in front of it: outside
    assert visual.solid_distance(np.array([[-RUN / 2, RISE / 2, 1.0]]), g, size, place)[0] == pytest.approx(0.5, abs=1e-9)
    assert visual.solid_distance(np.array([[1.0, 0.0, 0.0]]), g, size, place)[0] == pytest.approx(1.0, abs=1e-9)


def test_turned_box_distance():
    g = EntityGeometry(shape="box", size=[2.0, 1.0, 0.5], position=[0, 0.5, 0], yaw_deg=90)
    # turned 90 degrees about y: its 2 m side now runs along z
    d = visual.solid_distance(np.array([[0.0, 0.5, 1.5], [1.5, 0.5, 0.0]]), g, g.valid_size(), [0.0, 0.5, 0.0])
    assert d[0] == pytest.approx(0.5, abs=1e-9) and d[1] == pytest.approx(1.25, abs=1e-9)


def test_unusable_geometry_is_ignored():
    bad = Entity(name="b", kind="ball", geometry=EntityGeometry(shape="sphere", size=[0.2], position=[]))
    assert visual.geometry(bad) is None
    assert visual.geometry(Entity(name="c", kind="cube")) is None


def test_a_fixed_place_wins_over_bound_lengths():
    hole = Entity(name="hole", kind="black hole", bound_state=["x"],
                  geometry=EntityGeometry(shape="sphere", size=[2.0] * 3, position=[5.0, 0.0, 0.0]))
    assert visual.static_place(hole, UNITS) == [5.0, 0.0, 0.0]
