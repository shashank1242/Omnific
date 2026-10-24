"""Deterministic SimSpec validation (symbols, derivative convention, dimensions)."""

import re

from omnific_sim.conventions import (
    ALLOWED_CONSTANTS,
    ALLOWED_FUNCTIONS,
    check_equation_dimensions,
    derivative_symbol,
    identifiers,
    symbol_units,
)
from omnific_sim.spec import SimSpec
from omnific_sim.units import ureg
from sympy.parsing.sympy_parser import parse_expr


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
    allowed_eq = set(declared) | deriv_syms | {"t"} | ALLOWED_FUNCTIONS | ALLOWED_CONSTANTS
    allowed_plain = set(declared) | ALLOWED_FUNCTIONS | ALLOWED_CONSTANTS

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
        unknown = sorted(identifiers(ac.expression) - (set(params) | {"t"} | ALLOWED_FUNCTIONS | ALLOWED_CONSTANTS))
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
    for b in spec.bounds:
        if b.variable not in states:
            errors.append(f"bound variable {b.variable!r} is not a declared state")
    for ent in spec.entities:
        for v in ent.bound_state:
            if v not in states:
                errors.append(f"entity {ent.name!r}: bound_state {v!r} is not a declared state")
    return errors
