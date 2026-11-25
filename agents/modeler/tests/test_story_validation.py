"""The modeler's story: objects you can see, effects from real states, phases in order, outputs from states."""

from omnific_modeler.validation import _validate_story
from omnific_sim.spec import (
    Domain,
    Entity,
    Fidelity,
    KeyOutput,
    Phase,
    SimSpec,
    StateVariable,
    TimeSpan,
    VisualEffect,
)


def spec(**over):
    base = SimSpec(title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
                   parameters=[], equations=[], time=TimeSpan(end=5.0),
                   state=[StateVariable(name="R", unit="meter", initial_value=0.0),
                          StateVariable(name="T", unit="kelvin", initial_value=300.0),
                          StateVariable(name="g", unit="", initial_value=0.0)],
                   entities=[Entity(name="bomb", kind="atomic bomb", disappears_at=1.0)],
                   effects=[VisualEffect(name="fireball", kind="explosion", anchor="bomb", radius_state="R",
                                         intensity_state="g", temperature_state="T")],
                   phases=[Phase(name="Fall", start=0.0), Phase(name="Blast", start=1.0)],
                   outputs=[KeyOutput(label="Peak radius", state="R", measure="max")])
    return base.model_copy(update=over)


def errors(s):
    return _validate_story(s, [x.name for x in s.state], [e.name for e in s.entities])


def test_a_good_story_passes():
    assert errors(spec()) == []


def test_charts_are_not_objects():
    e = errors(spec(entities=[Entity(name="Horizon timing diagram", kind="causal_diagram")], effects=[]))
    assert any("not an object" in x for x in e)


def test_effects_need_real_states_of_the_right_kind():
    bad = VisualEffect(name="fireball", kind="explosion", anchor="nobody", radius_state="T", temperature_state="R")
    e = " | ".join(errors(spec(effects=[bad])))
    assert "not a declared entity" in e and "must be a length" in e and "must be a temperature" in e
    e = " | ".join(errors(spec(effects=[VisualEffect(name="halo", kind="glow")])))
    assert "anchor entity or position" in e and "fixed size" in e and "hex colour" in e


def test_phases_times_and_outputs():
    e = " | ".join(errors(spec(phases=[Phase(name="B", start=2.0), Phase(name="A", start=1.0)],
                               outputs=[KeyOutput(label="x", state="nope", measure="max")],
                               entities=[Entity(name="bomb", kind="bomb", appears_at=3.0, disappears_at=9.0)])))
    assert "time order" in e and "not a declared state" in e and "outside the run" in e


def test_physical_words_that_contain_abstract_ones_are_fine():
    ok = errors(spec(entities=[Entity(name="block", kind="graphite block"), Entity(name="ramp", kind="curved ramp")],
                     effects=[]))
    assert not any("not an object" in x for x in ok)


def test_the_prompt_says_what_must_happen():
    flash = VisualEffect(name="flash", kind="flash", anchor="bomb", size=750.0, intensity_state="g", color="#ffffff")
    bomb = spec(prompt="atomic bomb blow up in the event horizon of a black hole", effects=[flash])
    assert any("explosion" in e for e in errors(bomb))
    fixed = VisualEffect(name="fireball", kind="explosion", anchor="bomb", size=50.0, intensity_state="g",
                         color="#ff8800")
    assert any("must grow" in e for e in errors(spec(prompt="a bomb explodes", effects=[fixed])))
    assert errors(spec(prompt="a bomb explodes")) == []  # the default spec has a growing fireball
    assert not any("explosion" in e for e in errors(spec(prompt="a ball rolls down a ramp", effects=[])))
    assert not any("fire" in e for e in errors(spec(prompt="a firefly circles a lamp", effects=[])))


def test_objects_and_negated_events_need_no_effect():
    from omnific_modeler.validation import _prompt_events

    assert [k for k, _ in _prompt_events("atomic bomb blow up in the event horizon of a black hole")] == ["explosion"]
    assert _prompt_events("An unexploded bomb falls onto sand without detonating") == []
    assert _prompt_events("a match that doesn't ignite") == []
    assert [k for k, _ in _prompt_events("the rocket explodes and burns")] == ["explosion", "fire"]


def test_gunfire_is_not_flames():
    from omnific_modeler.validation import _prompt_events

    for prompt in ("an AK-47 fires at a barrel of oobleck", "they open fire on the target", "rapid-fire rifle test",
                   "fire the gun into water", "AK-47 and AWP guns shooting a barrel of non-newtonian fluid"):
        assert "fire" not in [k for k, _ in _prompt_events(prompt)], prompt
    for prompt in ("a log fire burns in the hearth", "the house is on fire", "a candle flame flickers",
                   "burning a log", "ignite the fuel", "a fire on the hillside"):
        assert "fire" in [k for k, _ in _prompt_events(prompt)], prompt


def test_piecewise_catch_all_true_is_allowed():
    from omnific_modeler.validation import validate_spec
    from omnific_sim.spec import Equation, Parameter

    s = spec(parameters=[Parameter(name="t0", value=1.0, unit="second")],
             equations=[Equation(label="radius", expression="R = Piecewise((0, t < t0), (2*(t - t0)*1, True))"),
                        Equation(label="temp", expression="T = 300"), Equation(label="glow", expression="g = 0")],
             entities=[Entity(name="bomb", kind="atomic bomb")])
    assert not [e for e in validate_spec(s) if "True" in e]
