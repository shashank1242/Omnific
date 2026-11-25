"""CAD route on the mesh side, without an LLM: sandbox output -> asset, rig from joints, kinematic clips, the checks
(overlap tolerances), the cad_evaluate activity, and SceneBuild's write -> run -> evaluate retry loop with a fake
writer and a fake sandbox. One integration test feeds the REAL CAD runner's output through (skipped without it)."""

import io
import json
import shutil
import uuid
import zipfile
from pathlib import Path

import numpy as np
import pytest
import trimesh
from omnific_common.contracts import (
    CAD_RUN_ACTIVITY,
    AssetPlan,
    CadEvaluateInput,
    CadRunInput,
    CadRunOutput,
    ClipPlan,
    MeshArtifact,
    MeshStageInput,
    SceneActivity,
    SceneBuildInput,
    SceneOutput,
    TaskQueue,
)
from omnific_common.storage import LocalStore
from omnific_mesh import activities, cad, cad_skin
from omnific_mesh.asset import Asset, from_glb, to_glb
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, StateVariable, TimeSpan
from temporalio import activity
from temporalio.exceptions import ApplicationError

SPEC = SimSpec(title="cart", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
               parameters=[], equations=[], time=TimeSpan(end=1.0),
               state=[StateVariable(name="v", unit="m/s", initial_value=0.0, description="speed")],
               entities=[Entity(name="cart", kind="cart", bound_state=["v"])])
ROLL = ClipPlan(name="roll", kind="locomotion", description="wheels roll", drive_state="v")
WHEELS = {"wheel_l": (0.25, 0.08, 0.0), "wheel_r": (-0.25, 0.08, 0.0)}


def plan(**kw) -> AssetPlan:
    base = {"entity": "cart", "kind": "cart", "routes": ["cad", "procedural"], "route_reason": "a simple mechanism",
            "search_query": "", "cad_brief": "box body on two wheels", "gen_prompt": "a red cart", "rig": "none",
            "clips": [ROLL], "poly_budget": 4000}
    return AssetPlan(**{**base, **kw})


def cart_output(rest=(), sweep=(), motion="spin", fastened_body=False) -> tuple[dict, dict]:
    """A synthetic sandbox result shaped like cad_harness.py's: body box + two wheels spinning about x."""
    shapes = {"body": trimesh.creation.box((0.4, 0.12, 0.6)).apply_translation((0, 0.2, 0))}
    for name, xyz in WHEELS.items():
        cyl = trimesh.creation.cylinder(radius=0.08, height=0.04, sections=24)
        cyl.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (0, 1, 0)))
        shapes[name] = cyl.apply_translation(xyz)
    parts, meshes = [], {}
    for i, (label, m) in enumerate(shapes.items()):
        meshes[f"v{i}"], meshes[f"f{i}"] = m.vertices.astype(np.float32), m.faces.astype(np.int64)
        lo, hi = m.bounds
        parts.append({"label": label, "index": i, "color": "#c0392b" if label == "body" else "#222222",
                      "valid": True, "volume": float(m.volume), "bbox_min": lo.tolist(), "bbox_max": hi.tolist(),
                      "n_tris": len(m.faces)})
    limits = [-30.0, 30.0]
    joints = [{"name": f"spin_{n}", "kind": "revolute", "parent": "body", "child": n, "origin": list(xyz),
               "axis": [1.0, 0.0, 0.0], "limits": limits, "motion": motion, "clip": "roll"}
              for n, xyz in WHEELS.items()]
    if fastened_body:
        joints = [dict(j, kind="fastened", motion="none") for j in joints]
    return {"units": "m", "parts": parts, "joints": joints,
            "interference": {"rest": list(rest), "sweep": list(sweep)}}, meshes


def zipped(report: dict, meshes: dict) -> bytes:
    npz = io.BytesIO()
    np.savez_compressed(npz, **meshes)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cad.json", json.dumps(report))
        z.writestr("parts.npz", npz.getvalue())
    return buf.getvalue()


def by_name(checks):
    return {c.name: c for c in checks}


# --- asset, rig, clips ------------------------------------------------------------------------------

