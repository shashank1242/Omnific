"""Mesh v2 CPU stages: GLB round trip, postprocess, rig/skin/deformation and clip evaluators, plan repair, scene."""

import numpy as np
import pytest
from omnific_common.contracts import AssetPlan, ClipPlan, MeshArtifact, MeshCapabilities
from omnific_mesh import anim, meshops, rig
from omnific_mesh.asset import Asset, Bone, Clip, axis_angle_quat, from_glb, to_glb
from omnific_mesh.assets import box, sphere
from omnific_mesh.plan import default_plan, plan_assets, repair
from omnific_mesh.scene import assemble, evaluate_scene
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, StateVariable, TimeSpan


def spec_with(entities, states=()):
    return SimSpec(title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
                   parameters=[], equations=[], time=TimeSpan(end=1.0), entities=entities,
                   state=[StateVariable(name=s, unit="m", initial_value=0.0, description="") for s in states])


def asset_from(gen) -> Asset:
    pos, nrm, idx = gen
    return Asset(positions=pos, normals=nrm, indices=idx.reshape(-1, 3), colors=np.full((len(pos), 3), 0.5,
                                                                                        np.float32))


def icosphere(radius=0.5, subdivisions=3) -> Asset:
    import trimesh

    s = trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)
    return Asset(positions=s.vertices.astype(np.float32), indices=s.faces.astype(np.uint32),
                 colors=np.full((len(s.vertices), 3), 0.5, np.float32))


def creature() -> Asset:
    """A body box on two leg boxes, skinned to root + two leg bones."""
    parts = []
    for centre, size in (((0, 1.0, 0), (2, 0.6, 0.8)), ((-0.6, 0.35, 0), (0.25, 0.7, 0.25)),
                         ((0.6, 0.35, 0), (0.25, 0.7, 0.25))):
        p, n, i = box(*size)
        parts.append((p + np.array(centre, np.float32), n, i.reshape(-1, 3)))
    pos = np.concatenate([p for p, _, _ in parts])
    off = np.cumsum([0] + [len(p) for p, _, _ in parts[:-1]])
    idx = np.concatenate([i + o for (_, _, i), o in zip(parts, off, strict=True)]).astype(np.uint32)
    a = Asset(positions=pos, indices=idx, colors=np.full((len(pos), 3), 0.6, np.float32))
    a.bones = [Bone("root", -1, np.array([0, 1.0, 0], np.float32)),
               Bone("leg_l", 0, np.array([-0.6, 0.7, 0], np.float32)),
               Bone("leg_r", 0, np.array([0.6, 0.7, 0], np.float32))]
    owner = np.repeat([0, 1, 2], [len(p) for p, _, _ in parts])
    a.joints = np.zeros((len(pos), 4), np.uint16)
    a.joints[:, 0] = owner
    a.weights = np.zeros((len(pos), 4), np.float32)
    a.weights[:, 0] = 1.0
    return a


# --- GLB I/O -----------------------------------------------------------------------------------

def test_glb_round_trip_keeps_skin_and_clips():
    a = creature()
    a.clips = [anim.procedural_clip(a, ClipPlan(name="walk", kind="locomotion", description="", drive_state=""))]
    b = from_glb(to_glb(a, "dog"))
    np.testing.assert_allclose(b.positions, a.positions, atol=1e-6)
    assert [x.name for x in b.bones] == ["root", "leg_l", "leg_r"] and [x.parent for x in b.bones] == [-1, 0, 0]
    np.testing.assert_allclose(b.bones[1].head, a.bones[1].head, atol=1e-6)
    np.testing.assert_allclose(b.weights, a.weights)
    assert [c.name for c in b.clips] == ["walk"]
    np.testing.assert_allclose(b.clips[0].rotations["leg_l"], a.clips[0].rotations["leg_l"], atol=1e-6)


