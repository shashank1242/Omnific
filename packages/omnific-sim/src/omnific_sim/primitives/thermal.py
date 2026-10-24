"""Heat-transfer primitives (SI units)."""

from collections.abc import Sequence

import numpy as np
from scipy.constants import sigma as SIGMA
from scipy.integrate import solve_ivp
from scipy.sparse import diags

from ..result import SimResult
from .ode import SolverError, integrate

BC = float | tuple[float, float, float]


def cell_centers(length: float, n_cells: int) -> np.ndarray:
    """Cell-centre coordinates [m] used by heat_1d."""
    return (np.arange(n_cells) + 0.5) * length / n_cells


def heat_1d(
    length: float,
    n_cells: int,
    alpha: float,
    T_init: float | Sequence[float] | np.ndarray,
    bc_left: BC,
    bc_right: BC,
    t_end: float,
    n_samples: int,
    rtol: float = 1e-8,
    atol: float = 1e-10,
) -> SimResult:
    """1-D heat equation T_t = alpha T_xx on [0, length], cell-centred finite volumes + BDF.

    length [m], alpha [m^2/s], T_init [K] scalar or array of n_cells, t_end [s].
    Each BC is either a float (Dirichlet wall temperature [K]) or a tuple
    (h [W/m^2/K], T_inf [K], k [W/m/K]) for a convective (Robin) boundary.
    State "T" [K] has shape (n_samples, n_cells); cell positions: cell_centers().
    Second-order accurate in space.
    """
    dx = length / n_cells
    T0 = np.broadcast_to(np.asarray(T_init, dtype=float), (n_cells,)).copy()

    def edge(bc: BC) -> tuple[float, float]:
        """Return (a, b) so that boundary-cell dT/dt gets a*(b - T_cell)."""
        if isinstance(bc, tuple):
            h, T_inf, k = bc
            # series: half-cell conduction dx/(2k) and film 1/h -> overall conductance U
            U = 1.0 / (dx / (2 * k) + 1.0 / h)
            return alpha * U / (k * dx), T_inf
        return 2 * alpha / dx**2, float(bc)

    aL, bL = edge(bc_left)
    aR, bR = edge(bc_right)
    c = alpha / dx**2
    main = np.full(n_cells, -2 * c)
    main[0] = -c - aL
    main[-1] = -c - aR
    A = diags([np.full(n_cells - 1, c), main, np.full(n_cells - 1, c)], [-1, 0, 1], format="csc")
    src = np.zeros(n_cells)
    src[0] += aL * bL
    src[-1] += aR * bR

    t_eval = np.linspace(0.0, t_end, n_samples)
    sol = solve_ivp(lambda t, T: A @ T + src, (0.0, t_end), T0, method="BDF", t_eval=t_eval,
                    jac=A, rtol=rtol, atol=atol)
    if not sol.success:
        raise SolverError(f"BDF failed: {sol.message}")
    return SimResult(t=sol.t, state={"T": sol.y.T}, units={"T": "kelvin"},
                     meta={"solver": "BDF", "n_cells": n_cells})


def lumped_capacitance(
    T0: float, T_inf: float, h: float, area: float, mass: float, cp: float,
    t_end: float, n: int, emissivity: float = 0.0,
) -> SimResult:
    """Lumped body: m cp dT/dt = -h A (T - T_inf) - eps sigma A (T^4 - T_inf^4).

    T0, T_inf [K], h [W/m^2/K], area [m^2], mass [kg], cp [J/kg/K], t_end [s],
    emissivity [-] (0 disables radiation; surroundings at T_inf). State: T [K].
    Without radiation T = T_inf + (T0 - T_inf) exp(-h A t / (m cp)).
    """
    def rhs(t, y):
        T = y["T"]
        q = h * area * (T - T_inf) + emissivity * SIGMA * area * (T**4 - T_inf**4)
        return {"T": -q / (mass * cp)}

    return integrate(rhs, {"T": T0}, {"T": "kelvin"}, (0.0, t_end), n)