def test_asset_is_rigged_from_moving_joints():
    report, meshes = cart_output()
    a = cad.build_asset(report, meshes)
    assert [b.name for b in a.bones] == ["root", "spin_wheel_l", "spin_wheel_r"]
    assert [b.parent for b in a.bones] == [-1, 0, 0]
    np.testing.assert_allclose(a.bones[1].head, WHEELS["wheel_l"])
    n_body = len(meshes["v0"])
    assert (a.joints[:n_body, 0] == 0).all() and (a.joints[n_body:n_body + len(meshes["v1"]), 0] == 1).all()
    assert np.allclose(a.colors[0], cad.hex_rgb("#c0392b"))
    # fastened joints add no bones: a rigid asset
    assert cad.build_asset(*cart_output(fastened_body=True)).bones == []


def test_spin_and_oscillate_clips_pass_the_clip_checks():
    report, meshes = cart_output()
    a = cad.build_asset(report, meshes)
    clips, notes = cad.kinematic_clips(report, [ROLL, ClipPlan(name="wave", kind="flex", description="",
                                                               drive_state="")])
    assert [c.name for c, _, _ in clips] == ["roll"] and "wave" in notes[0]
    clip, _, limit = clips[0]
    assert limit is None and set(clip.rotations) == {"spin_wheel_l", "spin_wheel_r"}
    a.clips = [clip]
    checks = by_name(cad.evaluate(report, a, plan(), ["body", "wheel_l", "wheel_r"], clips))
    assert all(c.passed for c in checks.values()), [c for c in checks.values() if not c.passed]
    assert "unbounded" in checks["roll.anim.joint_limits"].detail

    report, meshes = cart_output(motion="oscillate")
    clips, _ = cad.kinematic_clips(report, [ROLL])
    clip, _, limit = clips[0]
    assert limit == pytest.approx(np.radians(24) + 1e-3)  # 80% of the ±30° range
    first = next(iter(clip.rotations.values()))[0]
    assert np.allclose(first, [0, 0, 0, 1])  # starts at the rest pose
    a = cad.build_asset(report, meshes)
    assert all(c.passed for c in cad.evaluate(report, a, plan(), [], clips))


@pytest.mark.parametrize(("rest", "sweep", "failed"), [
    ([{"a": "body", "b": "wheel_l", "frac": 0.03, "link": "revolute"}], [], None),        # axle in its hub: fine
    ([{"a": "body", "b": "wheel_l", "frac": 0.03, "link": ""}], [], "cad.interference"),  # unjoined: 1% max
    ([{"a": "body", "b": "wheel_l", "frac": 0.6, "link": "fastened"}], [], None),         # one rigid body
    ([], [{"joint": "spin_wheel_l", "value": 90.0, "a": "wheel_l", "b": "wheel_r", "frac": 0.2, "link": ""}],
     "cad.joint_sweep"),
])
def test_overlap_tolerances(rest, sweep, failed):
    report, _ = cart_output(rest, sweep)
    checks = by_name(cad.evaluate_report(report, ["body", "wheel_l", "wheel_r"]))
    bad = [n for n, c in checks.items() if not c.passed]
    assert bad == ([failed] if failed else [])
    if failed == "cad.joint_sweep":
        assert "spin_wheel_l=90" in checks[failed].detail


def test_part_list_must_match_the_design():
    report, _ = cart_output()
    assert not by_name(cad.evaluate_report(report, ["body", "wheel"]))["cad.parts"].passed


# --- activities -----------------------------------------------------------------------------------------

class FakeLLM:
    """Writer returns scripted code; the vision critic approves; the planner routes the cart to CAD."""

    def __init__(self):
        self.writes = []

    async def structured(self, role, instructions, input, schema):
        from omnific_common.contracts import AssetPlanOutput, CadWriteOutput

        if schema is AssetPlanOutput:
            return AssetPlanOutput(assets=[plan()])
        assert schema is CadWriteOutput
        self.writes.append(input)
        return CadWriteOutput(code=f"# attempt {len(self.writes)}", parts=["body", "wheel_l", "wheel_r"],
                              notes="box on wheels")

    async def structured_images(self, role, instructions, text, images, schema):
        return schema(matches=True, score=8, defects=[], critique="")


