"""The Python port of the viewer's pose math (used by the Blender export) against the fixture vitest also checks."""

import json
import math
from pathlib import Path

import pytest
from omnific_sandbox import playback_math as pm

GOLDEN = Path(__file__).resolve().parents[2] / "apps" / "web" / "lib" / "fixtures" / "playback-golden.json"


@pytest.fixture(scope="module")
def golden():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_cases(golden):
    pb, base = golden["playback"], golden["base"]
    for case in golden["cases"]:
        got = pm.evaluate_poses(pb, case["time"], lambda n: base.get(n))
        assert sorted(got) == sorted(case["poses"])
        for name, want in case["poses"].items():
            g = got[name]
            assert g["position"].keys() == want["position"].keys(), name
            for axis, v in want["position"].items():
                assert g["position"][axis] == pytest.approx(v, abs=1e-12)
            for key in ("rotation_z", "scale"):
                assert (key in g) == (key in want), (name, key)
                if key in want:
                    assert g[key] == pytest.approx(want[key], abs=1e-12)
            if "color" in want:
                assert g["color"] == pytest.approx(want["color"], abs=1e-12)


def test_resolve_pose_keeps_untracked_axes():
    pos, rot, scale = pm.resolve_pose({"position": {"x": 2.0}, "rotation_z": 0.3}, [1, 2, 3], [0.1, 0.2, 0.0])
    assert pos == [2.0, 2, 3]
    assert rot == [0.1, 0.2, 0.3]
    assert scale is None


def test_frame_times_match_viewer_run_length():
    pb = {"t": [2.0, 3.0, 100.0]}
    times = pm.frame_times(pb, 24)
    assert len(times) == pm.RUN_SECONDS * 24 + 1
    assert times[0] == 2.0 and times[-1] == pytest.approx(100.0)
    assert pm.frame_times({"t": [5.0]}, 24) == [5.0]


def test_clip_phase_and_rate():
    pb = {"t": [0.0, 1.0], "series": {"v": {"unit": "m/s", "values": [0.0, -2.0]}}}
    ref = pm.drive_reference(pb, "v")
    assert ref == 2.0
    assert pm.clip_rate(pb, "v", 0.5, ref) == pytest.approx(0.5)
    assert pm.clip_rate(pb, "", 0.5, ref) == 1.0
    assert pm.advance_phase(0.9, 0.3, 1.0, 1.0) == pytest.approx(0.2)
    assert pm.advance_phase(0.1, -0.3, 1.0, 1.0) == pytest.approx(0.8)
    assert pm.advance_phase(0.5, 1.0, 1.0, 0.0) == 0.0


def test_colormap_ends_and_nan():
    assert pm.colormap("viridis", 0.0) == pytest.approx(pm.STOPS["viridis"][0])
    assert pm.colormap("inferno", 1.0) == pytest.approx(pm.STOPS["inferno"][-1])
    assert pm.colormap("viridis", math.nan) == pm.colormap("viridis", 0.0)
    assert pm.colormap_for_unit("kelvin") == "inferno" and pm.colormap_for_unit("meter") == "viridis"
