import numpy as np
import pytest

from omnific_sim.spec import TimeSpan
from omnific_sim.stopping import last_change_time, settle_stop, window_in


def test_window_converts_to_the_run_unit():
    assert window_in("second", 3) == 3
    assert window_in("minute", 3) == pytest.approx(0.05)
    assert window_in("millisecond", 3) == pytest.approx(3000)


def test_a_ball_rolling_to_rest_stops_three_seconds_after_it_rests():
    t = np.linspace(0, 20, 2001)
    x = np.where(t < 5, 10 * t - t**2, 25.0)  # decelerates, rests at t=5
    v = np.where(t < 5, 10 - 2 * t, 0.0)
    assert last_change_time(t, {"x": x, "v": v}) == pytest.approx(5, abs=0.05)
    assert settle_stop(t, {"x": x, "v": v}, 3.0) == pytest.approx(8, abs=0.05)


def test_a_motion_that_never_rests_runs_to_the_end():
    t = np.linspace(0, 10, 1001)
    assert settle_stop(t, {"theta": np.sin(3 * t)}, 3.0) is None


def test_damped_oscillation_stops_once_within_tolerance():
    t = np.linspace(0, 60, 6001)
    theta = np.exp(-0.5 * t) * np.cos(4 * t)
    stop = settle_stop(t, {"theta": theta}, 3.0)
    assert stop is not None and 12 < stop < 20  # e^-0.5t < 1e-3 near t=13.8, plus 3 s


def test_fields_and_constant_states():
    t = np.linspace(0, 10, 101)
    field = np.outer(np.minimum(t, 2.0), np.ones(4))  # heats until t=2, then flat
    stop = settle_stop(t, {"T": field, "k": np.full_like(t, 7.0)}, 3.0)
    assert stop == pytest.approx(5.0, abs=0.15)
    assert settle_stop(t, {"k": np.full_like(t, 7.0)}, 3.0) == pytest.approx(3.0)  # nothing moves at all


def test_timespan_defaults_and_exceptions():
    ts = TimeSpan(end=30)
    assert ts.stop == "settle" and ts.settle_window_s == 3.0 and ts.stop_reason == ""
    slow = TimeSpan(end=5, unit="hour", settle_window_s=600, stop_reason="heating is slow")
    assert slow.settle_window_s == 600
    with pytest.raises(ValueError):
        TimeSpan(end=1, settle_window_s=0)


def test_a_quiet_start_does_not_cut_off_delayed_motion():
    # still until 10 s, moves during 10-11 s, then rests: the stop follows the LAST change, not the first quiet spell
    t = np.linspace(0, 30, 3001)
    x = np.clip(t - 10, 0, 1)
    assert settle_stop(t, {"x": x}, 3.0) == pytest.approx(14, abs=0.05)


def test_each_field_component_uses_its_own_range():
    t = np.linspace(0, 10, 1001)
    field = np.stack([np.full_like(t, 1e6), t], axis=1)  # a huge constant column beside one that keeps changing
    assert settle_stop(t, {"f": field}, 3.0) is None


def test_a_non_finite_ending_is_never_rest():
    t = np.linspace(0, 10, 11)
    x = np.append(np.arange(10.0), np.nan)
    assert settle_stop(t, {"x": x}, 3.0) is None
