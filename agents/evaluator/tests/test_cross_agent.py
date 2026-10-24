"""Specs the Modeler accepts must pass the Evaluator's dimension checks (shared equation convention)."""

from omnific_evaluator.checks import check_dimensions
from omnific_modeler.validation import validate_spec
from test_evaluate import make_spec


def test_modeler_valid_spec_passes_evaluator_dimensions():
    spec = make_spec(assumptions=["no air resistance"])
    assert validate_spec(spec) == []
    assert all(c.passed for c in check_dimensions(spec))


def test_dimension_error_caught_by_both():
    from omnific_sim.spec import Equation

    spec = make_spec(assumptions=["x"], equations=[Equation(label="bad", expression="dv_dt = -g*m"),
                                                   Equation(label="kin", expression="dy_dt = v")])
    assert any("dimensionally inconsistent" in e for e in validate_spec(spec))
    assert not all(c.passed for c in check_dimensions(spec))