def test_glb_texture_round_trip():
    from omnific_mesh.asset import png
    from PIL import Image

    a = asset_from(box())
    a.colors, a.uvs = None, np.random.default_rng(0).random((len(a.positions), 2)).astype(np.float32)
    a.texture, a.base_color = png(Image.new("RGB", (8, 8), (255, 0, 0))), (1.0, 1.0, 1.0, 1.0)  # factor x texture
    b = from_glb(to_glb(a))
    assert b.texture == a.texture and b.uvs is not None
    assert np.allclose(meshops.vertex_colors(b), [1, 0, 0], atol=1e-3)  # sRGB red -> linear 1.0


# --- generate / postprocess checks -------------------------------------------------------------

def by_name(checks):
    return {c.name: c for c in checks}


def test_generated_mesh_checks():
    good = by_name(meshops.evaluate_generated(icosphere()))
    assert all(c.passed for c in good.values()), good
    # the MVP UV sphere has zero-area triangles at its poles: exactly what the check is for
    assert not by_name(meshops.evaluate_generated(asset_from(sphere())))["mesh.degenerate_faces"].passed
    bad = icosphere()
    bad.positions[0, 0] = np.nan
    assert not by_name(meshops.evaluate_generated(bad))["mesh.finite"].passed
    tiny = Asset(positions=np.eye(3, dtype=np.float32), indices=np.array([[0, 1, 2]], np.uint32))
    checks = by_name(meshops.evaluate_generated(tiny))
    assert not checks["mesh.non_empty"].passed


def test_postprocess_removes_floaters_scales_and_decimates():
    main = icosphere(radius=3.0, subdivisions=4)
    p, n, i = box(0.01, 0.01, 0.01)
    fl = asset_from((p + 20, n, i))
    merged = Asset(positions=np.concatenate([main.positions, fl.positions]),
                   indices=np.concatenate([main.indices, fl.indices + len(main.positions)]).astype(np.uint32),
                   colors=np.concatenate([main.colors, fl.colors]))
    clean, notes = meshops.postprocess(Asset(**vars(merged)), scene_size=1.0, poly_budget=1500)
    assert clean.n_tris <= 1500 and any("floater" in x for x in notes) and any("decimated" in x for x in notes)
    checks = by_name(meshops.evaluate_postprocessed(main, clean, 1.0, 1500))
    assert all(c.passed for c in checks.values()), {k: c.detail for k, c in checks.items() if not c.passed}


def test_postprocess_eval_catches_shape_change_and_budget():
    before = icosphere()
    after = asset_from(box())
    after.positions = after.positions.astype(np.float32)
    checks = by_name(meshops.evaluate_postprocessed(before, after, 1.0, 5))
    assert not checks["post.shape_preserved"].passed and not checks["post.poly_budget"].passed


# --- rigs ---------------------------------------------------------------------------------------

def pendulum_spec():
    return spec_with([Entity(name="bob", kind="ball", pivot=[0, 2, 0], arm=[0, -1.5, 0]),
                      Entity(name="bob2", kind="ball", parent="bob", pivot=[0, 0, 0], arm=[0, -1, 0])])


def test_physics_joint_matches_spec_and_viewer_math():
    from omnific_mesh.gltf import rest_positions

    spec = pendulum_spec()
    joint = rig.physics_joint(spec, "bob")
    k = rig.length_scale(spec.entities)
    assert joint == pytest.approx([0, 2 * k, 0, 0, -1.5 * k, 0])
    checks = rig.evaluate_physics_joint(spec, "bob", joint, rest_positions(spec.entities)["bob"])
    assert all(c.passed for c in checks), checks
    # pose formula == lib/articulation.ts: angle pi/2 swings the arm from -y to +x
    np.testing.assert_allclose(rig.pose_offset([0, -1, 0], np.pi / 2), [1, 0, 0], atol=1e-12)
    wrong = list(joint)
    wrong[4] += 0.5
    assert not by_name(rig.evaluate_physics_joint(spec, "bob", wrong, None))["joint.matches_spec"].passed
    assert rig.physics_joint(spec_with([Entity(name="b", kind="box")]), "b") == []


