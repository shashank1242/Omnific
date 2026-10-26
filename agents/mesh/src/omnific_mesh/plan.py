"""Asset planning and mesh routing: for each entity, an ordered list of ways to make its asset (library model, CAD,
3D generation, procedural shape), plus rig and clips. Deterministic for the procedural backend; an LLM decision
(then validated and repaired here, against what this deployment can run) for the generated backend."""

from __future__ import annotations

import os

from omnific_common.contracts import (
    AssetPlan,
    AssetPlanOutput,
    CheckResult,
    MeshCapabilities,
    MeshRoute,
    StageEval,
)
from omnific_common.llm import LLM, Role
from omnific_sim.spec import SimSpec

from .assets import asset_for_kind

# Assets that look right as primitives: generating a "ground plane" or "particle" adds nothing but cost.
ALWAYS_PROCEDURAL = {"plane"}
MIN_BUDGET, MAX_BUDGET, DEFAULT_BUDGET = 500, 100_000, 20_000
ROUTES: tuple[MeshRoute, ...] = ("library", "cad", "generated", "procedural")

PLAN_INSTRUCTIONS = """\
You plan the 3D assets for a physics simulation viewer and route each entity to the technology that will make it
best. For each entity decide:
- routes: the ways to make it, best first; each is tried until one passes its quality checks. Use only routes listed
  as available. Options:
  - "library": reuse an existing, licensed 3D model from an online library. Best for detailed real-world objects that
    people have already modelled and that are hard to make well: cars, aircraft, engines, furniture, tools, animals,
    characters.
  - "cad": a parametric CAD model written as code. Best for SIMPLE mechanisms whose geometry and joints must be exact:
    a robot arm, pendulum, piston, crank, gear pair, wheel on an axle, bracket, lever. Not for organic shapes or
    complex assemblies.
  - "generated": an image-to-3D model. Best for organic or unusual objects nobody has modelled ("a six-legged robot
    dog", "a cartoon planet").
  - "procedural": a clean primitive. Best for abstract things (ground, wall, generic particle, plain ball/box, rod,
    sheet); it is always the final fallback.
  Order matters: e.g. a car -> ["library", "generated", "procedural"]; a two-link robot arm -> ["cad", "library",
  "procedural"]; a dog -> ["library", "generated", "procedural"]; a plain ball -> ["procedural"].
- route_reason: one sentence explaining the order.
- search_query (library route): 2-5 plain search words for a model library (e.g. "red sports car"); else "".
- cad_brief (cad route): the parts, key dimensions in metres (use the spec's lengths) and joints, e.g. "base 0.2 m
  cube; upper arm 0.5 m long, revolute at the base top about z; forearm 0.4 m, revolute at the elbow about z"; else "".
- gen_prompt: a concrete visual description of ONE object: shape, materials, colours, notable parts; neutral pose,
  no background, no text. Used to generate it and to judge library candidates. Empty only for procedural-only.
- rig: "physics" if the entity swings about a pivot (it has an arm); "ml" if it is a body with moving parts that
  should visibly move (legs, wings, tail, arms); "both" if both; otherwise "none".
- clips (only with rig "ml" or "both", or a cad route with moving parts): secondary motion such as "idle"
  breathing, "locomotion" gait, "flex" bending. drive_state is a state variable whose magnitude sets the clip speed
  (e.g. a speed state for a walk), or "". The solver moves the entity; clips must not translate it.
- poly_budget: triangles after decimation, 2000-50000 (more for large hero objects, fewer for small/many).
Plan every entity exactly once, using the entity names given."""


def capabilities_from_env() -> MeshCapabilities:
    """What this deployment serves. MESH_GPU_ENABLED: a mesh-gpu worker runs (else a generated route would wait
    gpu_wait_minutes for nobody); MESH_CAD_ENABLED: the CAD sandbox runner exists; MESH_LIBRARY_SOURCES: enabled
    model libraries (each also needs its credentials, checked by the library module)."""
    on = {"1", "true", "yes", "on"}
    from .library import available_sources

    sources = available_sources([s.strip() for s in os.environ.get("MESH_LIBRARY_SOURCES", "").split(",")
                                 if s.strip()])
    return MeshCapabilities(gpu=os.environ.get("MESH_GPU_ENABLED", "").lower() in on,
                            cad=os.environ.get("MESH_CAD_ENABLED", "").lower() in on,
                            library_sources=sources)