@pytest.fixture
def acts(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    monkeypatch.setattr(activities, "get_store", lambda: store)
    monkeypatch.setattr(activities, "llm", FakeLLM())
    monkeypatch.setenv("MESH_CAD_ENABLED", "1")
    for k in ("MESH_GPU_ENABLED", "MESH_LIBRARY_SOURCES", "RAG_DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    return store


async def test_cad_evaluate_reports_a_failed_run_and_judges_a_good_one(acts):
    store = acts
    failed = CadRunOutput(ok=False, code_key="c.py", stderr="Traceback ...\nNameError: name 'Bx' is not defined")
    out = await activities.cad_evaluate(CadEvaluateInput(job_id="j", plan=plan(), spec=SPEC, run=failed))
    assert not out.eval.passed and "NameError" in out.eval.critique

    store.put("r.zip", zipped(*cart_output()))
    run = CadRunOutput(ok=True, code_key="c.py", result_key="r.zip", step_key="m.step", duration_s=2.0)
    out = await activities.cad_evaluate(CadEvaluateInput(job_id="j", plan=plan(), spec=SPEC, run=run,
                                                         parts=["body", "wheel_l", "wheel_r"]))
    assert out.eval.passed, [c for c in out.eval.checks if not c.passed]
    art = out.artifact
    assert art.source == "cad" and art.step_key == "m.step" and [c.name for c in art.clips] == ["roll"]
    assert art.bones == ["root", "spin_wheel_l", "spin_wheel_r"]
    a = from_glb(store.get(art.glb_key))
    assert [c.name for c in a.clips] == ["roll"]
    # postprocess keeps the rig while normalising size (authored model: no floater removal)
    post = await activities.postprocess_mesh(MeshStageInput(job_id="j", plan=plan(), spec=SPEC, artifact=art))
    assert post.eval.passed, [c for c in post.eval.checks if not c.passed]
    assert post.artifact.bones == art.bones and post.artifact.clips == art.clips


# --- SceneBuild: write -> run -> evaluate, with retries and fallback ---------------------------------------

async def run_scene(store, results: list[bytes | None],
                    generated: bytes | None = None) -> tuple[SceneOutput, list[CadRunInput]]:
    """`results`: what the fake CAD sandbox returns per attempt (None = the script failed). `generated`: the GLB the
    fake GPU worker returns (None = it has no generator)."""
    from omnific_common.temporal import workflow_runner
    from omnific_workflows.scene import SceneBuild
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    runs: list[CadRunInput] = []

    @activity.defn(name=CAD_RUN_ACTIVITY)
    async def fake_run_cad(inp: CadRunInput) -> CadRunOutput:
        runs.append(inp)
        data = results[min(inp.attempt, len(results) - 1)]
        if data is None:
            return CadRunOutput(ok=False, code_key="c.py", stderr="ValueError: bad fillet")
        key = f"jobs/{inp.job_id}/cad-{inp.attempt}.zip"
        store.put(key, data)
        return CadRunOutput(ok=True, code_key="c.py", result_key=key, duration_s=1.0)

    @activity.defn(name=SceneActivity.GENERATE.value)
    async def fake_generate(inp: MeshStageInput) -> MeshArtifact:
        if generated is None:
            raise ApplicationError("no generator", type="StageUnavailable", non_retryable=True)
        key = f"jobs/{inp.job_id}/gen-{inp.attempt}.glb"
        store.put(key, generated)
        return MeshArtifact(entity=inp.plan.entity, source="generated", asset="box", glb_key=key)

    try:
        env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot start Temporal test server: {exc}")
    async with env, \
            Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[SceneBuild],
                   workflow_runner=workflow_runner()), \
            Worker(env.client, task_queue=TaskQueue.MESH.value, activities=activities.SCENE_ACTIVITIES), \
            Worker(env.client, task_queue=TaskQueue.SANDBOX.value, activities=[fake_run_cad]), \
            Worker(env.client, task_queue=TaskQueue.MESH_GPU.value, activities=[fake_generate]):
        out = await env.client.execute_workflow(
            "SceneBuild", SceneBuildInput(job_id=uuid.uuid4().hex[:8], prompt="a cart rolls", spec=SPEC,
                                          backend="generated", max_attempts=2),
            id=uuid.uuid4().hex, task_queue=TaskQueue.WORKFLOW.value, result_type=SceneOutput)
    return out, runs


async def test_scene_build_retries_cad_with_the_failure_then_succeeds(acts):
    store = acts
    out, runs = await run_scene(store, [None, zipped(*cart_output())])
    cart = out.report.entities[0]
    assert cart.final == "cad_rigged" and cart.routes_tried == ["cad"] and cart.route_used == "cad"
    assert [r.code for r in runs] == ["# attempt 1", "# attempt 2"]
    second_request = activities.llm.writes[1]
    assert "bad fillet" in second_request and "# attempt 1" in second_request  # critique + previous code
    gltf = json.loads(store.get(out.scene_key))
    node = next(n for n in gltf["nodes"] if n["name"] == "cart")
    assert node["extras"]["omnific"]["source"] == "cad"
    assert [a["name"] for a in gltf["animations"]] == ["cart:roll"]


async def test_scene_build_falls_back_when_cad_never_passes(acts):
    store = acts
    bad = zipped(*cart_output([{"a": "body", "b": "wheel_l", "frac": 0.5, "link": ""}]))
    out, runs = await run_scene(store, [bad])
    cart = out.report.entities[0]
    assert cart.final == "procedural" and cart.routes_tried == ["cad", "procedural"] and len(runs) == 2
    assert any("CAD model never passed" in n for n in cart.notes)
    assert all(not e.passed for e in cart.evals if e.stage == "cad")


# --- CAD + generation: exact joints from CAD, surface from the generator ------------------------------------

def generated_cart(turns: int = 1, wheels: bool = True) -> Asset:
    """What a generator might return for the cart: same shape, finer mesh, turned, at its own scale, in its own colour."""
    shapes = [trimesh.creation.box((0.4, 0.12, 0.6)).apply_translation((0, 0.2, 0))]
    for x, z in ((0.25, 0.28), (-0.25, -0.28)) if not wheels else ():  # same envelope, nothing where the wheels are
        shapes.append(trimesh.creation.box((0.04, 0.16, 0.04)).apply_translation((x, 0.08, z)))
    for xyz in WHEELS.values() if wheels else ():
        cyl = trimesh.creation.cylinder(radius=0.08, height=0.04, sections=32)
        cyl.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (0, 1, 0)))
        shapes.append(cyl.apply_translation(xyz))
    m = trimesh.util.concatenate(shapes).subdivide().subdivide()
    m.apply_transform(trimesh.transformations.rotation_matrix(turns * np.pi / 2, (0, 1, 0)))
    m.apply_scale(3.0)
    return Asset(positions=m.vertices.astype(np.float32), indices=m.faces.astype(np.uint32),
                 colors=np.tile(np.array([0.1, 0.5, 0.9], np.float32), (len(m.vertices), 1)))


