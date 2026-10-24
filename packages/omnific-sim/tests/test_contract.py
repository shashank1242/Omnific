import numpy as np
import pytest
from pydantic import ValidationError

from omnific_sim import SimResult, SimSpec


def _spec(unit="m/s**2"):
    return {
        "title": "Drop", "prompt": "drop a ball", "domains": ["mechanics"],
        "fidelity": "analytic", "assumptions": ["no drag"],
        "parameters": [{"name": "g", "value": 9.81, "unit": unit}],
        "state": [{"name": "y", "unit": "m", "initial_value": 10.0}],
        "equations": [{"label": "fall", "expression": "a = g"}],
        "time": {"end": 1.0},
    }


def test_spec_roundtrip():
    s = SimSpec.model_validate(_spec())
    assert SimSpec.model_validate_json(s.model_dump_json()) == s
    assert s.parameter("g").quantity().to("m/s**2").magnitude == pytest.approx(9.81)
    assert "properties" in SimSpec.model_json_schema()


def test_spec_invalid_unit():
    with pytest.raises(ValidationError):
        SimSpec.model_validate(_spec("not_a_unit"))


def test_result_npz_roundtrip(tmp_path):
    r = SimResult(t=np.linspace(0, 1, 5), state={"x": np.arange(5.0), "f": np.ones((5, 3))},
                  units={"x": "meter", "f": "kelvin"})
    p = str(tmp_path / "r.npz")
    r.save_npz(p)
    r2 = SimResult.load_npz(p)
    np.testing.assert_array_equal(r2.t, r.t)
    np.testing.assert_array_equal(r2.state["f"], r.state["f"])
    assert r2.units == r.units