def available_routes(caps: MeshCapabilities) -> list[MeshRoute]:
    out: list[MeshRoute] = []
    if caps.library_sources:
        out.append("library")
    if caps.cad:
        out.append("cad")
    if caps.gpu:
        out.append("generated")
    return [*out, "procedural"]


def default_plan(spec: SimSpec) -> list[AssetPlan]:
    return [AssetPlan(entity=e.name, kind=e.kind, routes=["procedural"], route_reason="procedural backend",
                      search_query="", cad_brief="", gen_prompt="",
                      rig="physics" if e.arm is not None else "none", clips=[], poly_budget=0)
            for e in spec.entities]


def render_entities(spec: SimSpec, prompt: str, caps: MeshCapabilities, stats: str = "") -> str:
    states = ", ".join(s.name for s in spec.state)
    params = ", ".join(f"{p.name}={p.value:g} {p.unit}" for p in spec.parameters)
    lines = [f"- {e.name}: kind={e.kind!r}, bound_state={e.bound_state}"
             + (f", swings about a pivot (arm {e.arm})" if e.arm is not None else "")
             + (f", hangs from {e.parent}" if e.parent else "") for e in spec.entities]
    text = (f"Simulation prompt:\n{prompt}\n\nTitle: {spec.title}\nState variables: {states}\n"
            f"Parameters: {params or 'none'}\n\nEntities:\n" + "\n".join(lines)
            + f"\n\nAvailable routes: {available_routes(caps)}")
    if stats:
        text += f"\n\nPast results per kind (route: passed/tried) -- prefer routes that worked:\n{stats}"
    return text


def _routes(p: AssetPlan, kind_asset: str, articulated: bool, allowed: list[MeshRoute],
            notes: list[str]) -> list[MeshRoute]:
    if kind_asset in ALWAYS_PROCEDURAL:
        if p.routes != ["procedural"]:
            notes.append("ground/plane kept procedural")
        return ["procedural"]
    routes: list[MeshRoute] = []
    for r in p.routes:
        if r in routes:
            continue
        if r not in allowed:
            notes.append(f"route {r!r} unavailable here")
            continue
        if r == "library" and not p.search_query.strip():
            notes.append("library route without search_query dropped")
            continue
        if r == "cad" and not p.cad_brief.strip():
            notes.append("cad route without cad_brief dropped")
            continue
        if r == "generated" and not p.gen_prompt.strip():
            notes.append("generated route without gen_prompt dropped")
            continue
        routes.append(r)
        if r == "procedural":
            break  # terminal: nothing after it is ever tried
    if articulated and "cad" in routes and "generated" in routes and routes.index("generated") < routes.index("cad"):
        routes.remove("cad")
        routes.insert(routes.index("generated"), "cad")
        notes.append("articulated entity: cad (exact joints) moved ahead of generated")
    if routes[-1:] != ["procedural"]:
        routes.append("procedural")
    return routes