def test_skin_fits_the_surface_and_keeps_the_cad_mechanism():
    report, meshes = cart_output()
    model = cad.build_asset(report, meshes)
    model.clips = [c for c, _, _ in cad.kinematic_clips(report, [ROLL])[0]]
    gen = generated_cart()
    out, checks = cad_skin.apply_skin(gen, model)
    assert all(c.passed for c in checks), [c for c in checks if not c.passed]
    # the mechanism is CAD's, untouched: same bones at the same joints, same clips
    assert [b.name for b in out.bones] == [b.name for b in model.bones]
    np.testing.assert_array_equal(out.bones[1].head, model.bones[1].head)
    assert [c.name for c in out.clips] == ["roll"]
    # the surface is the generator's, on the CAD envelope
    assert out.n_tris == gen.n_tris
    assert np.allclose(out.colors, [0.1, 0.5, 0.9])
    for got, want in zip(out.bbox(), model.bbox(), strict=True):
        np.testing.assert_allclose(got, want, atol=1e-5)
    # every triangle is rigid on one bone: posing a wheel stretches nothing
    assert (out.weights[:, 0] == 1).all()
    tri_bones = out.joints[out.indices, 0]
    assert (tri_bones == tri_bones[:, :1]).all()
    wheel = tri_bones[:, 0] == 1
    centres = out.positions[out.indices[wheel]].mean(axis=1)
    assert np.linalg.norm(centres - np.array(WHEELS["wheel_l"]), axis=1).max() < 0.12


def test_skin_rejects_a_surface_that_misses_a_moving_part():
    model = cad.build_asset(*cart_output())
    _, checks = cad_skin.apply_skin(generated_cart(wheels=False), model)
    assert not by_name(checks)["cad_skin.moving_parts"].passed


def test_skin_rejects_an_empty_surface_without_raising():
    model = cad.build_asset(*cart_output())
    gen = generated_cart(turns=0)
    gen.positions = np.zeros((0, 3), np.float32)
    gen.indices = np.zeros((0, 3), np.uint32)
    gen.normals = gen.uvs = gen.colors = None
    out, checks = cad_skin.apply_skin(gen, model)
    assert out is None and not by_name(checks)["cad_skin.fit"].passed