def test_skin_checks():
    a = creature()
    assert all(c.passed for c in rig.evaluate_skin(a))
    bad = creature()
    bad.weights[0, 0] = 0.5
    assert not by_name(rig.evaluate_skin(bad))["skin.weights_sum_to_1"].passed
    cyc = creature()
    cyc.bones[0].parent = 2
    assert not by_name(rig.evaluate_skin(cyc))["skin.tree"].passed
    far = creature()
    far.bones[2].head = np.array([50, 0, 0], np.float32)
    assert not by_name(rig.evaluate_skin(far))["skin.bones_inside"].passed


def test_deformation_stretch():
    a = creature()
    assert all(c.passed for c in rig.evaluate_deformation(a))  # rigid parts: no stretch
    # a bar whose right half is skinned to a bone whose head sits at the far end: the seam at x=0 tears
    xs = np.linspace(-1, 1, 21)
    pos = np.array([(x, y, 0) for x in xs for y in (0.0, 0.1)], np.float32)
    faces = [(2 * i, 2 * i + 2, 2 * i + 1) for i in range(20)] + [(2 * i + 1, 2 * i + 2, 2 * i + 3) for i in range(20)]
    torn = Asset(positions=pos, indices=np.array(faces, np.uint32))
    torn.bones = [Bone("root", -1, np.array([-1, 0, 0], np.float32)), Bone("arm", 0, np.array([1, 0, 0], np.float32))]
    torn.joints = np.zeros((len(pos), 4), np.uint16)
    torn.joints[:, 0] = (pos[:, 0] > 0).astype(np.uint16)
    torn.weights = np.zeros((len(pos), 4), np.float32)
    torn.weights[:, 0] = 1
    checks = by_name(rig.evaluate_deformation(torn))
    assert not checks["deform.edge_stretch"].passed and not checks["deform.stretched_share"].passed


def test_a_joint_that_stretches_a_few_small_triangles_passes():
    # a long bar with a fine strip of tiny triangles at the elbow: bending stretches those few edges ~2-3x (normal
    # linear-blend skinning), which must not fail the rig the way a tear does
    xs = np.concatenate([np.linspace(-1, -0.01, 400), [-0.003, 0.003], np.linspace(0.01, 1, 400)])
    pos = np.array([(x, y, 0) for x in xs for y in (0.0, 0.1)], np.float32)
    m = len(xs) - 1
    faces = [(2 * i, 2 * i + 2, 2 * i + 1) for i in range(m)] + [(2 * i + 1, 2 * i + 2, 2 * i + 3) for i in range(m)]
    elbow = Asset(positions=pos, indices=np.array(faces, np.uint32))
    elbow.bones = [Bone("root", -1, np.array([-1, 0, 0], np.float32)), Bone("arm", 0, np.array([0, 0, 0], np.float32))]
    # weights blend across the elbow, as a real skin does (a hard 0/1 cut there would be a tear)
    w = np.clip((pos[:, 0] + 0.05) / 0.1, 0, 1)
    elbow.joints = np.zeros((len(pos), 4), np.uint16)
    elbow.joints[:, 1] = 1
    elbow.weights = np.zeros((len(pos), 4), np.float32)
    elbow.weights[:, 0], elbow.weights[:, 1] = 1 - w, w
    checks = by_name(rig.evaluate_deformation(elbow))
    assert checks["deform.edge_stretch"].passed and checks["deform.stretched_share"].passed, checks


def test_skin_identity_at_rest_and_rotation_moves_leg():
    a = creature()
    np.testing.assert_allclose(rig.skin(a), a.positions, atol=1e-6)
    posed = rig.skin(a, {"leg_l": axis_angle_quat((0, 0, 1), np.pi / 2)})
    moved = a.joints[:, 0] == 1
    assert np.abs(posed[moved] - a.positions[moved]).max() > 0.1
    np.testing.assert_allclose(posed[~moved], a.positions[~moved], atol=1e-6)


