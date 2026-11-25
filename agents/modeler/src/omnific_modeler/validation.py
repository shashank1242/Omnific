"""Deterministic SimSpec validation (symbols, derivative convention, dimensions)."""

import itertools
import re

from omnific_sim.conventions import (
    ALLOWED_CONSTANTS,
    ALLOWED_FUNCTIONS,
    ALLOWED_LOGICAL,
    check_equation_dimensions,
    derivative_symbol,
    identifiers,
    symbol_units,
)
from omnific_sim.spec import SimSpec
from omnific_sim.units import ureg
from sympy.parsing.sympy_parser import parse_expr

HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def _unit_ok(unit: str, what: str, errors: list[str]) -> bool:
    try:
        ureg.parse_units(unit)
        return True
    except Exception as e:  # noqa: BLE001
        errors.append(f"{what}: unit {unit!r} does not parse ({e})")
        return False


def validate_spec(spec: SimSpec) -> list[str]:
    errors: list[str] = []
    params = [p.name for p in spec.parameters]
    states = [s.name for s in spec.state]
    declared = params + states

    for n in declared:
        if not n.isidentifier():
            errors.append(f"name {n!r} is not a valid identifier")
    for n in sorted({n for n in declared if declared.count(n) > 1}):
        errors.append(f"name {n!r} is declared more than once")
    if "t" in declared:
        errors.append("'t' is reserved for time; rename that parameter/state")
    for s in states:
        if re.fullmatch(r"d.+_dt", s):
            errors.append(f"state name {s!r} collides with the derivative-symbol convention dX_dt")
    if spec.time.end <= spec.time.start:
        errors.append("time.end must be greater than time.start")
    time_ok = _unit_ok(spec.time.unit, "time", errors)
    if time_ok and ureg.parse_units(spec.time.unit).dimensionality != ureg.second.dimensionality:
        errors.append(f"time.unit {spec.time.unit!r} is not a unit of time")
        time_ok = False
    if not spec.equations:
        errors.append("at least one equation is required")
    if not spec.assumptions:
        errors.append("assumptions must not be empty; state the simplifications explicitly")

    units = symbol_units(spec) if time_ok else {}
    deriv_syms = {derivative_symbol(s) for s in states}
    allowed_eq = set(declared) | deriv_syms | {"t"} | ALLOWED_FUNCTIONS | ALLOWED_CONSTANTS | ALLOWED_LOGICAL
    allowed_plain = set(declared) | ALLOWED_FUNCTIONS | ALLOWED_CONSTANTS | ALLOWED_LOGICAL

    defined: set[str] = set()
    for eq in spec.equations:
        tag = f"equation {eq.label!r}"
        if eq.expression.count("=") != 1 or any(op in eq.expression for op in ("==", "<=", ">=", "!=")):
            errors.append(f"{tag}: expression must contain exactly one '=' ('lhs = rhs')")
            continue
        lhs, rhs = (side.strip() for side in eq.expression.split("=", 1))
        if not lhs or not rhs:
            errors.append(f"{tag}: both sides of '=' must be non-empty")
            continue
        ids = identifiers(eq.expression)
        unknown = sorted(ids - allowed_eq)
        if unknown:
            errors.append(
                f"{tag}: undeclared symbols {unknown}. Declare them as parameters/states or remove "
                f"them (derivatives must be written dX_dt for a declared state X)"
            )
            continue
        defined.add(lhs)
        if not time_ok:
            continue
        try:
            ok = check_equation_dimensions(eq.expression, units)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{tag}: cannot parse/check expression {eq.expression!r}: {e}")
            continue
        if ok is False:
            used = {k: v for k, v in units.items() if k in ids}
            errors.append(
                f"{tag}: dimensionally inconsistent ({eq.expression!r}). Units: "
                + ", ".join(f"{k}=[{v}]" for k, v in sorted(used.items()))
            )

    for s in states:
        if s not in defined and derivative_symbol(s) not in defined:
            errors.append(
                f"state {s!r} is not on the left side of any equation (use '{derivative_symbol(s)} = ...')"
            )

    for inv in spec.invariants:
        unknown = sorted(identifiers(inv.expression) - allowed_plain)
        if unknown:
            errors.append(f"invariant {inv.name!r}: undeclared symbols {unknown}")
    for i, ac in enumerate(spec.analytic_checks):
        tag = f"analytic_checks[{i}] ({ac.variable})"
        if ac.variable not in states:
            errors.append(f"{tag}: variable must be a declared state")
        unknown = sorted(identifiers(ac.expression) - (set(params) | {"t"} | ALLOWED_FUNCTIONS | ALLOWED_CONSTANTS | ALLOWED_LOGICAL))
        if unknown:
            errors.append(
                f"{tag}: expression uses {unknown}; only parameters, t, math functions and pi/E are "
                f"allowed (no state variables, no dX_dt)"
            )
        else:
            try:
                parse_expr(ac.expression)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{tag}: cannot parse expression {ac.expression!r}: {e}")
        if ac.t_max is not None and not (spec.time.start < ac.t_max <= spec.time.end):
            errors.append(f"{tag}: t_max must lie in (time.start, time.end]")
        if ac.rel_tolerance <= 0:
            errors.append(f"{tag}: rel_tolerance must be positive")
    env = spec.environment
    if env is None:
        errors.append("environment is missing: always describe the setting (lab bench, park, the Moon...)")
    else:
        for name in env.parameters:
            if name not in params:
                errors.append(f"environment parameter {name!r} is not a declared parameter")
        for field in ("sky_color", "ground_color"):
            if not HEX_COLOR.fullmatch(getattr(env, field)):
                errors.append(f"environment {field} must be a hex colour like '#9ec9ff'")
    for b in spec.bounds:
        if b.variable not in states:
            errors.append(f"bound variable {b.variable!r} is not a declared state")
    entity_names = [e.name for e in spec.entities]
    parents = {e.name: e.parent for e in spec.entities}
    for ent in spec.entities:
        for v in ent.bound_state:
            if v not in states:
                errors.append(f"entity {ent.name!r}: bound_state {v!r} is not a declared state")
        for prop in ent.properties:
            if prop not in {p.name for p in spec.parameters}:
                errors.append(f"entity {ent.name!r}: property {prop!r} is not a declared parameter")
        for field in ("pivot", "arm"):
            vec = getattr(ent, field)
            if vec is not None and len(vec) != 3:
                errors.append(f"entity {ent.name!r}: {field} must be [x, y, z] in metres")
        if ent.parent is not None and ent.parent not in entity_names:
            errors.append(f"entity {ent.name!r}: parent {ent.parent!r} is not a declared entity")
        if (ent.parent is not None or ent.pivot is not None) and ent.arm is None:
            errors.append(f"entity {ent.name!r}: parent/pivot need an arm (pivot → entity at angle 0)")
        seen, cur = {ent.name}, ent.parent
        while cur is not None and cur in parents:
            if cur in seen:
                errors.append(f"entity {ent.name!r}: parent chain has a cycle")
                break
            seen.add(cur)
            cur = parents[cur]
    errors += _validate_story(spec, states, entity_names)
    return errors