def test_skin_rejects_wrong_proportions():
    model = cad.build_asset(*cart_output())
    gen = generated_cart(turns=0)
    gen.positions = gen.positions * np.array([1.0, 6.0, 1.0], np.float32)
    out, checks = cad_skin.apply_skin(gen, model)
    assert out is None and not by_name(checks)["cad_skin.proportions"].passed


async def test_scene_build_uses_both_cad_and_generation(acts, monkeypatch):
    monkeypatch.setenv("MESH_GPU_ENABLED", "1")
    store = acts
    out, _ = await run_scene(store, [zipped(*cart_output())], generated=to_glb(generated_cart(), "cart"))
    cart = out.report.entities[0]
    assert cart.route_used == "cad" and cart.final == "cad_rigged" and cart.routes_tried == ["cad"]
    assert [e.passed for e in cart.evals if e.stage == "cad_skin"] == [True]
    assert any("generated surface fitted" in n for n in cart.notes)
    a = from_glb(store.get(f"{activities.cache_key(plan())}/asset.glb"))
    assert [b.name for b in a.bones] == ["root", "spin_wheel_l", "spin_wheel_r"]
    assert [c.name for c in a.clips] == ["roll"]
    assert np.allclose(a.colors[0], [0.1, 0.5, 0.9], atol=1e-3)
    assert [x["name"] for x in json.loads(store.get(out.scene_key))["animations"]] == ["cart:roll"]


@pytest.mark.parametrize("generated", [None, "bad-fit"])
async def test_scene_build_keeps_the_cad_model_when_the_surface_fails(acts, monkeypatch, generated):
    monkeypatch.setenv("MESH_GPU_ENABLED", "1")
    store = acts
    glb = to_glb(generated_cart(wheels=False), "cart") if generated else None
    out, _ = await run_scene(store, [zipped(*cart_output())], generated=glb)
    cart = out.report.entities[0]
    assert cart.route_used == "cad" and cart.final == "cad_rigged"
    assert any("keeping the plain CAD surface" in n for n in cart.notes)
    a = from_glb(store.get(f"{activities.cache_key(plan())}/asset.glb"))
    assert np.allclose(a.colors[0], cad.hex_rgb("#c0392b"), atol=1e-3)


def test_cache_key_separates_plain_and_skinned_cad(monkeypatch):
    monkeypatch.delenv("MESH_GPU_ENABLED", raising=False)
    plain = activities.cache_key(plan())
    monkeypatch.setenv("MESH_GPU_ENABLED", "1")
    assert activities.cache_key(plan()) != plain


# --- integration with the real CAD runner -------------------------------------------------------------

async def test_real_cad_runner_output_passes_the_checks(acts, tmp_path):
    try:
        import docker

        docker.from_env().images.get("omnific-cad-runner:latest")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"CAD runner image unavailable: {e}")
    from omnific_sandbox.backends import CAD_RESULT, DockerBackend

    fixture = Path(__file__).parents[3] / "sandbox" / "tests" / "cad" / "cart.py"
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    shutil.copy(fixture, tmp_path / "in" / "cad.py")
    res = DockerBackend("omnific-cad-runner:latest", result=CAD_RESULT).run(tmp_path / "in", tmp_path / "out", 180)
    assert res.ok, res.stderr
    acts.put("real.zip", res.result_path.read_bytes())
    run = CadRunOutput(ok=True, code_key="c.py", result_key="real.zip", duration_s=res.duration_s)
    out = await activities.cad_evaluate(CadEvaluateInput(
        job_id="j", plan=plan(), spec=SPEC, run=run, parts=["body", "wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr"]))
    assert out.eval.passed, [c for c in out.eval.checks if not c.passed]
    assert len(out.artifact.bones) == 5 and [c.name for c in out.artifact.clips] == ["roll"]


def test_a_plain_cad_box_is_not_rejected_for_having_twelve_triangles():
    """Regression: a 1 m ledge (a box, 12 triangles) failed 'mesh.non_empty (min 100)' in a real job."""
    from omnific_mesh import meshops

    m = trimesh.creation.box(extents=[2.0, 1.0, 1.0])
    box = Asset(positions=m.vertices.astype(np.float32), indices=m.faces.astype(np.uint32))
    assert box.n_tris == 12
    assert by_name(meshops.evaluate_generated(box, min_tris=meshops.CAD_MIN_TRIS))["mesh.non_empty"].passed
    assert not by_name(meshops.evaluate_generated(box))["mesh.non_empty"].passed  # ML meshes keep the higher bar
