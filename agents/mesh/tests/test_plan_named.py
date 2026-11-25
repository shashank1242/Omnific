"""Named real things get a library model first; plain shapes and markers stay simple."""

from omnific_common.contracts import AssetPlan, MeshCapabilities
from omnific_mesh.plan import names_real_thing, repair
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, TimeSpan


def test_which_kinds_name_real_things():
    for kind in ("atomic bomb", "black hole", "light detector", "space probe", "red sports car"):
        assert names_real_thing(kind), kind
    for kind in ("photon marker", "steel ball", "wooden ramp", "point mass", "sphere", "generic particle",
                 "point-like particle", "debris fragment", "oobleck volume", "fluid layer"):
        assert not names_real_thing(kind), kind


def test_a_bomb_downgraded_to_a_marker_gets_its_library_model_back():
    spec = SimSpec(title="t", prompt="p", domains=[Domain.OTHER], fidelity=Fidelity.HEURISTIC, assumptions=[],
                   parameters=[], state=[], equations=[], time=TimeSpan(end=1.0),
                   entities=[Entity(name="Falling source", kind="atomic bomb"),
                             Entity(name="Outward photon", kind="photon marker")])
    def plain(entity, kind):
        return AssetPlan(entity=entity, kind=kind, routes=["procedural"], route_reason="marker", search_query="",
                         cad_brief="", gen_prompt="", rig="none", clips=[], poly_budget=0)

    plans = [plain("Falling source", "atomic bomb"), plain("Outward photon", "photon marker")]
    caps = MeshCapabilities(cad=True, library_sources=["sketchfab"])
    fixed, checks = repair(spec, plans, caps)
    bomb, photon = fixed
    assert bomb.routes[0] == "library" and bomb.routes[-1] == "procedural"
    assert bomb.search_query == "atomic bomb" and "atomic bomb" in bomb.gen_prompt
    assert photon.routes == ["procedural"]
    assert any("names a real thing" in c.detail for c in checks)
    # without a library nothing changes
    fixed, _ = repair(spec, plans, MeshCapabilities(cad=True))
    assert fixed[0].routes == ["procedural"]
