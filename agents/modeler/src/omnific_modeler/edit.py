"""Follow-up edits: sort a plain-language change to a finished simulation into a re-run plan."""

from __future__ import annotations

from omnific_common.contracts import EDIT_ACTIVITY, EditClassifyInput, EditPlan, VariantChanges
from omnific_common.edits import apply_plan
from omnific_common.llm import Role
from omnific_sim.spec import SimSpec
from temporalio import activity

from . import activities

EDIT_INSTRUCTIONS = """You sort a user's follow-up change to a finished simulation into a re-run plan (EditPlan).
You get the change request, a compact view of the current SimSpec, and possibly the user's answers to your questions.

kind (pick exactly one):
- parameters: ONLY values that already exist in the spec: parameters (by name), initial values of state variables
  (by name), or the end of the time span. Every value MUST be in the unit the spec already uses for that name: convert
  ("30 degrees" for a parameter in radian -> 0.5236; "2 kg" for a parameter in gram -> 2000). Relative requests
  ("twice as heavy", "10% more friction") are computed from the current value shown. "Run it for 20 s" ->
  changes.end = 20 when the time unit is second (convert otherwise: 1/3 for minute) and stop_fixed = true. "Let it run longer" with no number ->
  changes.end = 2x the current end, stop_fixed = false. A parameters plan MAY also carry looks when the change is
  both ("swap the ball for a bowling ball": new mass and radius values plus a look).
- visual: ONLY appearance (colour, material, style, "give it a bowling-ball look"): fill looks with existing entity
  names and a few words each; no physics change at all.
- structural: anything else: new or removed objects, new forces or physics, a different setting or environment
  ("put it on the Moon" is structural: the environment and its gravity change together), changed equations, or a value
  that is not an existing parameter.
- unclear: ONLY when the request cannot be acted on without asking (e.g. "make it heavier" with several objects and no
  amount). Then give 1-3 questions (kind choice, number, boolean or text; sensible default; options for choices; a unit
  for numbers). NEVER unclear when the user's answers are already given: act on them.

summary: one plain sentence for the user saying what this version changes. Never invent changes they did not ask for.
Fields the kind does not use: empty lists, changes with empty lists and end null, stop_fixed false."""


def render_edit_input(inp: EditClassifyInput) -> str:
    spec = inp.spec
    lines = [f"Change request:\n{inp.request}", "", f"Title: {spec.title}", "Parameters:"]
    for p in spec.parameters:
        desc = f" ({p.description})" if p.description else ""
        lines.append(f"- {p.name} = {p.value:g} {p.unit}".rstrip() + desc)
    lines.append("State variables (initial values):")
    for v in spec.state:
        lines.append(f"- {v.name} = {v.initial_value:g} {v.unit}".rstrip())
    lines.append("Entities:")
    for e in spec.entities:
        props = f", its values: {', '.join(e.properties)}" if e.properties else ""
        lines.append(f"- {e.name}: kind={e.kind!r}, look={e.look!r}{props}")
    t = spec.time
    lines.append(f"Time span: start {t.start:g}, end {t.end:g} {t.unit}, stop={t.stop}")
    if spec.environment is not None:
        lines.append(f"Environment: {spec.environment.name}")
    if inp.answers:
        lines += ["", "The user's answers:"] + [f"- {a.question} -> {a.answer} {a.unit}".rstrip() for a in inp.answers]
    return "\n".join(lines)


def _structural(plan: EditPlan) -> EditPlan:
    return EditPlan(kind="structural", summary=plan.summary, changes=VariantChanges(), stop_fixed=False,
                    looks=[], questions=[])


def sanitize_plan(spec: SimSpec, plan: EditPlan, answered: bool) -> EditPlan:
    """Make the classifier's plan safe to act on. Deterministic, never raises."""
    try:
        if plan.kind in ("parameters", "visual"):
            ch = plan.changes
            if plan.kind == "visual":
                if not plan.looks:
                    return _structural(plan)
                ch = VariantChanges()
            elif not (ch.parameters or ch.initial or ch.end is not None or plan.looks):
                return _structural(plan)
            fixed = plan.stop_fixed and ch.end is not None
            out = plan.model_copy(update={"changes": ch, "stop_fixed": fixed, "questions": []})
            try:
                apply_plan(spec, out)
            except ValueError:
                return _structural(plan)
            return out
        if plan.kind == "unclear":
            if answered or not plan.questions:
                return _structural(plan)
            seen: set[str] = set()
            qs = []
            for q in plan.questions[:3]:
                base = q.id or "q"
                qid, n = base, 2
                while qid in seen:
                    qid, n = f"{base}_{n}", n + 1
                seen.add(qid)
                qs.append(q.model_copy(update={"id": qid}))
            return EditPlan(kind="unclear", summary=plan.summary, changes=VariantChanges(), stop_fixed=False,
                            looks=[], questions=qs)
        return _structural(plan)
    except Exception:  # noqa: BLE001 - sanitising must never fail the edit
        return _structural(plan)


@activity.defn(name=EDIT_ACTIVITY)
async def classify_edit(inp: EditClassifyInput) -> EditPlan:
    plan = await activities.llm.structured(Role.GENERAL, EDIT_INSTRUCTIONS, render_edit_input(inp), EditPlan)
    return sanitize_plan(inp.spec, plan, answered=bool(inp.answers))
