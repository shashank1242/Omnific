import numpy as np
import pytest

from omnific_sim.primitives import (
    SolverError,
    arrhenius,
    cell_centers,
    conservation_drift,
    convergence_ratio,
    damped_oscillator,
    dimension_check,
    energy,
    heat_1d,
    integrate,
    lumped_capacitance,
    pendulum,
    projectile,
    reaction_network,
    within_bounds,
)


def test_projectile_range_no_drag():
    v0, ang, g = 30.0, 40.0, 9.81
    r = projectile(v0, ang, g=g)
    assert r.meta["range"] == pytest.approx(v0**2 * np.sin(np.radians(2 * ang)) / g, rel=1e-8)
    assert r.meta["flight_time"] == pytest.approx(2 * v0 * np.sin(np.radians(ang)) / g, rel=1e-8)


def test_projectile_drag_shortens_range():
    assert projectile(30, 40, drag_coeff=0.05, mass=1.0).meta["range"] < projectile(30, 40).meta["range"]


def test_pendulum_small_angle_period():
    L, g, th0 = 2.0, 9.81, 0.01
    T0 = 2 * np.pi * np.sqrt(L / g)
    r = pendulum(L, g, th0, 1.2 * T0, 24001)
    th, t = r.state["theta"], r.t
    i = np.argmax(th < 0)  # first crossing downward = quarter period
    tq = t[i - 1] + th[i - 1] / (th[i - 1] - th[i]) * (t[i] - t[i - 1])
    assert 4 * tq == pytest.approx(T0 * (1 + th0**2 / 16), rel=1e-6)


def test_pendulum_energy_conserved():
    r = pendulum(1.0, 9.81, 2.0, 20.0, 2000)
    assert conservation_drift(energy(r, "pendulum", m=1.0, length=1.0, g=9.81)) < 1e-6


def test_oscillator_energy_conserved():
    r = damped_oscillator(1.5, 40.0, 0.0, 0.3, 0.1, 30.0, 3000)
    assert conservation_drift(energy(r, "oscillator", m=1.5, k=40.0)) < 1e-6


def test_damped_oscillator_closed_form():
    m, k, c, x0, v0 = 1.0, 25.0, 1.2, 0.5, -0.3
    r = damped_oscillator(m, k, c, x0, v0, 10.0, 500)
    zeta_w = c / (2 * m)
    wd = np.sqrt(k / m - zeta_w**2)
    x = np.exp(-zeta_w * r.t) * (x0 * np.cos(wd * r.t) + (v0 + zeta_w * x0) / wd * np.sin(wd * r.t))
    np.testing.assert_allclose(r.state["x"], x, atol=1e-8)


def test_heat_dirichlet_fourier():
    L, alpha, t = 1.0, 0.01, 2.0
    r = heat_1d(L, 400, alpha, 1.0, 0.0, 0.0, t, 5)
    x = cell_centers(L, 400)
    exact = sum(4 / (n * np.pi) * np.sin(n * np.pi * x / L) * np.exp(-alpha * (n * np.pi / L) ** 2 * t)
                for n in range(1, 200, 2))
    np.testing.assert_allclose(r.state["T"][-1], exact, atol=1e-3)
    assert r.state["T"].shape == (5, 400)


def test_heat_robin_steady_state():
    L, k, h, Tinf, TL = 0.5, 2.0, 10.0, 300.0, 400.0
    r = heat_1d(L, 50, 1e-4, 350.0, TL, (h, Tinf, k), 1e5, 3)
    # steady: k (TL - Ts)/L = h (Ts - Tinf)
    Ts = (k * TL / L + h * Tinf) / (k / L + h)
    x = cell_centers(L, 50)
    np.testing.assert_allclose(r.state["T"][-1], TL + (Ts - TL) * x / L, rtol=1e-6)


def test_lumped_capacitance_exp():
    T0, Tinf, h, A, m, cp = 500.0, 300.0, 25.0, 0.1, 2.0, 400.0
    r = lumped_capacitance(T0, Tinf, h, A, m, cp, 3000.0, 100)
    exact = Tinf + (T0 - Tinf) * np.exp(-h * A * r.t / (m * cp))
    np.testing.assert_allclose(r.state["T"], exact, rtol=1e-8)


def test_lumped_radiation_cools_faster():
    a = lumped_capacitance(900, 300, 5, 0.1, 1, 500, 500, 10)
    b = lumped_capacitance(900, 300, 5, 0.1, 1, 500, 500, 10, emissivity=0.9)
    assert b.state["T"][-1] < a.state["T"][-1]


def test_first_order_kinetics():
    A, Ea, T = 1e3, 2e4, 350.0
    k = arrhenius(A, Ea, T)
    r = reaction_network({"A": 2.0, "B": 0.0},
                         [{"reactants": {"A": 1}, "products": {"B": 1}, "A": A, "Ea": Ea}],
                         T, 3.0 / k, 50)
    np.testing.assert_allclose(r.state["A"], 2.0 * np.exp(-k * r.t), rtol=1e-6, atol=1e-9)
    assert conservation_drift(r.state["A"] + r.state["B"]) < 1e-9


def test_kinetics_callable_T():
    r = reaction_network({"A": 1.0, "B": 0.0},
                         [{"reactants": {"A": 1}, "products": {"B": 1}, "A": 1.0, "Ea": 0.0}],
                         lambda t: 300.0, 2.0, 20)
    np.testing.assert_allclose(r.state["A"], np.exp(-r.t), rtol=1e-6)


def test_integrate_errors():
    with pytest.raises(ValueError):
        integrate(lambda t, y: {"a": 0.0}, {"a": 1.0}, {}, (0, 1), 5)
    with pytest.raises(SolverError):
        integrate(lambda t, y: {"a": y["a"] ** 2}, {"a": 1.0}, {"a": ""}, (0, 2), 5, method="RK45")


def test_checks():
    r = damped_oscillator(1, 1, 0, 1, 0, 5, 50)
    assert within_bounds(r, "x", -1.0001, 1.0001)
    assert not within_bounds(r, "x", 0.0, 1.0)
    c = damped_oscillator(1, 1, 0, 1, 0, 5, 51)
    f = damped_oscillator(1, 1, 0, 1, 0, 5, 501)
    assert convergence_ratio(c, f, "x") < 1e-6


def test_dimension_check():
    u = {"F": "newton", "m": "kg", "a": "m/s**2", "v": "m/s", "t": "s", "x": "m"}
    assert dimension_check("F = m*a", u)
    assert dimension_check("x = v*t + a*t**2/2", u)
    assert not dimension_check("F = m*v", u)
    assert not dimension_check("x = v*t + a*t", u)
    assert not dimension_check("x = sin(t)", u)
    with pytest.raises(KeyError):
        dimension_check("F = m*q", u)
