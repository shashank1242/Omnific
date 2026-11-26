"""Which way a model faces: turn a reused or generated model so its front points along +X and its top along +Y.

Library and generated models arrive in whatever orientation their author left them (a rifle lying along x, another
along z, a car on its side). The scene can only aim an object ("the rifle faces the barrel") if every model shares one
convention, so we ask a vision model, on three labelled axis views, which axis is the model's front and which its top,
and store the 90-degree turn that maps them to +X / +Y. The turn goes on the model's `mesh:<entity>` node, so the
geometry, its bones and clips are untouched.

Fails open: if the vision call is unavailable or the answer is inconsistent, the model keeps its own orientation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

import numpy as np
from omnific_common.contracts import CheckResult
from omnific_common.llm import LLM, Role
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from .asset import png
from .meshops import vertex_colors
from .render import _rot, render_view

log = logging.getLogger(__name__)

Axis = Literal["+x", "-x", "+y", "-y", "+z", "-z"]
AXES: dict[str, np.ndarray] = {f"{s}{n}": sign * np.eye(3)[i] for i, n in enumerate("xyz")
                               for s, sign in (("+", 1.0), ("-", -1.0))}

# (label, yaw, pitch) for render.render_view: front (X right, Y up), top (X right, -Z up), side (Z right... computed)
VIEWS = [("view A", 0.0, 0.0), ("view B", 0.0, 90.0), ("view C", 90.0, 0.0)]


class Facing(BaseModel):
    front: Axis | Literal["none"] = Field(
        description="Axis the model's front points along: the muzzle of a gun, the nose of a vehicle or plane, the "
        "face of a person or animal, the screen of a monitor, the business end of a tool. 'none' only for things "
        "with no front (a ball, a barrel, a box, a tree)")
    up: Axis = Field(description="Axis pointing from the model's bottom to its top as it normally stands or is held "
                                 "(a rifle held level: from the trigger guard up to the sights)")
    reason: str = Field(description="One short sentence: what you saw that decides it")


INSTRUCTIONS = """\
You look at three flat-shaded views of ONE 3D model. Each view has its axes printed at the image edges: the label at an
edge is the axis direction pointing toward that edge (for example "+x" at the right edge means +x points right in that
view). The axis pointing straight at or away from you is printed under the view title.
Answer which axis the model's FRONT points along and which axis points UP (from its bottom to its top as it normally
stands or is held). Read the directions off the edge labels; do not assume +y is up. The two axes must differ."""


def _edge_labels(yaw: float, pitch: float) -> dict[str, str]:
    """Which world axis points to each image edge (right, left, top, bottom) and toward the viewer."""
    r = _rot(yaw, pitch)
    out = {}
    for edge, screen in (("right", (0, 1.0)), ("left", (0, -1.0)), ("top", (1, 1.0)), ("bottom", (1, -1.0)),
                         ("toward you", (2, 1.0))):
        comp, sign = screen
        best = max(AXES.items(), key=lambda kv: sign * (r @ kv[1])[comp])
        out[edge] = best[0]
    return out


def axis_sheet(asset, size: int = 384) -> bytes:
    """Three labelled orthographic views side by side, with the world axis printed at each image edge."""
    colors = vertex_colors(asset)
    pad = 28
    sheet = Image.new("RGB", (len(VIEWS) * (size + pad), size + 2 * pad), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for k, (title, yaw, pitch) in enumerate(VIEWS):
        im = render_view(asset.positions, asset.indices, colors, yaw, pitch, size=size)
        x0, y0 = k * (size + pad) + pad // 2, pad
        sheet.paste(im, (x0, y0))
        lab = _edge_labels(yaw, pitch)
        draw.text((x0 + 4, 4), f"{title} ({lab['toward you']} toward you)", fill=(20, 20, 20))
        draw.text((x0 + size - 22, y0 + size // 2), lab["right"], fill=(200, 30, 30))
        draw.text((x0 + 4, y0 + size // 2), lab["left"], fill=(200, 30, 30))
        draw.text((x0 + size // 2 - 8, y0 + 4), lab["top"], fill=(30, 30, 200))
        draw.text((x0 + size // 2 - 8, y0 + size - 14), lab["bottom"], fill=(30, 30, 200))
    return png(sheet)


def _quat(m: np.ndarray) -> list[float]:
    """Rotation matrix -> glTF quaternion [x, y, z, w]."""
    t = np.trace(m)
    if t > 0:
        s = 2.0 * np.sqrt(t + 1.0)
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    else:
        i = int(np.argmax(np.diag(m)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = 2.0 * np.sqrt(1.0 + m[i, i] - m[j, j] - m[k, k])
        q = [0.0, 0.0, 0.0, (m[k, j] - m[j, k]) / s]
        q[i] = 0.25 * s
        q[j] = (m[j, i] + m[i, j]) / s
        q[k] = (m[k, i] + m[i, k]) / s
    q = np.asarray(q, float)
    q /= np.linalg.norm(q)
    return [round(float(v), 9) + 0.0 for v in q]


def canonical_turn(front: str, up: str) -> list[float] | None:
    """The quaternion that maps the model's `front` axis to +X and `up` to +Y (front 'none': up only, smallest turn).
    [] for no turn at all; None when the answer is inconsistent (front and up on one axis)."""
    u = AXES[up]
    if front == "none":
        if up == "+y":
            return []
        # any horizontal front: keep +x if it is horizontal, else +z (the turn is a 90 or 180 degree tip)
        f = AXES["+x"] if abs(u[0]) < 0.5 else AXES["+z"] if up == "-x" else AXES["-z"]
    else:
        f = AXES[front]
    if abs(float(f @ u)) > 1e-9:
        return None
    m = np.vstack([f, u, np.cross(f, u)])  # rows: m @ f = +x, m @ u = +y
    if np.allclose(m, np.eye(3)):
        return []
    return _quat(m)


async def facing(llm: LLM, asset, what: str) -> tuple[list[float], CheckResult]:
    """Ask which way the model faces; returns (turn quaternion or [], report)."""
    try:
        sheet = await asyncio.to_thread(axis_sheet, asset)  # rendering is CPU work: keep the worker responsive
        v = await llm.structured_images(Role.GENERAL, INSTRUCTIONS, f"The model is: {what}.",
                                        [(sheet, "image/png")], Facing)
    except Exception as e:  # noqa: BLE001 - orientation is a refinement: a flaky call keeps the model as it is
        log.warning("facing unavailable: %s", e)
        return [], CheckResult(name="orient.facing", passed=True,
                               detail=f"facing unavailable ({type(e).__name__}); model kept as authored")
    turn = canonical_turn(v.front, v.up)
    if turn is None:
        return [], CheckResult(name="orient.facing", passed=True,
                               detail=f"inconsistent answer (front {v.front}, up {v.up}); model kept as authored")
    did = "already faces +x, up +y" if not turn else f"turned so front {v.front} -> +x, up {v.up} -> +y"
    return turn, CheckResult(name="orient.facing", passed=True, detail=f"{did}: {v.reason}")
