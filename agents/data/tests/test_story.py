"""Effects, phases, appearances and headline outputs in the playback."""

import math

import numpy as np
import pytest
from omnific_data import story
from omnific_data.activities import build_playback
from omnific_sim.result import SimResult
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

T_DET = 1.0


def blast_spec(**over):
    spec = SimSpec(
        title="blast", prompt="a bomb goes off", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC,
        assumptions=[], parameters=[], equations=[], time=TimeSpan(end=4.0, stop="fixed", output_samples=401),
        state=[StateVariable(name="R", unit="meter", initial_value=0.0),
               StateVariable(name="T_fb", unit="kelvin", initial_value=0.0),
               StateVariable(name="glow", unit="", initial_value=0.0)],
        entities=[Entity(name="bomb", kind="atomic bomb", disappears_at=T_DET)],
        effects=[VisualEffect(name="fireball", kind="explosion", anchor="bomb", radius_state="R",
                              intensity_state="glow", temperature_state="T_fb"),
                 VisualEffect(name="halo", kind="glow", position=[0.0, 5.0, 0.0], size=2.0, color="#ff8800")],
        phases=[Phase(name="Free fall", start=0.0), Phase(name="Detonation", start=T_DET),
                Phase(name="Fading", start=3.0), Phase(name="Never seen", start=9.0)],
        outputs=[KeyOutput(label="Peak fireball radius", state="R", measure="max", why="how big it gets"),
                 KeyOutput(label="Time of peak brightness", state="glow", measure="time_of_max"),
                 KeyOutput(label="Mean temperature", state="T_fb", measure="mean")],
    )
    return spec.model_copy(update=over)


def blast_result():
    t = np.linspace(0.0, 4.0, 401)
    after = np.clip(t - T_DET, 0.0, None)
    radius = 50.0 * (1 - np.exp(-3 * after))  # grows to ~50 m
    glow = after * np.exp(-2 * after)  # peaks at T_DET + 0.5
    temp = np.where(after > 0, 8000.0 * np.exp(-after), 0.0)
    return SimResult(t=t, state={"R": radius, "T_fb": temp, "glow": glow},
                     units={"R": "meter", "T_fb": "kelvin", "glow": ""})


def test_blackbody_runs_red_to_white_to_blue():
    red, sun, hot = story.blackbody(1500), story.blackbody(5800), story.blackbody(20000)
    assert red[0] > red[1] > red[2]
    assert min(sun) > 0.85  # near white
    assert hot[2] >= hot[0]  # blue-white
    assert story.blackbody(0) == (0.0, 0.0, 0.0) and story.blackbody(float("nan")) == (0.0, 0.0, 0.0)
    assert sum(story.blackbody(600)) < sum(story.blackbody(1000))  # an ember dims as it cools


def test_effects_phases_and_outputs_in_the_playback():
    doc = build_playback(blast_spec(), blast_result())
    ls = doc["length_scale"]
    fire, halo = doc["effects"]
    assert fire["anchor"] == "bomb" and fire["position"] is None
    assert len(fire["radius"]) == len(doc["t"]) and max(fire["radius"]) == pytest.approx(50.0 * ls, rel=1e-3)
    assert fire["intensity"][0] == 0 and max(fire["intensity"]) == pytest.approx(1.0)
    assert len(fire["color"]) == len(doc["t"]) and fire["color"][0] == [0.0, 0.0, 0.0]
    assert halo["position"] == pytest.approx([0.0, 5.0 * ls, 0.0]) and halo["color"] == pytest.approx([1.0, 0.5333, 0.0])
    assert np.allclose(halo["radius"], 2.0 * ls, rtol=1e-4) and set(halo["intensity"]) == {1.0}

    assert [p["name"] for p in doc["phases"]] == ["Free fall", "Detonation", "Fading"]  # 9 s is after the run
    assert doc["phases"][1]["start"] == T_DET and doc["phases"][1]["end"] == 3.0 and doc["phases"][2]["end"] == 4.0
    assert doc["entities"][0]["disappears_at"] == T_DET and "appears_at" not in doc["entities"][0]

    out = {o["label"]: o for o in doc["outputs"]}
    assert out["Peak fireball radius"]["value"] == pytest.approx(50.0, rel=1e-3)
    assert out["Peak fireball radius"]["unit"] == "meter"
    assert out["Time of peak brightness"]["value"] == pytest.approx(T_DET + 0.5, abs=0.02)
    assert out["Time of peak brightness"]["unit"] == "second"
    assert 0 < out["Mean temperature"]["value"] < 8000


def test_no_story_no_new_keys():
    spec = blast_spec(effects=[], phases=[], outputs=[], entities=[Entity(name="bomb", kind="atomic bomb")])
    doc = build_playback(spec, blast_result())
    assert not {"effects", "phases", "outputs"} & set(doc) and set(doc["entities"][0]) == {
        "name", "kind", "bound_state", "parent", "pivot", "arm"}
    assert math.isfinite(doc["length_scale"])
