"""Headless renders for the vision critic: painter's-algorithm flat shading with Pillow (no GPU, no OpenGL).

Good enough for "is this a dog? are the legs bending sensibly?", which is all the critic needs.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from .asset import png
from .meshops import vertex_colors

LIGHT = np.array([0.4, 0.8, 0.45]) / np.linalg.norm([0.4, 0.8, 0.45])

# (name, yaw degrees about +y, pitch degrees about +x)
VIEWS = [("front", 0, 0), ("side", 90, 0), ("three-quarter", 35, 25), ("top", 0, 89)]


def _rot(yaw: float, pitch: float) -> np.ndarray:
    y, p = np.radians(yaw), np.radians(pitch)
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    return rx @ ry


def render_view(positions: np.ndarray, faces: np.ndarray, colors: np.ndarray, yaw: float, pitch: float,
                size: int = 384, bg=(238, 241, 245), extent: float | None = None) -> Image.Image:
    p = positions.astype(np.float64) @ _rot(yaw, pitch).T
    centre = (p.min(0) + p.max(0)) / 2
    half = extent if extent is not None else float(np.abs(p - centre)[:, :2].max()) or 1.0
    scale = 0.45 * size / half
    xy = np.c_[(p[:, 0] - centre[0]) * scale + size / 2, size / 2 - (p[:, 1] - centre[1]) * scale]
    f = faces.astype(np.int64)
    n = np.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]])
    ln = np.linalg.norm(n, axis=1)
    n = n / np.where(ln > 0, ln, 1)[:, None]
    shade = 0.25 + 0.75 * np.abs(n @ LIGHT)  # two-sided: generated meshes aren't always consistently wound
    col = colors[f].mean(axis=1)
    col = np.where(col <= 0.0031308, col * 12.92, 1.055 * np.power(np.clip(col, 0, 1), 1 / 2.4) - 0.055)
    rgb = np.clip(col * shade[:, None] * 255, 0, 255).astype(np.uint8)
    order = np.argsort(p[f].mean(axis=1)[:, 2])  # far (smaller z: camera looks down -z) first
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)
    for i in order:
        if ln[i] <= 0:
            continue
        draw.polygon([tuple(xy[j]) for j in f[i]], fill=tuple(int(c) for c in rgb[i]))
    return img


def contact_sheet(images: list[Image.Image], labels: list[str], cols: int = 2) -> bytes:
    w, h = images[0].size
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for k, (im, label) in enumerate(zip(images, labels, strict=True)):
        x, y = (k % cols) * w, (k // cols) * h
        sheet.paste(im, (x, y))
        draw.text((x + 8, y + 6), label, fill=(20, 20, 20))
    return png(sheet)


def turntable(asset, positions: np.ndarray | None = None) -> bytes:
    """2x2 sheet of the standard views."""
    pos = asset.positions if positions is None else positions
    colors = vertex_colors(asset)
    return contact_sheet([render_view(pos, asset.indices, colors, yaw, pitch) for _, yaw, pitch in VIEWS],
                         [name for name, _, _ in VIEWS])


def frame_strip(asset, posed: list[np.ndarray], labels: list[str], yaw: float = 35, pitch: float = 15) -> bytes:
    """Same camera for every pose (fixed extent) so motion is visible between frames."""
    colors = vertex_colors(asset)
    allp = np.concatenate(posed) @ _rot(yaw, pitch).T
    extent = float(np.abs(allp - (allp.min(0) + allp.max(0)) / 2)[:, :2].max()) or 1.0
    return contact_sheet([render_view(p, asset.indices, colors, yaw, pitch, extent=extent) for p in posed], labels,
                         cols=min(len(posed), 4))
