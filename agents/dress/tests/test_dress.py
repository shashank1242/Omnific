import io
import json

import pytest
from omnific_common.contracts import (
    Attribution,
    BlenderRunOutput,
    CheckResult,
    DressEvaluateInput,
    DressScript,
    DressScripts,
    DressWriteInput,
    EntityReport,
    SceneReport,
)
from omnific_common.storage import LocalStore
from omnific_dress import activities
from omnific_dress.brief import build_brief, motion_ranges
from omnific_sim.spec import Domain, Entity, Environment, Fidelity, SimSpec, TimeSpan

ENV = Environment(name="Playground", description="A sunny park", kind="outdoor", ground="flat",
                  ground_material="grass", lighting="day", sky_color="#9ec9ff", ground_color="#4f7d3a", fog=0.1,
                  parameters=[])
SPEC = SimSpec(title="Ball on a swing", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.ANALYTIC,
               assumptions=[], parameters=[], state=[], equations=[], time=TimeSpan(end=2.0), environment=ENV,
               entities=[Entity(name="ball", kind="sphere", look="a red rubber ball"),
                         Entity(name="seat", kind="box", pivot=[0, 4, 0], arm=[0, -2.5, 0])])
PLAYBACK = {
    "version": 2, "t": [0, 1, 2], "length_scale": 2.0,
    "series": {"x": {"unit": "meter", "values": [0, 1, 2]}, "T": {"unit": "kelvin", "values": [1, 2, 3]},
               "th": {"unit": "radian", "values": [0.1, 0, -0.1]}},
    "entities": [{"name": "ball", "kind": "sphere", "bound_state": ["x"]},
                 {"name": "seat", "kind": "box", "bound_state": ["th"], "pivot": [0, 8, 0], "arm": [0, -5, 0]}],
    "tracks": [{"entity": "ball", "channel": "position_x", "state": "x", "min": 0, "max": 2, "scale": 2.0},
               {"entity": "ball", "channel": "color", "state": "T", "min": 1, "max": 3, "scale": 1.0},
               {"entity": "seat", "channel": "rotation_z", "state": "th", "min": -0.1, "max": 0.1, "scale": 1.0}],
    "environment": ENV.model_dump(),
}
GLTF = {
    "nodes": [{"name": "ball", "mesh": 0, "translation": [0, 0.5, 1.0]}, {"name": "seat", "mesh": 0}],
    "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    "accessors": [{"min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5]}],
}
REPORT = SceneReport(backend="generated", entities=[EntityReport(
    entity="ball", final="library", attempts=1, route_used="library", evals=[],
    attribution=Attribution(title="Ball", author="ann", license="CC-BY 4.0", url="u", source="polypizza"))])


def test_motion_ranges_cover_tracks_and_swings():
    r = motion_ranges(PLAYBACK, GLTF)
    assert r["ball"] == ([0.0, 0.5, 1.0], [4.0, 0.5, 1.0])
    assert r["seat"] == ([-5.0, 3.0, -5.0], [5.0, 13.0, 5.0])


def test_brief_mentions_what_the_writer_needs():
    text = build_brief("a ball rolls past a swing", SPEC, PLAYBACK, GLTF, REPORT)
    assert "Playground" in text and "grass" in text
    assert "1 metre = 2 scene units" in text
    assert "a red rubber ball" in text
    assert "library model 'Ball' by ann (CC-BY 4.0)" in text
    assert "colour shows T" in text
    # viewer (x, y, z) -> Blender (x, -z, y): the ball's rest z=1 becomes y=-1
    assert "moves within (0.00, -1.00, 0.50)..(4.00, -1.00, 0.50)" in text
    assert "ALL MOTION" in text


def test_normalize_scripts_dedupes_orders_and_unfences():
    out = activities.normalize_scripts(DressScripts(scripts=[
        DressScript(agent="lighting", code="```python\nx = 1\n```", notes=""),
        DressScript(agent="look", code="a = 1", notes=""),
        DressScript(agent="lighting", code="y = 2", notes="dup")]))
    assert [s.agent for s in out.scripts] == ["look", "lighting"]
    assert out.scripts[1].code == "x = 1\n"


class FakeLLM:
    def __init__(self, verdict=None, fail_vision=False):
        self.calls = []
        self.verdict = verdict
        self.fail_vision = fail_vision

    async def structured(self, role, instructions, text, schema):
        self.calls.append(("text", role, instructions, text))
        return DressScripts(scripts=[DressScript(agent="look", code="omnific.restyle('ball', roughness=0.4)",
                                                 notes="rubber")])

    async def structured_images(self, role, instructions, text, images, schema):
        self.calls.append(("vision", role, instructions, text))
        if self.fail_vision:
            raise RuntimeError("vision down")
        return self.verdict


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    store.put("jobs/j/playback.json", json.dumps(PLAYBACK).encode())
    store.put("jobs/j/scene.gltf", json.dumps(GLTF).encode())
    monkeypatch.setattr(activities, "get_store", lambda: store)
    return store


