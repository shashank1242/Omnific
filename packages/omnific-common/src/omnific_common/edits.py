"""Deterministic halves of editing a simulation: apply changed values / looks to a SimSpec, and say what changed.

Shared by the API (Tune runs) and the workflow (follow-up versions); no I/O, so safe inside workflow code.
"""

from __future__ import annotations

import math

from omnific_sim.spec import SimSpec

from .contracts import EditPlan, VariantChanges


def apply_changes(spec: SimSpec, changes: VariantChanges) -> SimSpec:
    """The SimSpec with the user's values applied. Raises ValueError naming the first bad change."""
    new_params = {c.name: c.value for c in changes.parameters}
    new_initial = {c.name: c.value for c in changes.initial}
    params = {p.name for p in spec.parameters}
    states = {v.name for v in spec.state}
    for name in new_params:
        if name not in params:
            raise ValueError(f"unknown parameter {name!r}")
    for name in new_initial:
        if name not in states:
            raise ValueError(f"unknown state variable {name!r}")
    for c in [*changes.parameters, *changes.initial]:
        if not math.isfinite(c.value):
            raise ValueError(f"{c.name} must be a finite number")
    time = spec.time
    if changes.end is not None:
        if not math.isfinite(changes.end) or changes.end <= time.start:
            raise ValueError(f"the end time must be a number after the start ({time.start} {time.unit})")
        time = time.model_copy(update={"end": changes.end})
    return spec.model_copy(update={
        "parameters": [p.model_copy(update={"value": new_params[p.name]}) if p.name in new_params else p
                       for p in spec.parameters],
        "state": [v.model_copy(update={"initial_value": new_initial[v.name]}) if v.name in new_initial else v
                  for v in spec.state],
        "time": time,
    })


def apply_plan(spec: SimSpec, plan: EditPlan) -> SimSpec:
    """A 'parameters' or 'visual' plan applied to the spec (a 'structural' one goes to the modeler instead). Both may
    carry looks ("swap the ball for a bowling ball" changes its mass and its look). Raises ValueError when the plan
    names something the spec doesn't have."""
    if plan.kind not in ("parameters", "visual"):
        raise ValueError(f"a {plan.kind!r} plan is not applied directly")
    out = spec
    if plan.kind == "parameters":
        out = apply_changes(spec, plan.changes)
        if plan.changes.end is not None and plan.stop_fixed:
            out = out.model_copy(update={"time": out.time.model_copy(update={
                "stop": "fixed", "stop_reason": "You asked for this duration."})})
    looks = {x.entity: x.look.strip() for x in plan.looks}
    names = {e.name for e in out.entities}
    for name in looks:
        if name not in names:
            raise ValueError(f"unknown entity {name!r}")
    return out.model_copy(update={"entities": [
        e.model_copy(update={"look": looks[e.name]}) if e.name in looks else e for e in out.entities]})


def _num(v: float) -> str:
    return f"{v:.6g}"


def spec_diff(old: SimSpec, new: SimSpec) -> list[str]:
    """What changed between two versions, one plain line each (empty when nothing did)."""
    out: list[str] = []
    olds, news = {p.name: p for p in old.parameters}, {p.name: p for p in new.parameters}
    for name, p in news.items():
        if name not in olds:
            out.append(f"new value {name} = {_num(p.value)} {p.unit}".rstrip())
        elif (o := olds[name]).value != p.value or o.unit != p.unit:
            out.append(f"{name}: {_num(o.value)} {o.unit} → {_num(p.value)} {p.unit}".replace("  ", " "))
    out += [f"removed value {name}" for name in olds if name not in news]

    old_state, new_state = {v.name: v for v in old.state}, {v.name: v for v in new.state}
    for name, v in new_state.items():
        if name not in old_state:
            out.append(f"new state {name}")
        elif (o := old_state[name]).initial_value != v.initial_value or o.unit != v.unit:
            out.append(f"{name} starts at {_num(v.initial_value)} {v.unit} (was {_num(o.initial_value)} {o.unit})"
                       .replace("  ", " ").replace(" )", ")"))
    out += [f"removed state {name}" for name in old_state if name not in new_state]

    old_e, new_e = {e.name: e for e in old.entities}, {e.name: e for e in new.entities}
    for name, e in new_e.items():
        if name not in old_e:
            out.append(f"new object {name} ({e.kind})")
            continue
        o = old_e[name]
        if o.kind != e.kind:
            out.append(f"{name} is now a {e.kind} (was {o.kind})")
        if (o.parent, o.pivot, o.arm) != (e.parent, e.pivot, e.arm):
            out.append(f"{name} is jointed differently (pivot / arm / parent)")
        if o.bound_state != e.bound_state:
            out.append(f"{name} now moves with {', '.join(e.bound_state) or 'nothing'}")
        if o.geometry != e.geometry:
            g = e.geometry
            out.append(f"{name} is now a {g.shape} of {' x '.join(f'{v:g}' for v in g.size)} m" if g and g.size
                       else f"{name}'s shape or size changed")
        if o.look != e.look:
            out.append(f"{name} looks like: {e.look}" if e.look else f"{name}: look reset")
    out += [f"removed object {name}" for name in old_e if name not in new_e]

    old_eq = sorted(e.expression.replace(" ", "") for e in old.equations)
    new_eq = sorted(e.expression.replace(" ", "") for e in new.equations)
    if old_eq != new_eq:
        out.append(f"equations changed ({len(old_eq)} → {len(new_eq)})")

    oe, ne = old.environment, new.environment
    if (oe.name if oe else None) != (ne.name if ne else None):
        out.append(f"setting: {oe.name if oe else 'none'} → {ne.name if ne else 'none'}")
    elif oe and ne:
        # what you can see; a reworded description or a re-listed parameter is not a change to report
        seen = ("kind", "ground", "ground_material", "lighting", "sky_color", "ground_color", "fog")
        if changed := [f.replace("_", " ") for f in seen if getattr(oe, f) != getattr(ne, f)]:
            out.append(f"setting {ne.name}: {', '.join(changed)} changed")

    ot, nt = old.time, new.time
    if ot.start != nt.start:
        out.append(f"starts at t = {_num(nt.start)} {nt.unit} (was {_num(ot.start)})")
    if ot.end != nt.end:
        out.append(f"runs up to {_num(nt.end)} {nt.unit} (was {_num(ot.end)})")
    if ot.stop != nt.stop:
        out.append("runs for exactly that long" if nt.stop == "fixed" else "stops once everything comes to rest")
    return out