# --- clips --------------------------------------------------------------------------------------

def test_procedural_clips_pass_their_checks():
    a = creature()
    for kind in ("idle", "locomotion", "flex"):
        clip = anim.procedural_clip(a, ClipPlan(name=kind, kind=kind, description="", drive_state=""))
        checks = anim.evaluate_clip(a, clip, kind)
        assert all(c.passed for c in checks), (kind, [c for c in checks if not c.passed])
    walk = anim.procedural_clip(a, ClipPlan(name="w", kind="locomotion", description="", drive_state=""))
    assert set(walk.rotations) == {"leg_l", "leg_r"}  # legs were found


def test_clip_checks_catch_problems():
    a = creature()
    t = np.linspace(0, 2, 61).astype(np.float32)
    jumpy = Clip("j", t, {"leg_l": axis_angle_quat((1, 0, 0), np.where(t > 1, 1.0, 0.0))})
    checks = by_name(anim.evaluate_clip(a, jumpy, "idle"))
    assert not checks["anim.smooth"].passed and not checks["anim.loops"].passed
    wild = Clip("w", t, {"leg_l": axis_angle_quat((1, 0, 0), 2.5 * np.sin(np.pi * t))})
    assert not by_name(anim.evaluate_clip(a, wild, "flex"))["anim.joint_limits"].passed
    drift = Clip("d", t, {"leg_l": axis_angle_quat((1, 0, 0), 0 * t)}, root_translation=np.c_[t, 0 * t, 0 * t])
    assert not by_name(anim.evaluate_clip(a, drift, "flex"))["anim.no_root_motion"].passed
    short = Clip("s", t[:5], {"leg_l": axis_angle_quat((1, 0, 0), 0 * t[:5])})
    assert not by_name(anim.evaluate_clip(a, short, "flex"))["anim.duration"].passed


# --- plan / router ---------------------------------------------------------------------------------

GPU = MeshCapabilities(gpu=True)
ALL = MeshCapabilities(gpu=True, cad=True, library_sources=["polypizza"])


def plan(entity, kind, routes, gen_prompt="", rig="none", clips=(), budget=8000, **kw):
    return AssetPlan(entity=entity, kind=kind, routes=routes, route_reason=kw.pop("route_reason", ""),
                     search_query=kw.pop("search_query", ""), cad_brief=kw.pop("cad_brief", ""),
                     gen_prompt=gen_prompt, rig=rig, clips=list(clips), poly_budget=budget, **kw)


def test_default_plan():
    spec = pendulum_spec()
    assert [(p.mode, p.rig) for p in default_plan(spec)] == [("procedural", "physics"), ("procedural", "physics")]


def test_plan_repair():
    spec = spec_with([Entity(name="dog", kind="dog"), Entity(name="bob", kind="ball", pivot=[0, 0, 0],
                                                             arm=[0, -1, 0]),
                      Entity(name="floor", kind="ground plane")], states=["v"])
    clip = ClipPlan(name="walk", kind="locomotion", description="trot", drive_state="speed")
    plans = [plan("dog", "dog", ["generated", "procedural"], "a beagle", "ml", [clip], budget=10),
             plan("bob", "ball", ["generated", "procedural"], "a brass bob"),
             plan("floor", "ground plane", ["generated", "procedural"], "grass"),
             plan("ghost", "x", ["procedural"], budget=0)]
    fixed, checks = repair(spec, plans, GPU)
    by = {p.entity: p for p in fixed}
    assert [p.entity for p in fixed] == ["dog", "bob", "floor"]
    assert by["dog"].poly_budget == 20_000 and by["dog"].clips[0].drive_state == ""  # unknown state cleared
    assert by["bob"].rig == "physics"
    assert by["floor"].routes == ["procedural"] and by["floor"].mode == "procedural"
    cc = by_name(checks)
    assert not cc["plan.coverage"].passed and "ghost" in cc["plan.coverage"].detail


