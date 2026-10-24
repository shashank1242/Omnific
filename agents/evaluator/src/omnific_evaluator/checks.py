"""Deterministic evaluator checks. Each returns a CheckResult; none raises on bad model/solver output."""

import os

import numpy as np
import sympy as sp
from omnific_common.contracts import CheckResult
from omnific_sim.conventions import check_equation_dimensions, symbol_units
from omnific_sim.primitives.checks import conservation_drift, convergence_ratio, within_bounds
from omnific_sim.result import SimResult
from omnific_sim.spec import SimSpec
from omnific_sim.units import Q_
from sympy.parsing.sympy_parser import parse_expr

CONVERGENCE_REL_TOL = 1e-2
_TINY = 1e-300


def convergence_tol() -> float:
    try:
        return float(os.environ.get("EVAL_CONVERGENCE_TOL", CONVERGENCE_REL_TOL))
    except ValueError:
        return CONVERGENCE_REL_TOL


def check_dimensions(spec: SimSpec) -> list[CheckResult]:
    units = symbol_units(spec)
    out = []
    for eq in spec.equations:
        name = f"dimensions[{eq.label}]"
        try:
            verdict = check_equation_dimensions(eq.expression, units)
            ok = verdict is not False
            detail = {True: "ok", None: "skipped: uses functions exempt from dimension checking",
                      False: f"dimensionally inconsistent: {eq.expression}"}[verdict]
        except KeyError as e:
            ok, detail = False, f"unknown symbol or unit in {eq.expression!r}: {e}"
        except Exception as e:  # noqa: BLE001 - unparsable model output is a finding, not a crash
            ok, detail = False, f"could not check {eq.expression!r}: {e}"
        out.append(CheckResult(name=name, passed=ok, detail=detail, blame=None if ok else "modeler"))
    return out


def values_in_spec_unit(spec: SimSpec, result: SimResult, name: str) -> np.ndarray:
    """State values converted to the unit declared in the spec (falls back to raw values)."""
    v = np.asarray(result.state[name], dtype=float)
    declared = next(s.unit for s in spec.state if s.name == name)
    have = result.units.get(name, declared)
    if have == declared:
        return v
    try:
        return np.asarray(Q_(v, have).to(declared).magnitude, dtype=float)
    except Exception:  # noqa: BLE001
        return v


def check_finite(result: SimResult) -> CheckResult:
    bad = [k for k, v in result.state.items() if not np.all(np.isfinite(v))]
    if not np.all(np.isfinite(result.t)):
        bad.append("t")
    if bad:
        return CheckResult(name="finiteness", passed=False, blame="solver_coder",
                           detail=f"non-finite values in: {', '.join(bad)}")
    return CheckResult(name="finiteness", passed=True, detail="all values finite")


def check_bounds(spec: SimSpec, result: SimResult) -> list[CheckResult]:
    out = []
    declared = {s.name: s for s in spec.state}
    for b in spec.bounds:
        name = f"bounds[{b.variable}]"
        rng = f"[{b.min}, {b.max}]"
        if b.variable not in declared:
            out.append(CheckResult(name=name, passed=False, blame="modeler",
                                   detail=f"bound refers to unknown state variable {b.variable!r}"))
            continue
        init = declared[b.variable].initial_value
        if (b.min is not None and init < b.min) or (b.max is not None and init > b.max):
            out.append(CheckResult(name=name, passed=False, blame="modeler",
                                   detail=f"initial value {init} already outside bounds {rng}"))
            continue
        if b.variable not in result.state:
            out.append(CheckResult(name=name, passed=False, blame="solver_coder",
                                   detail=f"{b.variable!r} missing from result"))
            continue
        v = values_in_spec_unit(spec, result, b.variable)
        view = SimResult(t=result.t, state={b.variable: v}, units={})
        if within_bounds(view, b.variable, b.min, b.max):
            out.append(CheckResult(name=name, passed=True, detail=f"within {rng}"))
        else:
            fin = v[np.isfinite(v)]
            span = f"observed [{fin.min():.6g}, {fin.max():.6g}]" if fin.size else "no finite values"
            out.append(CheckResult(name=name, passed=False, blame="solver_coder",
                                   detail=f"{span} violates bounds {rng}"))
    return out


def check_invariants(spec: SimSpec, result: SimResult) -> list[CheckResult]:
    out = []
    names = [s.name for s in spec.state] + [p.name for p in spec.parameters] + ["t"]
    syms = {n: sp.Symbol(n) for n in names}
    for inv in spec.invariants:
        name = f"invariant[{inv.name}]"
        try:
            expr = parse_expr(inv.expression, local_dict=syms)
            free = {s.name for s in expr.free_symbols}
            unknown = free - set(names)
            if unknown:
                raise ValueError(f"unknown symbols {sorted(unknown)}")
            args = sorted(free)
            fn = sp.lambdify([syms[a] for a in args], expr, modules="numpy")
        except Exception as e:  # noqa: BLE001
            out.append(CheckResult(name=name, passed=False, blame="modeler",
                                   detail=f"cannot parse invariant {inv.expression!r}: {e}"))
            continue
        params = {p.name: p.value for p in spec.parameters}
        state_names = {s.name for s in spec.state}
        missing = [a for a in args if a in state_names and a not in result.state]
        if missing:
            out.append(CheckResult(name=name, passed=False, blame="solver_coder",
                                   detail=f"result is missing state {missing}"))
            continue
        values = []
        for a in args:
            if a in state_names:
                values.append(values_in_spec_unit(spec, result, a))
            elif a in params and a not in state_names:
                values.append(params[a])
            else:  # time
                values.append(np.asarray(result.t, dtype=float))
        try:
            with np.errstate(all="ignore"):
                series = np.asarray(fn(*values), dtype=float)
                if series.ndim == 0:
                    series = np.broadcast_to(series, result.t.shape)
        except Exception as e:  # noqa: BLE001
            out.append(CheckResult(name=name, passed=False, blame="solver_coder",
                                   detail=f"could not evaluate invariant: {e}"))
            continue
        if series.ndim > 1:  # fields: integrate (sum) over the spatial axes
            series = series.reshape(series.shape[0], -1).sum(axis=1)
        if not np.all(np.isfinite(series)):
            out.append(CheckResult(name=name, passed=False, blame="solver_coder",
                                   detail="invariant evaluates to non-finite values"))
            continue
        drift = conservation_drift(series)
        ok = drift <= inv.rel_tolerance
        out.append(CheckResult(
            name=name, passed=ok, blame=None if ok else "solver_coder",
            detail=f"max relative drift {drift:.3g} {'<=' if ok else '>'} tolerance {inv.rel_tolerance:g}"))
    return out


