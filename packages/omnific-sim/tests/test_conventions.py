import pytest

from omnific_sim.conventions import check_equation_dimensions

UNITS = {"vx": "m/s", "dvx_dt": "(m/s)/(second)", "g": "m/s**2", "m": "kg", "t": "second"}


@pytest.mark.parametrize("expr", ["dvx_dt = 0", "dvx_dt = 0.0", "0 = dvx_dt", "dvx_dt = -g"])
def test_consistent(expr):
    assert check_equation_dimensions(expr, UNITS) is True


def test_inconsistent():
    assert check_equation_dimensions("dvx_dt = -g*m", UNITS) is False


def test_exempt():
    assert check_equation_dimensions("dvx_dt = -Abs(g)", UNITS) is None
