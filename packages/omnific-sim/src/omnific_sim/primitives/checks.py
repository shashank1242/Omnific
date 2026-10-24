"""Evaluator helpers for checking SimResults and equations."""

import numpy as np
import sympy as sp
from sympy.parsing.sympy_parser import parse_expr

from ..result import SimResult
from ..units import ureg


def conservation_drift(series) -> float:
    """Max |s - s[0]| / |s[0]| over the series (absolute if s[0] == 0)."""
    s = np.asarray(series, dtype=float)
    ref = abs(s[0])
    dev = float(np.max(np.abs(s - s[0])))
    return dev / ref if ref > 0 else dev


def convergence_ratio(result_coarse: SimResult, result_fine: SimResult, var: str,
                      result_finest: SimResult | None = None) -> float:
    """Convergence indicator for state `var` (fine results interpolated to coarse times).

    Without `result_finest`: relative difference max|coarse - fine| / max|fine| (small = converged).
    With it: err(coarse vs finest) / err(fine vs finest); >1 means refinement is helping
    (about 2^p for order p when the step is halved).
    """
    def interp(r: SimResult):
        v = r.state[var]
        return np.interp(result_coarse.t, r.t, v) if v.ndim == 1 else v

    c = result_coarse.state[var]
    f = interp(result_fine)
    if result_finest is None:
        return float(np.max(np.abs(c - f)) / max(np.max(np.abs(f)), 1e-300))
    ref = interp(result_finest)
    return float(np.max(np.abs(c - ref)) / max(np.max(np.abs(f - ref)), 1e-300))


def within_bounds(result: SimResult, var: str, min: float | None = None,
                  max: float | None = None) -> bool:
    """True if all values of state `var` are finite and within [min, max] (None = unbounded)."""
    v = result.state[var]
    if not np.all(np.isfinite(v)):
        return False
    return not ((min is not None and np.any(v < min)) or (max is not None and np.any(v > max)))


def _dim(expr, dims):
    """Dimensionality (pint UnitsContainer) of a SymPy expression; ValueError if inconsistent."""
    one = ureg.dimensionless.dimensionality
    if expr.is_Number:
        return one
    if expr.is_Symbol:
        if expr.name not in dims:
            raise KeyError(f"no unit given for symbol {expr.name!r}")
        return dims[expr.name]
    if expr.is_Mul:
        out = one
        for a in expr.args:
            out = out * _dim(a, dims)
        return out
    if expr.is_Add:
        ds = [_dim(a, dims) for a in expr.args]
        if any(d != ds[0] for d in ds):
            raise ValueError(f"cannot add terms with different dimensions in {expr}")
        return ds[0]
    if expr.is_Pow:
        b, e = expr.args
        if _dim(e, dims) != one:
            raise ValueError("exponent must be dimensionless")
        if e.is_Number:
            return _dim(b, dims) ** float(e)
        if _dim(b, dims) != one:
            raise ValueError("symbolic exponent requires dimensionless base")
        return one
    if isinstance(expr, sp.Function):
        if any(_dim(a, dims) != one for a in expr.args):
            raise ValueError(f"argument of {expr.func.__name__} must be dimensionless")
        return one
    raise ValueError(f"unsupported expression: {expr}")


def dimension_check(expr_str: str, symbol_units: dict[str, str]) -> bool:
    """True if both sides of 'lhs = rhs' have the same dimensionality and all sums are consistent.

    symbol_units: symbol -> pint unit string ('' for dimensionless). Functions such as sin/exp
    need dimensionless arguments. Symbols without a unit raise KeyError; dimensional errors
    return False.
    """
    if "=" not in expr_str:
        raise ValueError("expression must have the form 'lhs = rhs'")
    lhs, rhs = expr_str.split("=", 1)
    local = {k: sp.Symbol(k) for k in symbol_units}
    dims = {k: ureg.parse_units(u).dimensionality for k, u in symbol_units.items()}
    try:
        dl = _dim(parse_expr(lhs, local_dict=local), dims)
        dr = _dim(parse_expr(rhs, local_dict=local), dims)
    except ValueError:
        return False
    return dl == dr
