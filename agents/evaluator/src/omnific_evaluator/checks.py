"""Deterministic evaluator checks. Each returns a CheckResult; none raises on bad model/solver output."""

import os

import numpy as np
import sympy as sp
from omnific_common.contracts import CheckResult
from omnific_sim import visual
from omnific_sim.conventions import check_equation_dimensions, identifiers, symbol_units
from omnific_sim.primitives.checks import conservation_drift, convergence_ratio, within_bounds
from omnific_sim.result import SimResult
from omnific_sim.spec import SimSpec
from omnific_sim.units import Q_
from sympy.parsing.sympy_parser import parse_expr

CONVERGENCE_REL_TOL = 1e-2
# Bound slack as a fraction of the bound span: event overshoot (stopping at x = L) is float noise, not physics.
BOUNDS_REL_TOL = 1e-3
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


def check_environment(spec: SimSpec) -> list[CheckResult]:
    """Every parameter the environment sets must reach the model: a 'Moon' setting must really change g. Only the
    equations count: an invariant or analytic check naming g is a diagnostic and doesn't move anything."""
    env = spec.environment
    if env is None:
        return []
    used: set[str] = set()
    for e in spec.equations:
        used |= identifiers(e.expression)
    out = []
    for name in env.parameters:
        ok = name in used
        out.append(CheckResult(
            name=f"environment[{name}]", passed=ok,
            detail="used by the model" if ok else f"the {env.name!r} environment sets {name} but no equation uses it",
            blame=None if ok else "modeler",
        ))
    return out


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
        if within_bounds(view, b.variable, b.min, b.max, rtol=BOUNDS_REL_TOL):
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


# Contact slack, as a fraction of the body's radius / half-height: rolling contact is modelled, not exact.
CONTACT_TOL = 0.25


def check_geometry(spec: SimSpec, result: SimResult) -> list[CheckResult]:
    """The picture must match the physics: a body with true geometry may not sink into the ground (y = 0, when the
    setting has ground) and a sphere may not pass through a static ramp, block or table. Positions are centres:
    a ball resting on the ground has centre y = radius. Blame the modeler (geometry, placement or which state is the
    centre)."""
    env = spec.environment
    ground = env is None or env.ground == "flat"
    units = dict(result.units)
    n = len(result.t)
    values = {k: np.asarray(v, dtype=float) for k, v in result.state.items()}
    statics = []
    for e in spec.entities:
        place = visual.static_place(e, units)
        found = visual.geometry(e)
        if place is not None and found is not None:
            statics.append((e, found[0], found[1], place))
    out = []
    for e, g, size, place in statics:  # a ramp or table must stand on the ground, not in it
        if not ground or g.shape == "plane":
            continue
        bottom = place[1] - size[1] / 2
        ok = bottom >= -CONTACT_TOL * size[1] / 2
        out.append(CheckResult(
            name=f"geometry[{e.name}]", passed=ok, blame=None if ok else "modeler",
            detail=f"stands on the ground (bottom y = {bottom:.3g} m)" if ok else
            f"{e.name} sits {-bottom:.3g} m below the ground: its centre y must be at least half its height "
            f"({size[1] / 2:.3g} m), e.g. a ramp of rise h has centre y = h / 2"))
    for e in spec.entities:
        found = visual.geometry(e)
        if found is None or visual.static_place(e, units) is not None:
            continue
        g, size = found
        try:
            track = visual.centre_track(e, values, units, n)
        except Exception:  # noqa: BLE001 - unit trouble is reported by the dimension checks
            track = None
        if track is None or not np.isfinite(track).all():
            continue
        half_h = size[1] / 2
        problems: list[str] = []
        if ground and half_h > 0:
            low = float(np.min(track[:, 1] - half_h))
            if low < -CONTACT_TOL * half_h:
                i = int(np.argmin(track[:, 1]))
                problems.append(
                    f"sinks {-low:.3g} m into the ground at t = {float(result.t[i]):.3g} (its lowest centre y is "
                    f"{float(track[i, 1]):.3g} m, half its height {half_h:.3g} m): position states are the CENTRE, so "
                    "a body resting on the ground has y = its half-height (a ball: its radius); if y is the contact "
                    "point, bind the centre instead")
        if g.shape == "sphere":
            r = min(size) / 2
            for s_ent, sg, ssize, splace in statics:
                d = visual.solid_distance(track, sg, ssize, splace)
                if d is None:
                    continue
                depth = r - float(np.min(d))
                if depth > CONTACT_TOL * r:
                    i = int(np.argmin(d))
                    problems.append(
                        f"passes {depth:.3g} m into {s_ent.name} at t = {float(result.t[i]):.3g} (radius {r:.3g} m): "
                        f"place {s_ent.name} so the path of {e.name}'s centre stays one radius above its surface")
        ok = not problems
        out.append(CheckResult(
            name=f"geometry[{e.name}]", passed=ok, blame=None if ok else "modeler",
            detail="stays on the ground and outside every solid" if ok else "; ".join(problems)))
    return out


