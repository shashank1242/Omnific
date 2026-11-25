"""omnific_common.edits: applying an edit plan and saying what changed."""

import pytest
from omnific_common.contracts import EditPlan, EntityLook, ValueChange, VariantChanges
from omnific_common.edits import apply_changes, apply_plan, spec_diff
from omnific_sim.spec import (
    Domain,
    Entity,
    Environment,
    Equation,
    Fidelity,
    Parameter,
    SimSpec,
    StateVariable,
    TimeSpan,
)

ENV = Environment(name="Open field", description="A grassy field.", kind="outdoor", ground="flat",
                  ground_material="grass", lighting="day", sky_color="#88AAFF", ground_color="#448844", fog=0.0,
                  parameters=["g"])
SPEC = SimSpec(
    title="Drop", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.ANALYTIC, assumptions=[],
    parameters=[Parameter(name="g", value=9.81, unit="m/s**2")],
    state=[StateVariable(name="y", unit="m", initial_value=10.0)],
    equations=[Equation(label="fall", expression="dy_dt = -g*t")],
    time=TimeSpan(end=5.0), entities=[Entity(name="ball", kind="sphere")], environment=ENV,
)


def plan(kind, **kw) -> EditPlan:
    return EditPlan(kind=kind, summary="s", changes=kw.get("changes", VariantChanges()),
                    stop_fixed=kw.get("stop_fixed", False), looks=kw.get("looks", []), questions=[])


def test_parameters_plan_with_a_look_and_a_fixed_duration():
    p = plan("parameters", changes=VariantChanges(parameters=[ValueChange(name="g", value=1.62)], end=20.0),
             stop_fixed=True, looks=[EntityLook(entity="ball", look="a bowling ball")])
    out = apply_plan(SPEC, p)
    assert out.parameters[0].value == 1.62 and out.time.end == 20 and out.time.stop == "fixed"
    assert out.entities[0].look == "a bowling ball"
    assert spec_diff(SPEC, out) == ["g: 9.81 m/s**2 → 1.62 m/s**2", "ball looks like: a bowling ball",
                                    "runs up to 20 second (was 5)", "runs for exactly that long"]


def test_visual_plan_changes_only_looks():
    out = apply_plan(SPEC, plan("visual", changes=VariantChanges(parameters=[ValueChange(name="g", value=1.0)]),
                                looks=[EntityLook(entity="ball", look="red")]))
    assert out.parameters == SPEC.parameters and spec_diff(SPEC, out) == ["ball looks like: red"]


def test_plans_naming_unknown_things_raise():
    with pytest.raises(ValueError):
        apply_plan(SPEC, plan("visual", looks=[EntityLook(entity="car", look="red")]))
    with pytest.raises(ValueError):
        apply_changes(SPEC, VariantChanges(parameters=[ValueChange(name="mu", value=1)]))
    with pytest.raises(ValueError):
        apply_plan(SPEC, plan("structural"))


def test_diff_reports_joints_and_visible_setting_changes_only():
    moved = SPEC.model_copy(update={"entities": [Entity(name="ball", kind="sphere", pivot=[1, 0, 0])]})
    assert spec_diff(SPEC, moved) == ["ball is jointed differently (pivot / arm / parent)"]
    reworded = SPEC.model_copy(update={"environment": ENV.model_copy(
        update={"description": "A wide grassy field.", "parameters": ["g", "rho"]})})
    assert spec_diff(SPEC, reworded) == []
    dusk = SPEC.model_copy(update={"environment": ENV.model_copy(update={"lighting": "dusk", "fog": 0.2})})
    assert spec_diff(SPEC, dusk) == ["setting Open field: lighting, fog changed"]
    moon = SPEC.model_copy(update={"environment": ENV.model_copy(update={"name": "Moon"})})
    assert spec_diff(SPEC, moon) == ["setting: Open field → Moon"]
    assert spec_diff(SPEC, SPEC) == []


def test_diff_reports_a_unit_change_and_a_new_time_origin():
    cm = SPEC.model_copy(update={"state": [StateVariable(name="y", unit="cm", initial_value=10.0)]})
    assert spec_diff(SPEC, cm) == ["y starts at 10 cm (was 10 m)"]
    later = SPEC.model_copy(update={"time": TimeSpan(start=1.0, end=5.0)})
    assert spec_diff(SPEC, later) == ["starts at t = 1 second (was 0)"]