# Words that describe a chart or an idea rather than something you could see in the scene.
ABSTRACT_KINDS = frozenset({"diagram", "chart", "graph", "plot", "curve", "timeline", "histogram", "spectrum"})
LENGTH = ureg.meter.dimensionality
TEMPERATURE = ureg.kelvin.dimensionality


# What the prompt says HAPPENS -> the effect the scene must show (regexes over the lower-cased prompt).
PROMPT_EVENTS = (
    ((r"\bexplo(de|des|ded|ding|sion|sions|sive)\b|\b(blow|blows|blew|blown|blowing) up\b|\bdetonat\w*|"
      r"\bblasts?\b|\bsupernova"), "explosion",
     ("the prompt describes an explosion: add an `explosion` effect (a fireball) anchored on what blows up, with a "
     "radius_state that grows from 0 at the event and an intensity_state that flares and fades (plus a flash and a "
     "shockwave if they fit); make what blew up disappear at that moment")),
    # flames, not gunfire: "fires at", "fire the gun", "open fire", "gunfire" and "rapid-fire" are shots
    # (only "fire"/"fires" can mean a shot: "burning a log" and "ignite the fuel" are always flames)
    ((r"(?<!open )(?<![-\w])(fire|fires)\b(?!\s+(at|the|a|an|off|away|rounds?|shots?|bullets?|guns?|rifles?)\b)"
      r"|\b(burn|burns|burning|burnt|flame|flames|blaze|ablaze|ignite|ignites|ignition)\b"), "fire",
     "the prompt describes fire: add a `fire` effect anchored on what burns, driven by states"),
    (r"\b(smoke|smoky|smoking|smoulder\w*|smolder\w*)\b", "smoke",
     "the prompt describes smoke: add a `smoke` effect driven by states"),
)