def check_effects(spec: SimSpec, result: SimResult) -> list[CheckResult]:
    """Every effect must actually show: its brightness and radius must be above zero at some point of the run (an
    explosion whose states stay 0 is never drawn), and it must not be lit from start to end when phases say it starts
    later. Blame the modeler (the event's equations or timing)."""
    out = []
    t = np.asarray(result.t, dtype=float)
    for fx in getattr(spec, "effects", []) or []:
        problems = []
        on = np.ones(len(t), dtype=bool)  # drawn where its magnitude (what the viewer and .blend use) is above 0
        for field, what in (("intensity_state", "brightness"), ("radius_state", "radius")):
            name = getattr(fx, field)
            if not name:
                continue
            v = np.asarray(result.state.get(name, []), dtype=float)
            if v.ndim != 1 or len(v) != len(t) or not np.isfinite(v).any():
                problems.append(f"its {what} state {name!r} has no usable values")
                continue
            lit = np.nan_to_num(np.abs(v)) > 0
            if not lit.any():
                problems.append(f"its {what} {name!r} stays 0 for the whole run, so it never appears: make the event "
                                "happen inside the time span (e.g. Piecewise on the event time) and grow from there")
            on &= lit
        if not problems and not on.any():
            problems.append("its radius and brightness are never above 0 at the same time, so it never appears")
        ok = not problems
        out.append(CheckResult(name=f"effect[{fx.name}]", passed=ok, blame=None if ok else "modeler",
                               detail=f"{fx.kind} is drawn from the solved states" if ok else "; ".join(problems)))
    return out


# One object this many times farther out than the typical object turns everything else into specks on screen.
SPREAD_MAX = 30.0


def check_scene_spread(spec: SimSpec, result: SimResult) -> list[CheckResult]:
    """The scene is drawn on one scale, set by its farthest object: a distant observer 100x farther than the action
    makes the bomb, the probe and the black hole invisible dots. Fail when one object's reach is more than SPREAD_MAX
    times the median object's, naming it, so the modeler places it at a visual distance (true distance kept as a
    parameter) or leaves it out of the entities."""
    units = dict(result.units)
    n = len(result.t)
    values = {k: np.asarray(v, dtype=float) for k, v in result.state.items()}
    reach: dict[str, float] = {}
    for e in spec.entities:
        found = visual.geometry(e)
        half = max(found[1]) / 2 if found else 0.0
        place = visual.static_place(e, units)
        if place is not None:
            reach[e.name] = float(np.linalg.norm(place)) + half
            continue
        try:
            track = visual.centre_track(e, values, units, n)
        except Exception:  # noqa: BLE001 - unit problems are reported by the dimension checks
            track = None
        if track is not None and np.isfinite(track).any():
            reach[e.name] = float(np.nanmax(np.linalg.norm(track, axis=1))) + half
        elif half:
            reach[e.name] = half
    if len(reach) < 3:
        return []
    typical = float(np.median(list(reach.values())))
    if typical <= 0:  # most objects sit at the origin: compare with the nearest one that doesn't
        typical = min((r for r in reach.values() if r > 0), default=0.0)
    far = max((r, name) for name, r in reach.items())
    if typical <= 0 or far[0] <= SPREAD_MAX * typical:
        return [CheckResult(name="scene.spread", passed=True, blame=None,
                            detail=f"objects within {far[0] / typical:.3g}x of each other's reach" if typical > 0
                            else "objects share one scale")]
    return [CheckResult(name="scene.spread", passed=False, blame="modeler", detail=(
        f"{far[1]} reaches {far[0]:.3g} m, {far[0] / typical:.0f}x farther than the typical object ({typical:.3g} m): "
        "the scene is drawn on one scale, so everything else becomes an invisible speck. Keep every entity within about "
        f"{SPREAD_MAX:.0f}x of the action: give a distant observer a fixed visual position near the action (keep its "
        "true distance as a parameter the equations use, and say so in assumptions) or leave it out of the entities"))]


def check_bindings(spec: SimSpec, result: SimResult) -> list[CheckResult]:
    """A body with a fixed place (geometry.position) is never moved, so a scalar length state bound to it (its radius,
    say) shows nothing: the modeler meant a size (geometry.size) or the wrong entity. Length FIELDS (a surface height
    profile) are fine: they colour it."""
    units = dict(result.units)
    out = []
    for e in spec.entities:
        if e.geometry is None or len(e.geometry.position) != 3:
            continue
        scalar_lengths = [v for v in e.bound_state
                          if np.asarray(result.state.get(v, []), dtype=float).ndim == 1
                          and visual.is_length(units.get(v, ""))]
        if scalar_lengths:
            out.append(CheckResult(name=f"binding[{e.name}]", passed=False, blame="modeler", detail=(
                f"{e.name} has a fixed place (geometry.position) but binds length state(s) {scalar_lengths}, which "
                "the viewer would read as its position: bind only what changes how it looks, put sizes in "
                "geometry.size, or drop geometry.position if it moves")))
    return out

