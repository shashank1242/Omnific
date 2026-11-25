"""Build the e2e "blast" fixture with the real pipeline code: a bomb falling toward a black hole detonates; the Data
agent's playback (effects, phases, key outputs) and SceneBuild's scene.gltf. Run from the repo root:
    uv run --no-sync python apps/web/e2e/fixtures/make_blast.py
"""

import json
from pathlib import Path

import numpy as np
from omnific_data.activities import build_playback, dumps
from omnific_mesh.scene import assemble
from omnific_sim.result import SimResult
from omnific_sim.spec import (
    Domain,
    Entity,
    EntityGeometry,
    Environment,
    Fidelity,
    KeyOutput,
    Phase,
    SimSpec,
    StateVariable,
    TimeSpan,
    VisualEffect,
)

OUT = Path(__file__).with_name("blast")
T_DET, T_END = 2.0, 8.0

spec = SimSpec(
    title="A bomb detonates at a black hole's horizon", prompt="atomic bomb blows up at the event horizon of a black hole",
    domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=["illustrative fixture"], parameters=[],
    equations=[], time=TimeSpan(end=T_END, stop="fixed", output_samples=801),
    state=[StateVariable(name="x_bomb", unit="meter", initial_value=-30.0),
           StateVariable(name="R_fire", unit="meter", initial_value=0.0),
           StateVariable(name="T_fire", unit="kelvin", initial_value=0.0),
           StateVariable(name="glow", unit="", initial_value=0.0),
           StateVariable(name="R_shock", unit="meter", initial_value=0.0),
           StateVariable(name="redshift", unit="", initial_value=1.0)],
    entities=[Entity(name="bomb", kind="atomic bomb", bound_state=["x_bomb"], disappears_at=T_DET,
                     geometry=EntityGeometry(shape="cylinder", size=[1.5, 3.5, 1.5], position=[])),
              Entity(name="black hole", kind="black hole",
                     geometry=EntityGeometry(shape="sphere", size=[16.0, 16.0, 16.0], position=[16.0, 8.0, 0.0]))],
    effects=[VisualEffect(name="detonation flash", kind="flash", anchor="bomb", size=6.0, intensity_state="glow",
                          color="#fff6e0"),
             VisualEffect(name="fireball", kind="explosion", anchor="bomb", radius_state="R_fire",
                          intensity_state="glow", temperature_state="T_fire"),
             VisualEffect(name="shock front", kind="shockwave", anchor="bomb", radius_state="R_shock",
                          intensity_state="glow", color="#bcd8ff"),
             VisualEffect(name="accretion disk", kind="accretion_disk", position=[16.0, 8.0, 0.0], size=30.0,
                          color="#ff9a3c")],
    phases=[Phase(name="Falling in", start=0.0, description="The bomb falls toward the horizon"),
            Phase(name="Detonation", start=T_DET, description="A flash and a white-hot fireball"),
            Phase(name="Fireball grows", start=2.6),
            Phase(name="Redshifted fade", start=5.0, description="The light stretches and dims for a far observer")],
    outputs=[KeyOutput(label="Peak fireball radius", state="R_fire", measure="max", why="How big the blast gets"),
             KeyOutput(label="Peak fireball temperature", state="T_fire", measure="max"),
             KeyOutput(label="Time of peak brightness", state="glow", measure="time_of_max"),
             KeyOutput(label="Redshift at the end", state="redshift", measure="final",
                       why="How stretched the light is when it reaches a far observer")],
    environment=Environment(name="Near a black hole", description="Empty space beside a spinning black hole",
                            kind="space", ground="none", ground_material="", lighting="space", sky_color="#000000",
                            ground_color="#000000", fog=0.0, parameters=[]),
)

t = np.linspace(0.0, T_END, 801)
after = np.clip(t - T_DET, 0.0, None)
state = {
    "x_bomb": np.minimum(-30.0 + 0.5 * 7.0 * t**2, -30.0 + 0.5 * 7.0 * T_DET**2),
    "R_fire": 12.0 * (1 - np.exp(-1.6 * after)),
    "T_fire": np.where(after > 0, 9000.0 * np.exp(-0.45 * after) + 600.0, 0.0),
    "glow": np.where(after > 0, (1 - np.exp(-25 * after)) * np.exp(-0.5 * after), 0.0),
    "R_shock": 22.0 * (1 - np.exp(-0.9 * after)),
    "redshift": 1.0 + 3.0 * after,
}
result = SimResult(t=t, state=state, units={k: s.unit for k, s in ((s.name, s) for s in spec.state)})
pb = build_playback(spec, result)
gltf, _assets = assemble(spec, [], lambda key: b"", pb["length_scale"])
OUT.mkdir(exist_ok=True)
(OUT / "playback.json").write_bytes(dumps(pb))
(OUT / "scene.gltf").write_text(json.dumps(gltf), encoding="utf-8")
(OUT / "spec.json").write_text(spec.model_dump_json(), encoding="utf-8")
print(f"wrote {OUT}: {len(pb['t'])} frames, effects {[e['name'] for e in pb['effects']]}, ls {pb['length_scale']:.3g}")