def check_convergence(spec: SimSpec, coarse: SimResult, fine: SimResult | None) -> CheckResult:
    """Compare the refine=1 result with the refine>1 result, per state variable (relative difference)."""
    if fine is None:
        return CheckResult(name="convergence", passed=False, blame="solver_coder",
                           detail="no refined (refine>1) result available; simulate() must support `refine`")
    tol = convergence_tol()
    parts, bad = [], []
    for s in spec.state:
        if s.name not in coarse.state or s.name not in fine.state:
            bad.append(s.name)
            parts.append(f"{s.name}: missing from a result")
            continue
        c = SimResult(t=coarse.t, state={s.name: values_in_spec_unit(spec, coarse, s.name)}, units={})
        f = SimResult(t=fine.t, state={s.name: values_in_spec_unit(spec, fine, s.name)}, units={})
        if c.state[s.name].shape != f.state[s.name].shape:
            bad.append(s.name)
            parts.append(f"{s.name}: shape {c.state[s.name].shape} vs refined {f.state[s.name].shape}")
            continue
        r = convergence_ratio(c, f, s.name)
        if not np.isfinite(r) or r > tol:
            bad.append(s.name)
        parts.append(f"{s.name}: rel. diff {r:.3g}")
    ok = not bad
    head = f"coarse vs refined, tolerance {tol:g}: "
    return CheckResult(name="convergence", passed=ok, blame=None if ok else "solver_coder",
                       detail=head + "; ".join(parts) + ("" if ok else f". Not converged: {', '.join(bad)}"))


def check_analytic(spec: SimSpec, result: SimResult) -> list[CheckResult]:
    out = []
    states = {s.name for s in spec.state}
    params = {p.name: p.value for p in spec.parameters}
    syms = {n: sp.Symbol(n) for n in [*params, "t"]}
    seen: dict[str, int] = {}
    for ac in spec.analytic_checks:
        seen[ac.variable] = seen.get(ac.variable, 0) + 1
        name = f"analytic[{ac.variable}]" + (f"#{seen[ac.variable]}" if seen[ac.variable] > 1 else "")

        def fail(blame, detail, name=name):
            out.append(CheckResult(name=name, passed=False, blame=blame, detail=detail))

        if ac.variable not in states:
            fail("modeler", f"analytic check refers to unknown state variable {ac.variable!r}")
            continue
        try:
            expr = parse_expr(ac.expression, local_dict=syms)
            unknown = {x.name for x in expr.free_symbols} - set(syms)
            if unknown:
                raise ValueError(f"unknown symbols {sorted(unknown)}")
            args = sorted({x.name for x in expr.free_symbols})
            fn = sp.lambdify([syms[a] for a in args], expr, modules="numpy")
        except Exception as e:  # noqa: BLE001
            fail("modeler", f"cannot parse analytic expression {ac.expression!r}: {e}")
            continue
        if ac.variable not in result.state:
            fail("solver_coder", f"{ac.variable!r} missing from result")
            continue
        t = np.asarray(result.t, dtype=float)
        mask = t <= ac.t_max if ac.t_max is not None else np.ones(t.shape, bool)
        if not mask.any():
            fail("modeler", f"t_max={ac.t_max} is before the first output sample (t={t[0]:g})")
            continue
        try:
            with np.errstate(all="ignore"):
                ref = np.asarray(fn(*[t[mask] if a == "t" else params[a] for a in args]), dtype=float)
                ref = np.broadcast_to(ref, t[mask].shape)
        except Exception as e:  # noqa: BLE001
            fail("modeler", f"could not evaluate analytic expression {ac.expression!r}: {e}")
            continue
        if not np.all(np.isfinite(ref)):
            fail("modeler", f"analytic expression {ac.expression!r} is non-finite on the compared range")
            continue
        num = values_in_spec_unit(spec, result, ac.variable)[mask]
        ref = ref.reshape(ref.shape + (1,) * (num.ndim - 1))
        err = float(np.max(np.abs(num - ref)) / max(float(np.max(np.abs(ref))), _TINY))
        ok = bool(err <= ac.rel_tolerance)
        rng = f" for t<={ac.t_max:g}" if ac.t_max is not None else ""
        out.append(CheckResult(
            name=name, passed=ok, blame=None if ok else "solver_coder",
            detail=f"max relative error vs {ac.expression!r}{rng}: {err:.3g} "
                   f"{'<=' if ok else '>'} tolerance {ac.rel_tolerance:g}"))
    return out
