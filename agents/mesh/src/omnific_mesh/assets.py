"""MVP procedural asset library (no GPU, no external assets).

Each generator returns (positions float32 (n,3), normals float32 (n,3), indices uint32 (m,))
with counter-clockwise winding.
"""

import numpy as np

# Ordered: first matching keyword wins. Keys are substrings of the lowercased entity kind.
KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("planet", "moon", "star", "sun", "earth"), "planet"),
    (("pizza", "disk", "disc", "plate", "coin", "wheel"), "disk"),
    (("plane", "ground", "floor", "surface", "table", "wall", "sheet"), "plane"),
    (("cylinder", "tube", "pipe", "rod", "can", "column", "pillar"), "cylinder"),
    (("sphere", "ball", "particle", "droplet", "bubble", "atom", "marble", "projectile"), "sphere"),
    (("box", "cube", "block", "brick", "crate"), "box"),
]


def asset_for_kind(kind: str) -> str:
    k = kind.lower()
    for words, asset in KEYWORDS:
        if any(w in k for w in words):
            return asset
    return "box"


def sphere(radius: float = 0.5, n_lat: int = 16, n_lon: int = 24):
    lat = np.linspace(0, np.pi, n_lat + 1)
    lon = np.linspace(0, 2 * np.pi, n_lon + 1)
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    nrm = np.stack([np.sin(la) * np.cos(lo), np.cos(la), np.sin(la) * np.sin(lo)], -1).reshape(-1, 3)
    idx = []
    for i in range(n_lat):
        for j in range(n_lon):
            a = i * (n_lon + 1) + j
            b = a + n_lon + 1
            idx += [a, a + 1, b, a + 1, b + 1, b]
    return (nrm * radius).astype(np.float32), nrm.astype(np.float32), np.array(idx, np.uint32)


def box(sx: float = 1.0, sy: float = 1.0, sz: float = 1.0):
    pos, nrm, idx = [], [], []
    half = np.array([sx, sy, sz]) / 2
    for axis in range(3):
        u, v = (axis + 1) % 3, (axis + 2) % 3
        for sign in (-1, 1):
            n = np.zeros(3)
            n[axis] = sign
            base = len(pos)
            for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = np.zeros(3)
                p[axis] = sign * half[axis]
                p[u] = su * half[u]
                p[v] = sv * half[v]
                pos.append(p)
                nrm.append(n)
            order = [0, 1, 2, 0, 2, 3] if sign > 0 else [0, 2, 1, 0, 3, 2]
            idx += [base + o for o in order]
    return np.array(pos, np.float32), np.array(nrm, np.float32), np.array(idx, np.uint32)


def cylinder(radius: float = 0.5, height: float = 1.0, n: int = 32):
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    c, s = np.cos(ang), np.sin(ang)
    h = height / 2
    pos, nrm, idx = [], [], []
    for k in range(n):
        pos += [(radius * c[k], -h, radius * s[k]), (radius * c[k], h, radius * s[k])]
        nrm += [(c[k], 0, s[k])] * 2
    for k in range(n):
        a, b = 2 * k, 2 * ((k + 1) % n)
        idx += [a, a + 1, b, b, a + 1, b + 1]
    for y, ny in ((h, 1.0), (-h, -1.0)):
        center = len(pos)
        pos.append((0, y, 0))
        nrm.append((0, ny, 0))
        ring = len(pos)
        for k in range(n):
            pos.append((radius * c[k], y, radius * s[k]))
            nrm.append((0, ny, 0))
        for k in range(n):
            a, b = ring + k, ring + (k + 1) % n
            idx += [center, b, a] if ny > 0 else [center, a, b]
    return np.array(pos, np.float32), np.array(nrm, np.float32), np.array(idx, np.uint32)


def plane(size: float = 10.0):
    h = size / 2
    pos = np.array([(-h, 0, -h), (-h, 0, h), (h, 0, h), (h, 0, -h)], np.float32)
    nrm = np.tile(np.array([0, 1, 0], np.float32), (4, 1))
    return pos, nrm, np.array([0, 1, 2, 0, 2, 3], np.uint32)


def disk():
    return cylinder(radius=0.5, height=0.05, n=48)


def planet():
    return sphere(radius=5.0, n_lat=24, n_lon=48)


GENERATORS = {
    "sphere": sphere, "box": box, "cylinder": cylinder, "plane": plane, "disk": disk,
    "planet": planet,
}