def test_router_rules():
    spec = spec_with([Entity(name="car", kind="car"), Entity(name="arm", kind="robot arm", pivot=[0, 1, 0],
                                                             arm=[0.5, 0, 0]),
                      Entity(name="dog", kind="dog"), Entity(name="ball", kind="ball")], states=["v"])
    plans = [plan("car", "car", ["library", "library", "generated"], "a red car", search_query="red car"),
             plan("arm", "robot arm", ["generated", "cad"], "a robot arm", cad_brief="two links 0.5 m"),
             plan("dog", "dog", ["library", "procedural", "generated"], "a beagle", "ml"),  # no search_query
             plan("ball", "ball", [], "")]
    fixed, checks = repair(spec, plans, ALL)
    by = {p.entity: p for p in fixed}
    assert by["car"].routes == ["library", "generated", "procedural"]  # deduped, procedural appended
    assert by["arm"].routes == ["cad", "generated", "procedural"]      # exact joints first for an articulated body
    assert by["dog"].routes == ["procedural"]                          # library dropped; nothing after procedural
    assert by["dog"].rig == "none" and by["dog"].gen_prompt == ""      # ML rig needs generated/library
    assert by["ball"].routes == ["procedural"] and by["ball"].poly_budget == 0
    assert by_name(checks)["plan[car]"].passed is True
    assert "cad" in by_name(checks)["plan[arm]"].detail

    # only what this deployment serves survives
    fixed, checks = repair(spec, plans, GPU)
    by = {p.entity: p for p in fixed}
    assert by["car"].routes == ["generated", "procedural"] and by["arm"].routes == ["generated", "procedural"]
    assert "unavailable" in by_name(checks)["plan[car]"].detail


async def test_planner_without_routes_skips_the_llm():
    class NoLLM:
        async def structured(self, *a, **k):
            raise AssertionError("no LLM call when only procedural is available")

    plans, ev = await plan_assets(NoLLM(), pendulum_spec(), "p", "generated", MeshCapabilities())
    assert ev.passed and all(p.routes == ["procedural"] for p in plans)


def test_route_stats_render():
    from omnific_rag.route_stats import norm_kind, render
    assert norm_kind("  Red   CAR ") == "red car"
    assert render({"car": {"library": (9, 10), "cad": (1, 4)}}) == "- car: cad 1/4, library 9/10"


# --- scene --------------------------------------------------------------------------------------

def test_procedural_scene_is_the_mvp_scene():
    from omnific_mesh.gltf import build_gltf, rest_positions

    spec = pendulum_spec()
    arts = [MeshArtifact(entity=e.name, source="procedural", asset="sphere") for e in spec.entities]
    gltf, _ = assemble(spec, arts, load=lambda k: b"")
    mvp, _ = build_gltf([(e.name, "sphere", []) for e in spec.entities], rest_positions(spec.entities))
    assert gltf == mvp


def test_mixed_scene_with_generated_skinned_asset(tmp_path):
    import json

    spec = spec_with([Entity(name="dog", kind="dog"), Entity(name="floor", kind="ground plane")])
    a = creature()
    a.clips = [anim.procedural_clip(a, ClipPlan(name="walk", kind="locomotion", description="", drive_state=""))]
    store = {"k.glb": to_glb(a, "dog")}
    walk = ClipPlan(name="walk", kind="locomotion", description="", drive_state="")
    arts = [MeshArtifact(entity="dog", source="generated", asset="box", glb_key="k.glb", bones=["root"],
                         clips=[walk]),
            MeshArtifact(entity="floor", source="procedural", asset="plane")]
    gltf, assets = assemble(spec, arts, load=store.__getitem__)
    size = len(json.dumps(gltf))
    checks = by_name(evaluate_scene(spec, gltf, size))
    assert all(c.passed for c in checks.values()), {k: c.detail for k, c in checks.items() if not c.passed}
    names = [n["name"] for n in gltf["nodes"]]
    assert "dog" in names and "mesh:dog" in names and "bone:dog:leg_l" in names
    assert [x["name"] for x in gltf["animations"]] == ["dog:walk"]
    assert gltf["nodes"][names.index("dog")]["extras"]["omnific"]["clips"][0]["name"] == "walk"
    assert assets["floor"] == "plane"


