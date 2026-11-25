"""Cross-agent: the Data agent reads positions and the scene scale the way omnific_sim.visual does."""

def test_position_tracks_follow_the_shared_rule_and_static_bodies_count_toward_scale():
    """The evaluator's geometry check and SceneBuild's true sizes rely on omnific_sim.visual's reading of positions:
    it must match the tracks this agent writes."""
    import numpy as np
    from omnific_data.activities import infer_tracks, length_scale
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

    units = {"s": "meter", "phi": "radian", "x": "meter", "y": "meter"}
    ball = Entity(name="ball", kind="sphere", bound_state=["phi", "x", "y", "s"],
                  geometry=EntityGeometry(shape="sphere", size=[0.2, 0.2, 0.2], position=[]))
    ramp = Entity(name="ramp", kind="ramp", geometry=EntityGeometry(shape="wedge", size=[8.0, 2.0, 1.0],
                                                                       position=[-4.0, 1.0, 0.0]))
    spec = SimSpec(title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
                   parameters=[], state=[StateVariable(name=k, unit=u, initial_value=0.0) for k, u in units.items()],
                   equations=[], time=TimeSpan(end=1.0), entities=[ball, ramp])
    arrays = {k: np.linspace(0.0, 1.0, 5) for k in units}
    tracks = {t["state"]: t["channel"] for t in infer_tracks(spec, arrays, units) if t["entity"] == "ball"}
    axes = dict(visual.length_axes(ball, units))
    assert {s: "position_" + "xyz"[a] for s, a in axes.items()} == {
        s: c for s, c in tracks.items() if c.startswith("position_")}
    assert length_scale(spec, arrays, units) == 10.0 / 8.0  # the ramp's far corner (8 m) sets the scale
