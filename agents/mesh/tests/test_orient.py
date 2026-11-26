"""Which way models face: the canonical turn (front +X, top +Y), the labelled axis views, and aiming one entity at
another in the assembled scene (the rifle that lay sideways instead of pointing at its barrel)."""

import itertools
import math

import numpy as np
import pytest
from omnific_common.contracts import MeshArtifact
from omnific_mesh import orient
from omnific_mesh.asset import Asset, to_glb
from omnific_mesh.assets import GENERATORS
from omnific_mesh.scene import aim, assemble
from omnific_sim.spec import Domain, Entity, EntityGeometry, Fidelity, SimSpec, TimeSpan


def rotate(q, v):
    x, y, z, w = q
    m = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    return m @ np.asarray(v, float)


PAIRS = [(f, u) for f, u in itertools.product(orient.AXES, orient.AXES) if abs(orient.AXES[f] @ orient.AXES[u]) < 0.5]


@pytest.mark.parametrize("front,up", PAIRS)
def test_canonical_turn_maps_front_to_x_and_top_to_y(front, up):
    q = orient.canonical_turn(front, up)
    assert q is not None
    q = q or [0.0, 0.0, 0.0, 1.0]
    assert np.linalg.norm(q) == pytest.approx(1.0)
    np.testing.assert_allclose(rotate(q, orient.AXES[front]), [1, 0, 0], atol=1e-9)
    np.testing.assert_allclose(rotate(q, orient.AXES[up]), [0, 1, 0], atol=1e-9)


@pytest.mark.parametrize("up", list(orient.AXES))
def test_a_thing_with_no_front_is_only_stood_up(up):
    q = orient.canonical_turn("none", up) or [0.0, 0.0, 0.0, 1.0]
    np.testing.assert_allclose(rotate(q, orient.AXES[up]), [0, 1, 0], atol=1e-9)


def test_inconsistent_answers_and_no_turn():
    assert orient.canonical_turn("+x", "-x") is None
    assert orient.canonical_turn("+x", "+y") == []
    assert orient.canonical_turn("none", "+y") == []


def test_the_views_are_labelled_with_the_true_axes():
    assert orient._edge_labels(0, 0) == {"right": "+x", "left": "-x", "top": "+y", "bottom": "-y",
                                         "toward you": "+z"}
    top = orient._edge_labels(0, 90)
    assert top["toward you"] == "+y" and top["right"] == "+x" and top["top"] == "-z"
    side = orient._edge_labels(90, 0)
    assert side["top"] == "+y" and {side["right"], side["left"]} == {"+z", "-z"}
    assert side["toward you"] in ("+x", "-x")


def rifle() -> Asset:
    """A long thin box along z with a stub on top: a stand-in for a model that lies along the wrong axis."""
    pos, nrm, idx = GENERATORS["box"]()
    return Asset(positions=(pos * np.array([0.1, 0.2, 1.0])).astype(np.float32), normals=nrm, indices=idx.reshape(-1, 3),
                 base_color=(0.3, 0.3, 0.3, 1.0))


class FacingLLM:
    def __init__(self, front, up):
        self.front, self.up, self.seen = front, up, []

    async def structured_images(self, role, instructions, text, images, schema):
        self.seen.append((text, images))
        return schema(front=self.front, up=self.up, reason="the muzzle is the thin end")


async def test_facing_turns_the_model_and_reports_why():
    llm = FacingLLM("-z", "+y")
    turn, check = await orient.facing(llm, rifle(), "rifle: an AK-47")
    assert check.passed and "-z -> +x" in check.detail and "muzzle" in check.detail
    np.testing.assert_allclose(rotate(turn, [0, 0, -1]), [1, 0, 0], atol=1e-9)
    assert llm.seen[0][1][0][1] == "image/png" and "AK-47" in llm.seen[0][0]


async def test_facing_fails_open():
    class Broken:
        async def structured_images(self, *a):
            raise RuntimeError("no network")

    turn, check = await orient.facing(Broken(), rifle(), "rifle")
    assert turn == [] and check.passed and "kept as authored" in check.detail
    turn, check = await orient.facing(FacingLLM("+y", "-y"), rifle(), "rifle")
    assert turn == [] and "inconsistent" in check.detail


def test_aim_points_the_front_at_the_target():
    for src, dst in [([0, 0, 0], [3, 0, 0]), ([0, 0, 0], [0, 0, -2]), ([1, 1, 6], [1, 3, 0]), ([0, 0, 0], [-1, 2, 1])]:
        q = aim(src, dst)
        d = np.subtract(dst, src) / np.linalg.norm(np.subtract(dst, src))
        np.testing.assert_allclose(rotate(q, [1, 0, 0]), d, atol=1e-9)
        # a level aim is a pure yaw (no roll about the barrel)
        np.testing.assert_allclose(rotate(q, [0, 1, 0])[1], math.cos(math.asin(d[1])), atol=1e-9)
    assert aim([1, 2, 3], [1, 2, 3]) is None


def spec_with(entities):
    return SimSpec(title="t", prompt="p", domains=[Domain.OTHER], fidelity=Fidelity.HEURISTIC, assumptions=[],
                   parameters=[], state=[], equations=[], entities=entities, time=TimeSpan(end=1.0))


def test_a_rifle_faces_its_barrel_in_the_scene():
    gun = Entity(name="AK-47 prop", kind="rifle", faces="Left barrel",
                 geometry=EntityGeometry(shape="box", size=[0.9, 0.2, 0.08], position=[-1.5, 0.25, 1.7]))
    barrel = Entity(name="Left barrel", kind="barrel",
                    geometry=EntityGeometry(shape="cylinder", size=[0.6, 0.9, 0.6], position=[-1.5, 0.8, 0.0]))
    art = MeshArtifact(entity="AK-47 prop", source="library", asset="box", glb_key="gun.glb",
                       orientation=orient.canonical_turn("-z", "+y"))
    glb = to_glb(rifle())
    gltf, _ = assemble(spec_with([gun, barrel]), [art], lambda key: glb, 2.0)
    nodes = {n["name"]: n for n in gltf["nodes"]}
    root, mesh = nodes["AK-47 prop"], nodes["mesh:AK-47 prop"]
    assert mesh["rotation"] == art.orientation
    # model front (-z) -> +x by its mesh node, then +x -> toward the barrel by the root
    d = np.subtract(nodes["Left barrel"]["translation"], root["translation"])
    front = rotate(root["rotation"], rotate(mesh["rotation"], [0, 0, -1]))
    np.testing.assert_allclose(front, d / np.linalg.norm(d), atol=1e-6)
    assert "rotation" not in nodes["Left barrel"]


def test_facing_needs_both_places_and_ignores_itself():
    a = Entity(name="cam", kind="camera", faces="ball", geometry=EntityGeometry(shape="box", size=[0.2, 0.2, 0.2],
                                                                               position=[0, 0, 0]))
    b = Entity(name="ball", kind="sphere", bound_state=["x"])  # no known place: the camera is left as it is
    gltf, _ = assemble(spec_with([a, b]), [], lambda key: b"", 2.0)
    assert "rotation" not in next(n for n in gltf["nodes"] if n["name"] == "cam")
    selfie = a.model_copy(update={"faces": "cam"})
    gltf, _ = assemble(spec_with([selfie]), [], lambda key: b"", 2.0)
    assert "rotation" not in gltf["nodes"][0]
