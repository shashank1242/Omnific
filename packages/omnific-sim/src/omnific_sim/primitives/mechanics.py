"""Classical mechanics primitives (SI units)."""

import numpy as np
from scipy.integrate import solve_ivp

from ..result import SimResult
from .ode import SolverError, integrate


def projectile(
    v0: float,
    angle_deg: float,
    g: float = 9.80665,
    drag_coeff: float = 0.0,
    mass: float = 1.0,
    h0: float = 0.0,
    n: int = 200,
    rtol: float = 1e-10,
    atol: float = 1e-12,
) -> SimResult:
    """2-D projectile from (0, h0) until it returns to y=0.

    v0 [m/s], angle_deg [deg above horizontal], g [m/s^2], mass [kg], h0 [m].
    drag_coeff k [kg/m] gives quadratic drag F = -k |v| v (k = 0.5*rho*Cd*A); 0 disables it.
    State: x, y [m], vx, vy [m/s], sampled at n times in [0, flight time].
    meta: range [m], flight_time [s], max_height [m].
    """
    th = np.radians(angle_deg)

    def f(t, s):
        vx, vy = s[2], s[3]
        sp = np.hypot(vx, vy)
        return [vx, vy, -drag_coeff * sp * vx / mass, -g - drag_coeff * sp * vy / mass]

    def hit(t, s):
        return s[1]

    hit.terminal = True
    hit.direction = -1
    sol = solve_ivp(
        f, (0.0, 1e6), [0.0, h0, v0 * np.cos(th), v0 * np.sin(th)], method="DOP853",
        events=hit, dense_output=True, rtol=rtol, atol=atol,
    )
    if not sol.success or sol.t_events[0].size == 0:
        raise SolverError(f"projectile did not land: {sol.message}")
    t_land = float(sol.t_events[0][0])
    t = np.linspace(0.0, t_land, n)
    y = sol.sol(t)
    y[1, -1] = 0.0
    return SimResult(
        t=t,
        state={"x": y[0], "y": y[1], "vx": y[2], "vy": y[3]},
        units={"x": "meter", "y": "meter", "vx": "meter/second", "vy": "meter/second"},
        meta={
            "solver": "DOP853", "range": float(y[0, -1]), "flight_time": t_land,
            "max_height": float(y[1].max()),
        },
    )


def damped_oscillator(
    m: float, k: float, c: float, x0: float, v0: float, t_end: float, n: int
) -> SimResult:
    """m x'' + c x' + k x = 0. m [kg], k [N/m], c [N s/m], x0 [m], v0 [m/s], t_end [s].

    State: x [m], v [m/s] at n samples on [0, t_end].
    """
    return integrate(
        lambda t, y: {"x": y["v"], "v": -(k * y["x"] + c * y["v"]) / m},
        {"x": x0, "v": v0}, {"x": "meter", "v": "meter/second"}, (0.0, t_end), n,
    )


def pendulum(length: float, g: float, theta0: float, t_end: float, n: int,
             omega0: float = 0.0) -> SimResult:
    """Full nonlinear pendulum theta'' = -(g/L) sin(theta).

    length [m], g [m/s^2], theta0 [rad] (from vertical), omega0 [rad/s], t_end [s].
    State: theta [rad], omega [rad/s] at n samples on [0, t_end].
    """
    return integrate(
        lambda t, y: {"theta": y["omega"], "omega": -(g / length) * np.sin(y["theta"])},
        {"theta": theta0, "omega": omega0}, {"theta": "radian", "omega": "radian/second"},
        (0.0, t_end), n,
    )


def energy(result: SimResult, kind: str, *, m: float, k: float | None = None,
           length: float | None = None, g: float | None = None) -> np.ndarray:
    """Total mechanical energy [J] vs time for an oscillator or pendulum result.

    kind="oscillator": 0.5 m v^2 + 0.5 k x^2 (needs k [N/m]).
    kind="pendulum": 0.5 m L^2 omega^2 + m g L (1 - cos theta) (needs length [m], g [m/s^2]).
    """
    s = result.state
    if kind == "oscillator":
        if k is None:
            raise ValueError("oscillator energy needs k")
        return 0.5 * m * s["v"] ** 2 + 0.5 * k * s["x"] ** 2
    if kind == "pendulum":
        if length is None or g is None:
            raise ValueError("pendulum energy needs length and g")
        return 0.5 * m * length**2 * s["omega"] ** 2 + m * g * length * (1 - np.cos(s["theta"]))
    raise ValueError("kind must be 'oscillator' or 'pendulum'")
