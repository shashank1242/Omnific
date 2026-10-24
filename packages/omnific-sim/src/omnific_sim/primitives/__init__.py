"""Solver primitives for simulation code and evaluators."""

from .checks import conservation_drift, convergence_ratio, dimension_check, within_bounds
from .kinetics import arrhenius, reaction_network
from .mechanics import damped_oscillator, energy, pendulum, projectile
from .ode import SolverError, integrate
from .thermal import cell_centers, heat_1d, lumped_capacitance

__all__ = [
    "SolverError", "arrhenius", "cell_centers", "conservation_drift", "convergence_ratio",
    "damped_oscillator", "dimension_check", "energy", "heat_1d", "integrate",
    "lumped_capacitance", "pendulum", "projectile", "reaction_network", "within_bounds",
]
