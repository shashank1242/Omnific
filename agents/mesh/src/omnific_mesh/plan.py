"""Asset planning: which entities get generated meshes, rigs and clips. Deterministic for the procedural backend,
an LLM decision (then validated and repaired here) for the generated backend."""

from __future__ import annotations

from omnific_common.contracts import AssetPlan, AssetPlanOutput, CheckResult, StageEval
from omnific_common.llm import LLM, Role
from omnific_sim.spec import SimSpec

from .assets import asset_for_kind

# Assets that look right as primitives: generating a "ground plane" or "particle" adds nothing but cost.
ALWAYS_PROCEDURAL = {"plane"}
MIN_BUDGET, MAX_BUDGET, DEFAULT_BUDGET = 500, 100_000, 20_000

PLAN_INSTRUCTIONS = """\
You plan the 3D assets for a physics simulation viewer. For each entity decide:
- mode: "generated" when a real-looking object helps (an animal, a vehicle, a pizza, a planet, a pendulum bob shaped
  like something specific); "procedural" for abstract primitives (ground, wall, generic particle, plain ball/box,
  rod, sheet) where a clean primitive reads better.
- gen_prompt (generated only): a concrete visual description of ONE object for an image-to-3D model: shape,
  materials, colours, notable parts; neutral pose, no background, no text. Empty for procedural.
- rig: "physics" if the entity swings about a pivot (it has an arm); "ml" if it is a body with moving parts that
  should visibly move (legs, wings, tail, arms, a robot's joints); "both" if both; otherwise "none".
- clips (only with rig "ml" or "both"): secondary motion such as "idle" breathing, "locomotion" gait, "flex" bending.
  drive_state is a state variable whose magnitude sets the clip speed (e.g. a speed state for a walk), or "".
  The solver moves the entity; clips must not translate it.
- poly_budget: triangles after decimation, 2000-50000 (more for large hero objects, fewer for small/many).
Plan every entity exactly once, using the entity names given."""


def default_plan(spec: SimSpec) -> list[AssetPlan]:
    return [AssetPlan(entity=e.name, kind=e.kind, mode="procedural", gen_prompt="",
                      rig="physics" if e.arm is not None else "none", clips=[], poly_budget=0)
            for e in spec.entities]


def render_entities(spec: SimSpec, prompt: str) -> str:
    states = ", ".join(s.name for s in spec.state)
    lines = [f"- {e.name}: kind={e.kind!r}, bound_state={e.bound_state}"
             + (f", swings about a pivot (arm {e.arm})" if e.arm is not None else "") for e in spec.entities]
    return (f"Simulation prompt:\n{prompt}\n\nTitle: {spec.title}\nState variables: {states}\n\nEntities:\n"
            + "\n".join(lines))


def repair(spec: SimSpec, plans: list[AssetPlan]) -> tuple[list[AssetPlan], list[CheckResult]]:
    """Validate an LLM plan, fixing what can be fixed deterministically. Every fix is reported as a failed check
    (so the critique can mention it) while the returned plan is always usable."""
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
        notes = []
        upd: dict = {"kind": e.kind}
        if asset_for_kind(e.kind) in ALWAYS_PROCEDURAL and p.mode == "generated":
            upd["mode"], upd["gen_prompt"] = "procedural", ""
            notes.append("ground/plane kept procedural")
        mode = upd.get("mode", p.mode)
        if mode == "generated" and not p.gen_prompt.strip():
            upd["mode"] = mode = "procedural"
            notes.append("generated without gen_prompt -> procedural")
        rig = p.rig
        if e.arm is not None and rig in ("none", "ml"):
            rig = "physics" if rig == "none" else "both"
            notes.append("entity swings about a pivot: physics rig added")
        if e.arm is None and rig in ("physics", "both"):
            rig = "none" if rig == "physics" else "ml"
            notes.append("no arm in the spec: physics rig removed")
        if mode == "procedural" and rig in ("ml", "both"):
            rig = "physics" if rig == "both" else "none"
            notes.append("procedural assets can't take an ML rig")
        upd["rig"] = rig
        clips = p.clips if rig in ("ml", "both") else []
        if len(clips) != len(p.clips):
            notes.append("clips dropped: they need an ML rig")
        bad_drive = [c.name for c in clips if c.drive_state and c.drive_state not in states]
        if bad_drive:
            clips = [c.model_copy(update={"drive_state": ""}) if c.drive_state not in states else c for c in clips]
            notes.append(f"unknown drive_state cleared on {bad_drive}")
        names_seen: set[str] = set()
        clips = [c for c in clips if not (c.name in names_seen or names_seen.add(c.name))]
        upd["clips"] = clips
        budget = p.poly_budget if mode == "generated" else 0
        if mode == "generated" and not (MIN_BUDGET <= budget <= MAX_BUDGET):
            budget = DEFAULT_BUDGET
            notes.append(f"poly_budget {p.poly_budget} out of range -> {DEFAULT_BUDGET}")
        upd["poly_budget"] = budget
        fixed.append(p.model_copy(update=upd))
        checks.append(CheckResult(name=f"plan[{e.name}]", passed=not notes,
                                  detail="; ".join(notes) or f"{mode}, rig {rig}, {len(clips)} clips"))
    return fixed, checks


async def plan_assets(llm: LLM, spec: SimSpec, prompt: str, backend: str) -> tuple[list[AssetPlan], StageEval]:
    if backend != "generated" or not spec.entities:
        plans = default_plan(spec)
        return plans, StageEval(stage="plan", attempt=0, passed=True, checks=[
            CheckResult(name="plan.coverage", passed=True, detail=f"{len(plans)} entities, procedural backend")])
    try:
        out = await llm.structured(Role.GENERAL, PLAN_INSTRUCTIONS, render_entities(spec, prompt), AssetPlanOutput)
        plans, checks = repair(spec, out.assets)
        critique = "; ".join(c.detail for c in checks if not c.passed)
    except Exception as e:  # noqa: BLE001 - planning failure means procedural, never a failed job
        plans = default_plan(spec)
        checks = [CheckResult(name="plan.llm", passed=False, detail=f"planner failed ({type(e).__name__}): procedural")]
        critique = checks[0].detail
    # the returned plan is always valid after repair; `passed` reports whether repair was needed
    return plans, StageEval(stage="plan", attempt=0, passed=all(c.passed for c in checks), checks=checks,
                            critique=critique)