def test_scene_checks_catch_missing_entity_and_broken_refs():
    spec = spec_with([Entity(name="a", kind="box"), Entity(name="b", kind="box")])
    gltf, _ = assemble(spec, [MeshArtifact(entity="a", source="procedural", asset="box")], load=lambda k: b"")
    gltf["nodes"] = [n for n in gltf["nodes"] if n["name"] != "b"]
    gltf["nodes"][0]["mesh"] = 99
    checks = by_name(evaluate_scene(spec, gltf, 10))
    assert not checks["scene.entities"].passed and not checks["scene.gltf_structure"].passed


def test_render_produces_png():
    from omnific_mesh import render

    sheet = render.turntable(icosphere())
    assert sheet[:8] == b"\x89PNG\r\n\x1a\n"


# --- activities (fake store + fake critic) ------------------------------------------------------

class FakeLLM:
    def __init__(self, matches=True, score=8):
        self.matches, self.score, self.calls = matches, score, 0

    async def structured_images(self, role, instructions, text, images, schema):
        self.calls += 1
        assert images[0][0][:4] == b"\x89PNG"
        return schema(matches=self.matches, score=self.score, defects=[] if self.matches else ["wrong object"],
                      critique="make it a dog")


@pytest.fixture
def acts(tmp_path, monkeypatch):
    from omnific_common.storage import LocalStore
    from omnific_mesh import activities

    store = LocalStore(tmp_path)
    monkeypatch.setattr(activities, "get_store", lambda: store)
    monkeypatch.setattr(activities, "llm", FakeLLM())
    return activities, store


def dog_plan(**kw):
    base = {"entity": "dog", "kind": "dog", "routes": ["generated", "procedural"], "route_reason": "",
            "search_query": "", "cad_brief": "", "gen_prompt": "a beagle", "rig": "ml",
                "clips": [ClipPlan(name="walk", kind="locomotion", description="trot", drive_state="")],
                "poly_budget": 2000}
    return AssetPlan(**{**base, **kw})


async def test_evaluate_generate_runs_critic_only_after_checks(acts):
    from omnific_common.contracts import EvaluateStageInput

    activities, store = acts
    store.put("raw.glb", to_glb(icosphere()))
    art = MeshArtifact(entity="dog", source="generated", asset="box", glb_key="raw.glb")
    ev = await activities.evaluate_stage(EvaluateStageInput(job_id="j", stage="generate", plan=dog_plan(),
                                                            artifact=art))
    assert ev.passed and activities.llm.calls == 1
    activities.llm.matches = False
    ev = await activities.evaluate_stage(EvaluateStageInput(job_id="j", stage="generate", plan=dog_plan(),
                                                            artifact=art))
    assert not ev.passed and ev.critique == "make it a dog"
    store.put("bad.glb", b"not a glb")
    calls = activities.llm.calls
    ev = await activities.evaluate_stage(EvaluateStageInput(
        job_id="j", stage="generate", plan=dog_plan(), artifact=art.model_copy(update={"glb_key": "bad.glb"})))
    assert not ev.passed and activities.llm.calls == calls  # no critic for an unreadable mesh


