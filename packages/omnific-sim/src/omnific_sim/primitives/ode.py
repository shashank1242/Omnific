"""Generic ODE integration returning a SimResult."""

from collections.abc import Callable

import numpy as np
from scipy.integrate import solve_ivp

from ..result import SimResult


class SolverError(RuntimeError):
    """Raised when the ODE solver fails to reach the end of the time span."""


def integrate(
    rhs: Callable[[float, dict[str, float]], dict[str, float]],
    y0: dict[str, float],
    units: dict[str, str],
    t_span: tuple[float, float],
    n_samples: int,
    method: str = "DOP853",
    rtol: float = 1e-9,
    atol: float = 1e-12,
) -> SimResult:
    """Integrate dy/dt = rhs(t, y) and sample at `n_samples` evenly spaced times.

    rhs(t, y) takes t [s] and a dict name->value and returns a dict name->derivative
    (every state name in y0 must appear). `units` maps each state name to a pint unit
    string. Returns SimResult with t [s] shape (n_samples,) and state[name] shape (n_samples,).
    Raises SolverError if the solver fails.
    """
    names = list(y0)
    missing = [k for k in names if k not in units]
    if missing:
        raise ValueError(f"units missing for state variables: {missing}")
    if n_samples < 2:
        raise ValueError("n_samples must be >= 2")

    def f(t, y):
        d = rhs(t, dict(zip(names, y, strict=True)))
        try:
            return [d[k] for k in names]
        except KeyError as e:
            raise KeyError(f"rhs did not return derivative for state {e}") from None

    t_eval = np.linspace(t_span[0], t_span[1], n_samples)
    sol = solve_ivp(
        f, t_span, [y0[k] for k in names], method=method, t_eval=t_eval, rtol=rtol, atol=atol
    )
    if not sol.success:
        raise SolverError(f"{method} failed: {sol.message}")
    return SimResult(
        t=sol.t,
        state={k: sol.y[i] for i, k in enumerate(names)},
        units={k: units[k] for k in names},
        meta={"solver": method, "nfev": int(sol.nfev)},
    )
