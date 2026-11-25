"""What happens, not just what moves: effects (fireball, flash, shock front...), phases, objects that appear or
disappear, and the headline outputs. Everything is precomputed per frame here so the browser and the .blend draw the
same numbers (docs/playback-format.md, "Effects, phases and key outputs")."""

import math

import numpy as np

# A blast seen from inside the scene should be white-hot at its core; anything cooler than this is drawn as a dim ember.
MIN_GLOW_K = 800.0


def blackbody(kelvin: float) -> tuple[float, float, float]:
    """Approximate sRGB colour (0..1) of a black body, after Tanner Helland's fit (1000-40000 K). Cooler bodies fade
    to a dim deep red; non-finite or non-positive temperatures are black."""
    if not math.isfinite(kelvin) or kelvin <= 0:
        return (0.0, 0.0, 0.0)
    t = min(kelvin, 40000.0) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(max(t, 1.0)) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
        b = 255.0
    rgb = [min(255.0, max(0.0, c)) / 255.0 for c in (r, g, b)]
    if kelvin < 1000.0:  # below the fit: the same deep red, dimming toward black as it cools
        k = max(0.0, (kelvin - MIN_GLOW_K / 2) / (1000.0 - MIN_GLOW_K / 2))
        rgb = [c * k for c in rgb]
    return (round(rgb[0], 4), round(rgb[1], 4), round(rgb[2], 4))


def hex_rgb(h: str) -> tuple[float, float, float]:
    try:
        return tuple(round(int(h[i:i + 2], 16) / 255.0, 4) for i in (1, 3, 5))  # type: ignore[return-value]
    except (ValueError, IndexError):
        return (1.0, 0.6, 0.2)


def _series(arrays: dict[str, np.ndarray], name: str, n: int) -> np.ndarray | None:
    v = arrays.get(name)
    if v is None or v.ndim != 1 or len(v) != n:
        return None
    return np.asarray(v, dtype=float)


def _round(values, digits: int = 5) -> list:
    return [None if not math.isfinite(float(v)) else round(float(v), digits) for v in values]


def effects_doc(spec, arrays: dict[str, np.ndarray], n: int, ls: float) -> list[dict]:
    """Per effect, per frame: radius in scene units, intensity 0..1 and sRGB colour."""
    out = []
    for fx in getattr(spec, "effects", []) or []:
        radius = _series(arrays, fx.radius_state, n) if fx.radius_state else None
        r = (np.abs(radius) * ls) if radius is not None else np.full(n, max(fx.size, 0.0) * ls)
        r = np.where(np.isfinite(r), r, 0.0)
        level = _series(arrays, fx.intensity_state, n) if fx.intensity_state else None
        if level is not None:
            peak = float(np.nanmax(np.abs(level))) if np.isfinite(level).any() else 0.0
            inten = np.clip(np.nan_to_num(np.abs(level) / peak, nan=0.0), 0.0, 1.0) if peak > 0 else np.zeros(n)
        else:
            inten = np.ones(n)
        temp = _series(arrays, fx.temperature_state, n) if fx.temperature_state else None
        if temp is not None:
            colors = [list(blackbody(float(k))) for k in temp]
        else:
            colors = [list(hex_rgb(fx.color))] * n
        pos = [float(c) * ls for c in fx.position] if not fx.anchor and len(fx.position) == 3 else None
        out.append({
            "name": fx.name, "kind": fx.kind, "anchor": fx.anchor or None, "position": pos,
            "description": fx.description, "radius": _round(r), "intensity": _round(inten, 4),
            "color": colors if temp is not None else colors[0],
        })
    return out


def phases_doc(spec, t_first: float, t_last: float) -> list[dict]:
    """Named stages, each lasting until the next starts; the last ends with the run. Stages after the run (cut short
    by a settle stop) are dropped."""
    phases = sorted(getattr(spec, "phases", []) or [], key=lambda p: p.start)
    out = []
    for i, ph in enumerate(phases):
        if ph.start >= t_last:
            break
        end = phases[i + 1].start if i + 1 < len(phases) else t_last
        out.append({"name": ph.name, "start": max(ph.start, t_first), "end": min(end, t_last),
                    "description": ph.description})
    return out


def outputs_doc(spec, t: np.ndarray, state: dict[str, np.ndarray], units: dict[str, str]) -> list[dict]:
    """The headline numbers, from the full-resolution solved values (not the resampled playback)."""
    out = []
    for o in getattr(spec, "outputs", []) or []:
        v = state.get(o.state)
        if v is None:
            continue
        v = np.asarray(v, dtype=float)
        if v.ndim != 1 or len(v) != len(t) or not np.isfinite(v).any():
            continue
        ok = np.isfinite(v)
        unit = units.get(o.state, "")
        at = None
        if o.measure == "max":
            i = int(np.nanargmax(np.where(ok, v, -np.inf)))
            value, at = v[i], t[i]
        elif o.measure == "min":
            i = int(np.nanargmin(np.where(ok, v, np.inf)))
            value, at = v[i], t[i]
        elif o.measure == "time_of_max":
            i = int(np.nanargmax(np.where(ok, v, -np.inf)))
            value, at, unit = t[i], t[i], spec.time.unit
        elif o.measure == "time_of_min":
            i = int(np.nanargmin(np.where(ok, v, np.inf)))
            value, at, unit = t[i], t[i], spec.time.unit
        elif o.measure == "initial":
            i = int(np.argmax(ok))
            value, at = v[i], t[i]
        elif o.measure == "final":
            i = len(v) - 1 - int(np.argmax(ok[::-1]))
            value, at = v[i], t[i]
        else:  # mean over time, weighted by the (possibly uneven) sample spacing
            tt, vv = t[ok], v[ok]
            area = float(np.sum((vv[1:] + vv[:-1]) * np.diff(tt)) / 2)  # trapezoid rule (any NumPy version)
            value = area / (tt[-1] - tt[0]) if len(tt) > 1 and tt[-1] > tt[0] else float(vv[0])
        out.append({"label": o.label, "state": o.state, "measure": o.measure, "value": float(value), "unit": unit,
                    "at": None if at is None else float(at), "why": o.why})
    return out


def visibility(entity) -> dict:
    """appears_at / disappears_at when set (absent otherwise, so older playbacks are unchanged)."""
    return {k: float(v) for k in ("appears_at", "disappears_at") if (v := getattr(entity, k, None)) is not None}
