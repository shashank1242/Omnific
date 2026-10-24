"""Chemical kinetics primitives (SI units)."""

from collections.abc import Callable

import numpy as np
from scipy.constants import R as GAS_CONSTANT
from scipy.integrate import solve_ivp

from ..result import SimResult
from .ode import SolverError


def arrhenius(A: float, Ea: float, T: float) -> float:
    """Rate constant k = A exp(-Ea / (R T)). A [rate-constant units], Ea [J/mol], T [K]."""
    return A * float(np.exp(-Ea / (GAS_CONSTANT * T)))


def reaction_network(
    species0: dict[str, float],
    reactions: list[dict],
    T: float | Callable[[float], float],
    t_end: float,
    n: int,
    conc_unit: str = "mol/m**3",
    method: str = "Radau",
    rtol: float = 1e-8,
    atol: float = 1e-12,
) -> SimResult:
    """Mass-action kinetics with Arrhenius rates, integrated with a stiff solver.

    species0: name -> initial concentration (conc_unit). Each reaction is a dict with
    reactants {name: stoich}, products {name: stoich}, A (units such that rate is
    conc_unit/s), Ea [J/mol], optional orders {name: order} (default: reactant stoichiometry).
    Rate r = A exp(-Ea/RT) * prod(c^order); dc_i/dt = sum (nu_prod - nu_reac) r.
    T [K] is a constant or a callable T(t). State: one series per species.
    """
    names = list(species0)
    idx = {s: i for i, s in enumerate(names)}
    parsed = []
    for r in reactions:
        reac, prod = r.get("reactants", {}), r.get("products", {})
        for s in (*reac, *prod):
            if s not in idx:
                raise ValueError(f"unknown species {s!r} in reaction")
        nu = np.zeros(len(names))
        for s, v in reac.items():
            nu[idx[s]] -= v
        for s, v in prod.items():
            nu[idx[s]] += v
        orders = r.get("orders", reac)
        parsed.append((nu, [(idx[s], o) for s, o in orders.items()], r["A"], r["Ea"]))
    Tfun = T if callable(T) else (lambda t: T)

    def f(t, c):
        Tt = Tfun(t)
        cc = np.maximum(c, 0.0)
        d = np.zeros_like(c)
        for nu, ords, A, Ea in parsed:
            rate = arrhenius(A, Ea, Tt)
            for i, o in ords:
                rate *= cc[i] ** o
            d += nu * rate
        return d

    t_eval = np.linspace(0.0, t_end, n)
    sol = solve_ivp(f, (0.0, t_end), [species0[s] for s in names], method=method,
                    t_eval=t_eval, rtol=rtol, atol=atol)
    if not sol.success:
        raise SolverError(f"{method} failed: {sol.message}")
    return SimResult(t=sol.t, state={s: sol.y[i] for s, i in idx.items()},
                     units=dict.fromkeys(names, conc_unit),
                     meta={"solver": method, "nfev": int(sol.nfev)})