def repair(spec: SimSpec, plans: list[AssetPlan],
           caps: MeshCapabilities | None = None) -> tuple[list[AssetPlan], list[CheckResult]]:
    """Validate an LLM plan, fixing what can be fixed deterministically. Every fix is reported as a failed check
    (so the critique can mention it) while the returned plan is always usable."""
    allowed = available_routes(caps or MeshCapabilities())
    checks: list[CheckResult] = []
    by_name = {p.entity: p for p in plans}
    states = {s.name for s in spec.state}
    names = [e.name for e in spec.entities]
    missing = [n for n in names if n not in by_name]
    extra = [p.entity for p in plans if p.entity not in names]
    dup = len(plans) - len(by_name)
    checks.append(CheckResult(name="plan.coverage", passed=not (missing or extra or dup),
                              detail=f"missing {missing}, unknown {extra}, duplicates {dup}"
                              if missing or extra or dup else f"all {len(names)} entities planned once"))
    fixed: list[AssetPlan] = []
    defaults = {p.entity: p for p in default_plan(spec)}
    for e in spec.entities:
        p = by_name.get(e.name) or defaults[e.name]
        notes: list[str] = []
        routes = _routes(p, asset_for_kind(e.kind), e.arm is not None, allowed, notes)
        upd: dict = {"kind": e.kind, "routes": routes}
        if routes == ["procedural"]:
            upd.update(gen_prompt="", search_query="", cad_brief="")
        rig = p.rig
        if e.arm is not None and rig in ("none", "ml"):
            rig = "physics" if rig == "none" else "both"
            notes.append("entity swings about a pivot: physics rig added")
        if e.arm is None and rig in ("physics", "both"):
            rig = "none" if rig == "physics" else "ml"
            notes.append("no arm in the spec: physics rig removed")
        if rig in ("ml", "both") and not {"generated", "library"} & set(routes):
            rig = "physics" if rig == "both" else "none"
            notes.append("ML rig needs a generated or library route")
        upd["rig"] = rig
        clips = p.clips if rig in ("ml", "both") or "cad" in routes else []
        if len(clips) != len(p.clips):
            notes.append("clips dropped: they need an ML rig or a CAD model")
        bad_drive = [c.name for c in clips if c.drive_state and c.drive_state not in states]
        if bad_drive:
            clips = [c.model_copy(update={"drive_state": ""}) if c.drive_state not in states else c for c in clips]
            notes.append(f"unknown drive_state cleared on {bad_drive}")
        names_seen: set[str] = set()
        clips = [c for c in clips if not (c.name in names_seen or names_seen.add(c.name))]
        upd["clips"] = clips
        made = routes != ["procedural"]
        budget = p.poly_budget if made else 0
        if made and not (MIN_BUDGET <= budget <= MAX_BUDGET):
            budget = DEFAULT_BUDGET
            notes.append(f"poly_budget {p.poly_budget} out of range -> {DEFAULT_BUDGET}")
        upd["poly_budget"] = budget
        fixed.append(p.model_copy(update=upd))
        checks.append(CheckResult(name=f"plan[{e.name}]", passed=not notes,
                                  detail="; ".join(notes) or f"routes {routes}, rig {rig}, {len(clips)} clips"))
    return fixed, checks


async def plan_assets(llm: LLM, spec: SimSpec, prompt: str, backend: str, caps: MeshCapabilities | None = None,
                      stats: str = "") -> tuple[list[AssetPlan], StageEval]:
    caps = caps or MeshCapabilities()
    if backend != "generated" or not spec.entities or available_routes(caps) == ["procedural"]:
        plans = default_plan(spec)
        why = "procedural backend" if backend != "generated" else "no mesh route besides procedural is available"
        return plans, StageEval(stage="plan", attempt=0, passed=True, checks=[
            CheckResult(name="plan.coverage", passed=True, detail=f"{len(plans)} entities, {why}")])
    try:
        out = await llm.structured(Role.GENERAL, PLAN_INSTRUCTIONS, render_entities(spec, prompt, caps, stats),
                                   AssetPlanOutput)
        plans, checks = repair(spec, out.assets, caps)
        critique = "; ".join(c.detail for c in checks if not c.passed)
    except Exception as e:  # noqa: BLE001 - planning failure means procedural, never a failed job
        plans = default_plan(spec)
        checks = [CheckResult(name="plan.llm", passed=False, detail=f"planner failed ({type(e).__name__}): procedural")]
        critique = checks[0].detail
    # the returned plan is always valid after repair; `passed` reports whether repair was needed
    return plans, StageEval(stage="plan", attempt=0, passed=all(c.passed for c in checks), checks=checks,
                            critique=critique)
