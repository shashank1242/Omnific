"""CAD harness in the real CAD runner image (docker backend): a good mechanism, and each contract violation the
writer must be told about. Skipped without Docker or the image (docker build -f sandbox/cad-runner.Dockerfile
-t omnific-cad-runner:latest .)."""

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from omnific_sandbox.backends import CAD_RESULT, DockerBackend

FIXTURES = Path(__file__).parent / "cad"
IMAGE = "omnific-cad-runner:latest"


@pytest.fixture(scope="module")
def backend():
    try:
        import docker

        docker.from_env().images.get(IMAGE)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"CAD runner image unavailable: {e}")
    return DockerBackend(IMAGE, result=CAD_RESULT)


def run(backend, tmp_path, name):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    shutil.copy(FIXTURES / f"{name}.py", in_dir / "cad.py")
    res = backend.run(in_dir, out_dir, 180)
    report = json.loads(zipfile.ZipFile(res.result_path).read("cad.json")) if res.ok else None
    return res, report


def test_cart_spins_its_wheels_without_collisions(backend, tmp_path):
    res, report = run(backend, tmp_path, "cart")
    assert res.ok, res.stderr
    assert [p["label"] for p in report["parts"]] == ["body", "wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr"]
    assert all(p["valid"] and p["volume"] > 0 and p["n_tris"] > 0 for p in report["parts"])
    assert {j["motion"] for j in report["joints"]} == {"spin"}
    assert report["interference"] == {"rest": [], "sweep": []}
    assert {"cad.json", "parts.npz", "model.step"} <= set(zipfile.ZipFile(res.result_path).namelist())


def test_sweep_finds_a_collision_inside_the_joint_range(backend, tmp_path):
    res, report = run(backend, tmp_path, "collide")
    assert res.ok, res.stderr
    assert report["interference"]["rest"] == []
    hits = report["interference"]["sweep"]
    assert hits and {h["b"] for h in hits} == {"wall"} and all(h["value"] < 0 for h in hits)


def test_rest_overlap_is_reported(backend, tmp_path):
    res, report = run(backend, tmp_path, "overlap")
    assert res.ok, res.stderr
    (hit,) = report["interference"]["rest"]
    assert hit["link"] == "" and hit["frac"] == pytest.approx(0.5, abs=0.01)


@pytest.mark.parametrize(("name", "message"), [("cycle", "tree"), ("broken", "the writer made a mistake")])
def test_contract_violations_fail_with_a_message(backend, tmp_path, name, message):
    res, _ = run(backend, tmp_path, name)
    assert not res.ok and message in res.stderr
