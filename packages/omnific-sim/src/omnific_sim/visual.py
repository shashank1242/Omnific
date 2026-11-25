"""Where things are in the picture, in metres: shared by the Data agent (scene scale), the Mesh agent (true sizes)
and the Evaluator (nothing sinks into the ground or through a ramp).

Frame: the viewer's. x and z horizontal, y up, ground at y = 0. A body's position states are its CENTRE; length
states bind to x, y, z in the order they appear in `bound_state` (the Data agent's track rule).
"""

from __future__ import annotations

import math

import numpy as np

from .spec import Entity, EntityGeometry, SimSpec
from .units import ureg


def is_length(unit: str) -> bool:
    try:
        u = ureg.parse_units(unit) if unit else None
    except Exception:  # noqa: BLE001 - unknown units are not lengths
        return False
    return u is not None and u.dimensionality == ureg.meter.dimensionality


def length_axes(entity: Entity, units: dict[str, str]) -> list[tuple[str, int]]:
    """(state, axis) for the entity's position states: its first three length states -> x, y, z."""
    names = [s for s in entity.bound_state if is_length(units.get(s, ""))]
    return list(zip(names[:3], range(3), strict=False))


def geometry(entity: Entity) -> tuple[EntityGeometry, list[float]] | None:
    """The entity's geometry and its validated size, or None when it has none (or an unusable one)."""
    g = entity.geometry
    if g is None:
        return None
    size = g.valid_size()
    return (g, size) if size is not None else None


def static_place(entity: Entity, units: dict[str, str]) -> list[float] | None:
    """Where a non-moving body's centre is (metres), or None if it moves or has no position."""
    found = geometry(entity)
    if found is None or entity.arm is not None or length_axes(entity, units):
        return None
    return found[0].valid_position()


def extent_peaks(spec: SimSpec, units: dict[str, str]) -> list[float]:
    """|coordinates| of every static body's bounding-box corners: they count toward the scene scale like motion."""
    peaks: list[float] = []
    for e in spec.entities:
        place = static_place(e, units)
        found = geometry(e)
        if place is None or found is None:
            continue
        half = [s / 2 for s in found[1]]
        peaks += [abs(place[i]) + half[i] for i in range(3)]
    return peaks


def centre_track(entity: Entity, values: dict[str, np.ndarray], units: dict[str, str], n: int) -> np.ndarray | None:
    """(n, 3) centre positions in metres over time for a translating body; None for articulated or unplaced ones."""
    if entity.arm is not None:
        return None
    axes = length_axes(entity, units)
    found = geometry(entity)
    base = (found[0].valid_position() if found else None) or [0.0, 0.0, 0.0]
    if not axes and found is None:
        return None
    out = np.tile(np.asarray(base, dtype=float), (n, 1))
    for name, axis in axes:
        v = values.get(name)
        if v is None:
            return None
        q = ureg.Quantity(np.asarray(v, dtype=float), units.get(name, "meter")).to("meter").magnitude
        out[:, axis] = np.asarray(q, dtype=float).reshape(-1)[:n]
    return out


# --- distances to solids -------------------------------------------------------------------------------------

def _local(points: np.ndarray, centre: list[float], yaw_deg: float) -> np.ndarray:
    """Points in the solid's own frame (translate, then undo its turn about y)."""
    a = math.radians(yaw_deg)
    c, s = math.cos(a), math.sin(a)
    p = points - np.asarray(centre, dtype=float)
    x, z = p[:, 0], p[:, 2]
    return np.stack([c * x - s * z, p[:, 1], s * x + c * z], axis=1)


def _box_sdf(p: np.ndarray, half: np.ndarray) -> np.ndarray:
    q = np.abs(p) - half
    outside = np.linalg.norm(np.maximum(q, 0.0), axis=1)
    inside = np.minimum(np.max(q, axis=1), 0.0)
    return outside + inside


def _wedge_sdf(p: np.ndarray, size: list[float]) -> np.ndarray:
    """Signed distance to a wedge centred at the origin: x in [-r/2, r/2], y in [-h/2, h/2], the slope from
    (-r/2, h/2) down to (r/2, -h/2), vertical back face at x = -r/2. Exact in the x-y section, extruded in z."""
    r, h, d = size
    tri = np.array([[-r / 2, -h / 2], [r / 2, -h / 2], [-r / 2, h / 2]])
    xy = p[:, :2]
    # distance to the triangle's edges
    dmin = np.full(len(p), np.inf)
    for i in range(3):
        a, b = tri[i], tri[(i + 1) % 3]
        ab = b - a
        t = np.clip(((xy - a) @ ab) / max(float(ab @ ab), 1e-18), 0.0, 1.0)
        proj = a + t[:, None] * ab
        dmin = np.minimum(dmin, np.linalg.norm(xy - proj, axis=1))
    # inside the triangle: left of every edge (counter-clockwise vertices)
    inside = np.ones(len(p), dtype=bool)
    for i in range(3):
        a, b = tri[i], tri[(i + 1) % 3]
        cross = (b[0] - a[0]) * (xy[:, 1] - a[1]) - (b[1] - a[1]) * (xy[:, 0] - a[0])
        inside &= cross >= 0
    d2 = np.where(inside, -dmin, dmin)
    dz = np.abs(p[:, 2]) - d / 2
    return np.where((d2 > 0) | (dz > 0),
                    np.sqrt(np.maximum(d2, 0.0) ** 2 + np.maximum(dz, 0.0) ** 2),
                    np.maximum(d2, dz))


def solid_distance(points: np.ndarray, g: EntityGeometry, size: list[float], centre: list[float]) -> np.ndarray | None:
    """Signed distance (metres, negative inside) from each point to a static solid; None for shapes we don't test."""
    p = _local(np.asarray(points, dtype=float), centre, g.yaw_deg)
    if g.shape == "wedge":
        return _wedge_sdf(p, size)
    if g.shape in ("box", "plane"):
        return _box_sdf(p, np.asarray(size, dtype=float) / 2)
    return None


# The scene is about 10 units across (data agent length_scale). Anything drawn smaller than this would be a speck next
# to the rest (a 3 m bomb beside a 60 km black hole), so it is enlarged uniformly to this size and the viewer says so.
MIN_VISIBLE_OBJECT = 0.3   # scene units, largest dimension of an object
MIN_VISIBLE_EFFECT = 0.6   # scene units, peak radius of an effect


def enlarge_factor(extent: float, minimum: float) -> float:
    """How much to enlarge something whose largest scene-unit extent is `extent` so it can be seen (1 = true size)."""
    if not math.isfinite(extent) or extent <= 0:
        return 1.0
    return max(1.0, minimum / extent)
