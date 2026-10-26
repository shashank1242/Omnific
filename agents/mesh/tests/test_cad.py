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
    MeshStageInput,
    SceneBuildInput,
    SceneOutput,
    TaskQueue,
)
from omnific_common.storage import LocalStore
from omnific_mesh import activities, cad
from omnific_mesh.asset import from_glb
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, StateVariable, TimeSpan
from temporalio import activity

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

async def run_scene(store, results: list[bytes | None]) -> tuple[SceneOutput, list[CadRunInput]]:
    """`results`: what the fake CAD sandbox returns per attempt (None = the script failed)."""
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

    try:
        env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot start Temporal test server: {exc}")
    async with env, \
            Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[SceneBuild],
                   workflow_runner=workflow_runner()), \
            Worker(env.client, task_queue=TaskQueue.MESH.value, activities=activities.SCENE_ACTIVITIES), \
            Worker(env.client, task_queue=TaskQueue.SANDBOX.value, activities=[fake_run_cad]):
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