async def test_write_uses_reasoning_model_and_feeds_back_the_critique(env, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(activities, "llm", fake)
    prev = [DressScript(agent="scenery", code="make_tree()", notes="")]
    out = await activities.dress_write(DressWriteInput(
        job_id="j", prompt="p", spec=SPEC, playback_key="jobs/j/playback.json", scene_key="jobs/j/scene.gltf",
        scene_report=REPORT, attempt=1, critique="the tree hides the ball", previous=prev))
    assert [s.agent for s in out.scripts] == ["look"]
    _, role, instructions, text = fake.calls[0]
    assert role.value == "MODEL_REASONING"
    assert "omnific.restyle" in instructions
    assert "the tree hides the ball" in text and "make_tree()" in text


def harness_report(**over):
    rep = {"scripts": [{"name": "00_look", "ok": True, "error": ""}],
           "checks": {"protected": {"ok": True, "changed": []}, "gltf_protected": {"ok": True},
                      "clearance": {"ok": True, "hits": [], "scenery_objects": 3},
                      "fidelity": {"ok": True, "max_error": 1e-7, "frames": 301, "where": ""},
                      "framing": {"ok": False, "worst": {"ball": 0.5}}}}
    rep["checks"].update(over)
    return rep


def png_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(buf, format="PNG")
    return buf.getvalue()


SCENE_OK = (CheckResult(name="scene.entities", passed=True, detail="ok"),)


def evaluate_input(store, report, stills=2, scene_checks=SCENE_OK):
    store.put("jobs/j/dress/report.json", json.dumps(report).encode())
    keys = []
    for i in range(stills):
        store.put(f"jobs/j/dress/stills/{i}.png", png_bytes())
        keys.append(f"jobs/j/dress/stills/{i}.png")
    run = BlenderRunOutput(ok=True, report_key="jobs/j/dress/report.json", scene_key="jobs/j/dress/scene.gltf",
                           blend_key="jobs/j/dress/scene.blend", still_keys=keys)
    return DressEvaluateInput(job_id="j", prompt="p", spec=SPEC, attempt=0, run=run, scene_checks=list(scene_checks))


async def test_evaluate_passes_with_critic_and_ignores_blend_checks_for_gating(env, monkeypatch):
    fake = FakeLLM(verdict=activities.Verdict(clear=True, score=8, defects=[], critique=""))
    monkeypatch.setattr(activities, "llm", fake)
    ev = await activities.dress_evaluate(evaluate_input(env, harness_report(), scene_checks=[
        CheckResult(name="scene.entities", passed=True, detail="ok")]))
    assert ev.passed
    names = {c.name: c.passed for c in ev.checks}
    assert names["blend.framing"] is False and names["dress.scene.entities"] and names["dress.critic"]
    assert fake.calls[0][1].value == "MODEL_GENERAL"  # a different model from the writer


async def test_evaluate_fails_on_protected_change_without_calling_the_critic(env, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(activities, "llm", fake)
    ev = await activities.dress_evaluate(evaluate_input(env, harness_report(protected={"ok": False,
                                                                                         "changed": ["ball"]})))
    assert not ev.passed and fake.calls == []
    assert "only omnific.restyle" in ev.critique


async def test_evaluate_critic_rejects_and_fails_open(env, monkeypatch):
    monkeypatch.setattr(activities, "llm", FakeLLM(verdict=activities.Verdict(
        clear=False, score=3, defects=["the ball is hidden"], critique="move the tree")))
    ev = await activities.dress_evaluate(evaluate_input(env, harness_report()))
    assert not ev.passed and ev.critique == "move the tree"
    monkeypatch.setattr(activities, "llm", FakeLLM(fail_vision=True))
    ev = await activities.dress_evaluate(evaluate_input(env, harness_report()))
    assert ev.passed and "critic unavailable" in next(c.detail for c in ev.checks if c.name == "dress.critic")


async def test_evaluate_reports_runner_failure(env):
    ev = await activities.dress_evaluate(DressEvaluateInput(
        job_id="j", prompt="p", spec=SPEC, attempt=2, run=BlenderRunOutput(ok=False, stderr="boom")))
    assert not ev.passed and ev.checks[0].name == "dress.run" and "boom" in ev.critique



def test_brief_lists_the_props_with_real_sizes():
    from omnific_common.contracts import DressPropOption

    opts = [DressPropOption(id="park_bench", name="Park Bench", tags=["bench", "wood"], size=[1.5, 0.5, 0.9]),
            DressPropOption(id="rock_01", name="Rock")]
    text = build_brief("p", SPEC, PLAYBACK, GLTF, None, opts)
    assert "- park_bench: Park Bench, 1.50 x 0.50 x 0.90 m [bench, wood]" in text
    assert "- rock_01: Rock, size unknown m" in text
    assert "PROPS: none available" in build_brief("p", SPEC, PLAYBACK, GLTF, None, [])


def test_only_offered_props_are_kept():
    from omnific_common.contracts import DressDraft

    out = activities.normalize_scripts(DressDraft(scripts=[DressScript(agent="scenery", code="x = 1", notes="")],
                                                  props=["park_bench", "invented", "park_bench"]), {"park_bench"})
    assert out.props == ["park_bench"]


async def test_write_offers_the_props_and_asks_for_a_draft(env, monkeypatch):
    from omnific_common.contracts import DressDraft, DressPropOption

    class Draft(FakeLLM):
        async def structured(self, role, instructions, text, schema):
            self.calls.append(("text", role, instructions, text))
            assert schema is DressDraft
            return DressDraft(scripts=[DressScript(agent="scenery", code="omnific.prop('park_bench')", notes="")],
                              props=["park_bench", "ufo"])

    fake = Draft()
    monkeypatch.setattr(activities, "llm", fake)
    out = await activities.dress_write(DressWriteInput(
        job_id="j", prompt="p", spec=SPEC, playback_key="jobs/j/playback.json", scene_key="jobs/j/scene.gltf",
        prop_options=[DressPropOption(id="park_bench", name="Park Bench")]))
    assert out.props == ["park_bench"]
    assert "omnific.prop(id" in fake.calls[0][2] and "park_bench: Park Bench" in fake.calls[0][3]



async def test_evaluate_fails_without_the_scene_contract(env, monkeypatch):
    monkeypatch.setattr(activities, "llm", FakeLLM())
    ev = await activities.dress_evaluate(evaluate_input(env, harness_report(), scene_checks=()))
    assert not ev.passed and any(c.name == "dress.scene" and not c.passed for c in ev.checks)