# "without detonating", "doesn't explode", "fails to ignite", "no smoke": the event does NOT happen
NEGATED = re.compile(r"\b(not|no|never|without|fails? to|failed to|won't|doesn't|does not|didn't|did not)\b[\w\s'-]{0,24}$")


def _prompt_events(prompt: str) -> list[tuple[str, str]]:
    """Events the prompt says happen (an object's name alone, like 'bomb', is not an event)."""
    text = re.sub(r"\s+", " ", prompt.lower())
    found = []
    for pattern, kind, why in PROMPT_EVENTS:
        if any(not NEGATED.search(text[max(0, m.start() - 40):m.start()]) for m in re.finditer(pattern, text)):
            found.append((kind, why))
    return found


def _dim(unit: str):
    try:
        return ureg.parse_units(unit).dimensionality
    except Exception:  # noqa: BLE001 - unit errors are reported elsewhere
        return None


def _validate_story(spec: SimSpec, states: list[str], entity_names: list[str]) -> list[str]:
    """Entities are things you can see; effects, phases, appearances and key outputs refer to real states and times."""
    errors: list[str] = []
    units = {s.name: s.unit for s in spec.state}
    t0, t1 = spec.time.start, spec.time.end
    for ent in spec.entities:
        words = set(re.findall(r"[a-z]+", f"{ent.name} {ent.kind}".lower()))  # whole words: "graphite" is fine
        if words & ABSTRACT_KINDS:
            errors.append(f"entity {ent.name!r} is a chart or an idea, not an object: entities are physical things you "
                          "can see (the bomb, the black hole, a probe); put measured quantities in states and outputs")
        for field in ("appears_at", "disappears_at"):
            v = getattr(ent, field)
            if v is not None and not (t0 <= v <= t1):
                errors.append(f"entity {ent.name!r}: {field} {v} is outside the run ({t0} to {t1} {spec.time.unit})")
        if ent.appears_at is not None and ent.disappears_at is not None and ent.appears_at >= ent.disappears_at:
            errors.append(f"entity {ent.name!r}: appears_at must be before disappears_at")
    names = [e.name for e in spec.effects]
    for n in sorted({n for n in names if names.count(n) > 1}):
        errors.append(f"effect name {n!r} is used more than once")
    for fx in spec.effects:
        where = f"effect {fx.name!r}"
        if fx.anchor and fx.anchor not in entity_names:
            errors.append(f"{where}: anchor {fx.anchor!r} is not a declared entity")
        if not fx.anchor and len(fx.position) != 3:
            errors.append(f"{where}: give an anchor entity or position [x, y, z] in metres")
        for field, want, label in (("radius_state", LENGTH, "a length"), ("temperature_state", TEMPERATURE,
                                                                          "a temperature in kelvin"),
                                   ("intensity_state", None, "")):
            name = getattr(fx, field)
            if not name:
                continue
            if name not in states:
                errors.append(f"{where}: {field} {name!r} is not a declared state")
            elif want is not None and _dim(units[name]) != want:
                errors.append(f"{where}: {field} {name!r} must be {label} (its unit is {units[name]!r})")
        if not fx.radius_state and not fx.size > 0:
            errors.append(f"{where}: set radius_state (a length state) or a fixed size > 0 in metres")
        if not fx.temperature_state and not HEX_COLOR.fullmatch(fx.color):
            errors.append(f"{where}: without temperature_state, color must be a hex colour like '#ff8a2a'")
    kinds = {fx.kind for fx in spec.effects}
    for kind, why in _prompt_events(spec.prompt):
        if kind not in kinds:
            errors.append(why)
        elif kind in ("explosion", "fire", "smoke") and not any(fx.kind == kind and fx.radius_state
                                                                 for fx in spec.effects):
            errors.append(f"the {kind} effect must grow: give it a radius_state (a length state that is 0 before the "
                          "event and grows after it), not only a fixed size")
    starts = [ph.start for ph in spec.phases]
    if any(b <= a for a, b in itertools.pairwise(starts)):
        errors.append("phases must be in time order with distinct starts")
    for ph in spec.phases:
        if not (t0 <= ph.start < t1):
            errors.append(f"phase {ph.name!r} starts at {ph.start}, outside the run ({t0} to {t1} {spec.time.unit})")
    if len(spec.outputs) > 8:
        errors.append("give at most 8 key outputs: the ones that matter most")
    for o in spec.outputs:
        if o.state not in states:
            errors.append(f"key output {o.label!r}: state {o.state!r} is not a declared state")
    return errors