async def test_rig_and_animation_evaluation(acts):
    from omnific_common.contracts import EvaluateStageInput

    activities, store = acts
    a = creature()
    store.put("rig.glb", to_glb(a))
    art = MeshArtifact(entity="dog", source="generated", asset="box", glb_key="rig.glb", bones=["root"])
    ev = await activities.evaluate_stage(EvaluateStageInput(job_id="j", stage="ml_rig", plan=dog_plan(), artifact=art))
    assert ev.passed, ev
    a.clips = [anim.procedural_clip(a, dog_plan().clips[0])]
    store.put("anim.glb", to_glb(a))
    ev = await activities.evaluate_stage(EvaluateStageInput(
        job_id="j", stage="animate", plan=dog_plan(), artifact=art.model_copy(update={"glb_key": "anim.glb"})))
    assert ev.passed, ev


async def test_postprocess_and_physics_activities(acts):
    from omnific_common.contracts import MeshStageInput

    activities, store = acts
    store.put("raw.glb", to_glb(icosphere(radius=4.0, subdivisions=4)))
    spec = spec_with([Entity(name="dog", kind="dog", pivot=[0, 1, 0], arm=[0, -1, 0])])
    art = MeshArtifact(entity="dog", source="generated", asset="box", glb_key="raw.glb")
    out = await activities.postprocess_mesh(MeshStageInput(job_id="j", plan=dog_plan(), spec=spec, artifact=art))
    assert out.eval.passed, out.eval
    assert out.artifact.n_tris <= 2000 and store.exists(out.artifact.glb_key)
    assert max(np.subtract(out.artifact.bbox_max, out.artifact.bbox_min)) == pytest.approx(1.0, rel=1e-3)
    pr = await activities.physics_rig(MeshStageInput(job_id="j", plan=dog_plan(rig="both"), spec=spec,
                                                     artifact=out.artifact))
    assert pr.eval.passed and len(pr.artifact.joint) == 6


async def test_procedural_animate_and_cache(acts, monkeypatch):
    from omnific_common.contracts import MeshStageInput, PlanAssetsInput

    activities, store = acts
    store.put("rig.glb", to_glb(creature()))
    spec = spec_with([Entity(name="dog", kind="dog")])
    art = MeshArtifact(entity="dog", source="generated", asset="box", glb_key="rig.glb", bones=["root"])
    out = await activities.procedural_animate(MeshStageInput(job_id="j", plan=dog_plan(), spec=spec, artifact=art))
    assert out.eval.passed and [c.name for c in out.artifact.clips] == ["walk"]
    assert [c.name for c in from_glb(store.get(out.artifact.glb_key)).clips] == ["walk"]
    cached = await activities.cache_asset(MeshStageInput(job_id="j", plan=dog_plan(), spec=spec,
                                                         artifact=out.artifact))
    assert cached.glb_key.startswith("assets/") and store.exists(cached.glb_key)

    async def plan(llm, spec, prompt, backend, caps=None, stats=""):
        from omnific_common.contracts import StageEval
        return [dog_plan()], StageEval(stage="plan", attempt=0, passed=True, checks=[])

    monkeypatch.setattr(activities, "make_plan", plan)  # the next job with the same plan hits the cache
    hit = await activities.plan_assets(PlanAssetsInput(job_id="j2", prompt="p", spec=spec, backend="generated"))
    assert [c.glb_key for c in hit.cached] == [cached.glb_key]


async def test_assemble_activity_writes_scene(acts):
    import json

    from omnific_common.contracts import AssembleInput

    activities, store = acts
    spec = spec_with([Entity(name="ball", kind="ball")])
    out = await activities.assemble_scene(AssembleInput(job_id="j", spec=spec, artifacts=[
        MeshArtifact(entity="ball", source="procedural", asset="sphere")]))
    assert out.eval.passed and json.loads(store.get(out.scene_key))["nodes"][0]["name"] == "ball"


def test_render_entities_includes_look():
    from omnific_mesh.plan import render_entities
    spec = spec_with([Entity(name="car", kind="car", look="a red vintage race car"),
                      Entity(name="floor", kind="ground")], states=["v"])
    text = render_entities(spec, "p", ALL)
    assert "look: 'a red vintage race car'" in text
    assert text.count("look:") == 1
