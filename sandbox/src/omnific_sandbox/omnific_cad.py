"""Helper imported by generated CAD scripts (inside the CAD runner image): parts with colours, and joints as plain
data. Joint vocabulary after earthtojake/text-to-cad (MIT): revolute / slider / fastened, finite limits, the mate
graph a tree; rest pose = joint value 0 = the geometry as built.

A generated script defines:
    def build() -> dict[str, Part]      # label -> part(shape, color=...) or a bare build123d shape
    JOINTS = [revolute(...), ...]       # optional
"""

from __future__ import annotations

MOTIONS = ("none", "spin", "oscillate")


def part(shape, color: str = "#9aa0a6") -> dict:
    """A labelled part's geometry and its display colour (#rrggbb)."""
    return {"shape": shape, "color": color}


def _vec(v, what: str) -> list[float]:
    out = [float(x) for x in v]
    if len(out) != 3:
        raise ValueError(f"{what} must have 3 components, got {v!r}")
    return out


def revolute(name: str, parent: str, child: str, origin, axis, limits=(-180.0, 180.0), motion: str = "none",
             clip: str = "") -> dict:
    """`child` turns about `axis` through `origin` (metres), `limits` in degrees. motion: "spin" (continuous, e.g.
    a wheel or propeller), "oscillate" (back and forth within limits) or "none"; `clip` names the planned clip that
    plays it."""
    return {"name": name, "kind": "revolute", "parent": parent, "child": child, "origin": _vec(origin, "origin"),
            "axis": _vec(axis, "axis"), "limits": [float(limits[0]), float(limits[1])], "motion": motion,
            "clip": clip}


def slider(name: str, parent: str, child: str, origin, axis, limits=(0.0, 0.1), motion: str = "none",
           clip: str = "") -> dict:
    """`child` slides along `axis`, `limits` in metres. Sliders don't animate (clips rotate bones): motion "none"."""
    return {"name": name, "kind": "slider", "parent": parent, "child": child, "origin": _vec(origin, "origin"),
            "axis": _vec(axis, "axis"), "limits": [float(limits[0]), float(limits[1])], "motion": motion,
            "clip": clip}


def fastened(name: str, parent: str, child: str) -> dict:
    """A rigid attachment (0 degrees of freedom)."""
    return {"name": name, "kind": "fastened", "parent": parent, "child": child, "origin": [0.0, 0.0, 0.0],
            "axis": [0.0, 1.0, 0.0], "limits": [0.0, 0.0], "motion": "none", "clip": ""}
