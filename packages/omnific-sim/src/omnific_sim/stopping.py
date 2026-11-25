"""When a 'settle' run stops: once no state has changed for the settle window, plus that window as a buffer.

Applied after the solver (in the playback stage), never by generated code, so every simulation stops by the same rule.
"""

from __future__ import annotations

import numpy as np

from .units import ureg

REL_TOL = 1e-3  # a state is at rest when it stays within 0.1% of its range from its final value


def window_in(unit: str, seconds: float) -> float:
    """`seconds` expressed in the run's time unit."""
    return float(ureg.Quantity(seconds, "second").to(unit or "second").magnitude)


def last_change_time(t: np.ndarray, states: dict[str, np.ndarray], rel_tol: float = REL_TOL) -> float:
    """The last time any state was still away from its final value (t[0] when nothing ever changed)."""
    t = np.asarray(t, dtype=float)
    last = 0
    for values in states.values():
        v = np.asarray(values, dtype=float)
        if v.ndim == 0 or len(v) != len(t):
            continue
        flat = v.reshape(len(t), -1)
        finite = np.isfinite(flat)
        if not finite.any():
            continue
        if not finite[-1][finite.any(axis=0)].all():
            return float(t[-1])  # ends on NaN/inf: never call that rest (and never trim the bad tail away)
        masked = np.where(finite, flat, np.nan)
        live = finite.any(axis=0)
        # each component against its own range: a large constant column must not hide a small changing one
        span = np.zeros(flat.shape[1])
        span[live] = np.nanmax(masked[:, live], axis=0) - np.nanmin(masked[:, live], axis=0)
        dev = np.abs(np.where(finite, flat - flat[-1], 0.0))
        moving_rows = ((dev > rel_tol * span) & (span > 0)).any(axis=1)
        moving = np.nonzero(moving_rows)[0]
        if len(moving):
            last = max(last, int(moving[-1]) + 1)  # the sample after the last one still away from rest
    return float(t[min(last, len(t) - 1)])


def settle_stop(t: np.ndarray, states: dict[str, np.ndarray], window: float, rel_tol: float = REL_TOL) -> float | None:
    """The time a settle run stops at, or None when it should run to its end (it never rested for `window`)."""
    t = np.asarray(t, dtype=float)
    stop = last_change_time(t, states, rel_tol) + window
    return stop if stop < t[-1] else None
