"""Mesh clean-up (postprocess stage) and the deterministic mesh checks for the generate and postprocess stages."""

from __future__ import annotations

import io

import numpy as np
from omnific_common.contracts import CheckResult
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .asset import Asset

# Longest side (scene units) of each procedural asset: a generated mesh replaces one at the same size, so scene
# layouts (and the viewer's camera framing) stay as they were.
NOMINAL_SIZE = {"sphere": 1.0, "box": 1.0, "cylinder": 1.0, "disk": 1.0, "plane": 10.0, "planet": 10.0}
MAX_TEXTURE = 1024
FLOATER_AREA = 0.01  # components below 1% of the total area are dropped
MAX_DEGENERATE = 0.005
MIN_TRIS = 100
HAUSDORFF_FRAC = 0.02
SIZE_TOL = 0.05


def check(name: str, ok: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=bool(ok), detail=detail)


def face_areas(a: Asset) -> np.ndarray:
    p, f = a.positions.astype(np.float64), a.indices.astype(np.int64)
    return 0.5 * np.linalg.norm(np.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]]), axis=1)


def face_components(a: Asset) -> np.ndarray:
    """Component label per face (faces sharing a vertex position are connected)."""
    f = a.indices.astype(np.int64)
    # weld by position so UV seams don't split a component
    _, weld = np.unique(np.round(a.positions, 6), axis=0, return_inverse=True)
    fv = weld.reshape(-1)[f]
    n = int(weld.max()) + 1
    rows = np.repeat(np.arange(len(f)), 3)
    g = coo_matrix((np.ones(len(rows)), (rows, fv.reshape(-1))), shape=(len(f), n)).tocsr()
    adj = g @ g.T
    _, labels = connected_components(adj, directed=False)
    return labels


def _subset(a: Asset, keep_faces: np.ndarray) -> Asset:
    f = a.indices[keep_faces]
    used = np.unique(f)
    remap = np.full(len(a.positions), -1, np.int64)
    remap[used] = np.arange(len(used))
    pick = lambda x: None if x is None else x[used]
    return Asset(positions=a.positions[used], indices=remap[f].astype(np.uint32), normals=pick(a.normals),
                 uvs=pick(a.uvs), colors=pick(a.colors), texture=a.texture, texture_mime=a.texture_mime,
                 base_color=a.base_color)


def vertex_colors(a: Asset) -> np.ndarray:
    """Per-vertex colour from the texture at each vertex UV (or existing colours / the base colour)."""
    if a.colors is not None:
        return a.colors.astype(np.float32)
    if a.texture is None or a.uvs is None:
        return np.tile(np.asarray(a.base_color[:3], np.float32), (len(a.positions), 1))
    from PIL import Image

    img = np.asarray(Image.open(io.BytesIO(a.texture)).convert("RGB"), np.float32) / 255.0
    h, w = img.shape[:2]
    u = np.mod(a.uvs[:, 0], 1.0)
    v = np.mod(a.uvs[:, 1], 1.0)  # glTF: v = 0 is the top row
    px = np.clip((u * (w - 1)).round().astype(int), 0, w - 1)
    py = np.clip((v * (h - 1)).round().astype(int), 0, h - 1)
    srgb = img[py, px]
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4).astype(np.float32)


# --- generate stage: is this a usable mesh at all? ------------------------------------------

def evaluate_generated(a: Asset) -> list[CheckResult]:
    out = [check("mesh.non_empty", a.n_tris >= MIN_TRIS, f"{a.n_tris} triangles (min {MIN_TRIS})")]
    if a.n_tris == 0:
        return out
    finite = bool(np.isfinite(a.positions).all())
    out.append(check("mesh.finite", finite, "all vertex positions finite" if finite else "NaN/inf vertices"))
    if not finite:
        return out
    areas = face_areas(a)
    total = float(areas.sum())
    degen = float((areas <= 1e-12 * max(total, 1e-30)).mean())
    out.append(check("mesh.degenerate_faces", degen <= MAX_DEGENERATE, f"{degen:.2%} zero-area faces"))
    labels = face_components(a)
    comp_area = np.bincount(labels, weights=areas)
    big = int((comp_area >= FLOATER_AREA * total).sum())
    out.append(check("mesh.components", big >= 1,
                     f"{len(comp_area)} components, {big} above {FLOATER_AREA:.0%} of the area"))
    has_look = a.texture is not None or a.colors is not None
    out.append(check("mesh.appearance", has_look, "texture or vertex colours present" if has_look
                     else "no texture and no vertex colours"))
    return out


# --- postprocess ------------------------------------------------------------------------------

def postprocess(a: Asset, scene_size: float, poly_budget: int) -> tuple[Asset, list[str]]:
    """Drop floaters and degenerate faces, fix winding, decimate to budget, centre and scale, shrink textures."""
    import trimesh

    notes: list[str] = []
    areas = face_areas(a)
    keep = areas > 1e-12 * max(float(areas.sum()), 1e-30)
    labels = face_components(a)
    comp_area = np.bincount(labels, weights=areas)
    keep &= comp_area[labels] >= FLOATER_AREA * areas.sum()
    if not keep.all():
        notes.append(f"removed {int((~keep).sum())} floater/degenerate faces")
    a = _subset(a, keep)

    tm = trimesh.Trimesh(a.positions, a.indices, process=False)
    trimesh.repair.fix_winding(tm)
    trimesh.repair.fix_inversion(tm)
    a.indices = np.asarray(tm.faces, np.uint32)

    if poly_budget and a.n_tris > poly_budget:
        import fast_simplification

        colors = vertex_colors(a)
        pts, faces = fast_simplification.simplify(a.positions.astype(np.float32), a.indices.astype(np.int64),
                                                  target_count=int(poly_budget))
        _, nearest = cKDTree(a.positions).query(pts)
        notes.append(f"decimated {a.n_tris} -> {len(faces)} triangles (texture baked to vertex colours)")
        a = Asset(positions=pts.astype(np.float32), indices=faces.astype(np.uint32), colors=colors[nearest],
                  base_color=(1.0, 1.0, 1.0, 1.0))

    lo, hi = a.bbox()
    longest = float((hi - lo).max())
    if longest <= 0:
        raise ValueError("mesh has zero extent")
    k = scene_size / longest
    a.positions = ((a.positions - (lo + hi) / 2) * k).astype(np.float32)
    a.compute_normals()

    if a.texture is not None:
        from PIL import Image

        from .asset import png

        img = Image.open(io.BytesIO(a.texture))
        if max(img.size) > MAX_TEXTURE:
            img.thumbnail((MAX_TEXTURE, MAX_TEXTURE))
            a.texture, a.texture_mime = png(img), "image/png"
            notes.append(f"texture resized to {img.size[0]}x{img.size[1]}")
    return a, notes


def _normalised(a: Asset) -> np.ndarray:
    lo, hi = a.bbox()
    return (a.positions - (lo + hi) / 2) / max(float((hi - lo).max()), 1e-12)


def hausdorff(a: Asset, b: Asset, n: int = 3000, seed: int = 0) -> float:
    """Symmetric Hausdorff estimate between unit-normalised surfaces (fraction of the longest side)."""
    import trimesh

    rng = np.random.default_rng(seed)
    ta = trimesh.Trimesh(_normalised(a), a.indices, process=False)
    tb = trimesh.Trimesh(_normalised(b), b.indices, process=False)
    pa = np.concatenate([ta.sample(n, seed=int(rng.integers(1 << 31))), ta.vertices[:: max(1, len(ta.vertices) // n)]])
    pb = np.concatenate([tb.sample(n, seed=int(rng.integers(1 << 31))), tb.vertices[:: max(1, len(tb.vertices) // n)]])
    _, d_ab, _ = trimesh.proximity.closest_point(tb, pa)
    _, d_ba, _ = trimesh.proximity.closest_point(ta, pb)
    return float(max(d_ab.max(), d_ba.max()))


def evaluate_postprocessed(before: Asset, after: Asset, scene_size: float, poly_budget: int) -> list[CheckResult]:
    import trimesh

    out = [check("post.poly_budget", not poly_budget or after.n_tris <= poly_budget,
                 f"{after.n_tris} triangles (budget {poly_budget or 'none'})")]
    h = hausdorff(before, after)
    out.append(check("post.shape_preserved", h <= HAUSDORFF_FRAC,
                     f"Hausdorff distance {h:.2%} of size (max {HAUSDORFF_FRAC:.0%})"))
    tm = trimesh.Trimesh(after.positions, after.indices, process=False)
    out.append(check("post.consistent_winding", tm.is_winding_consistent,
                     "winding consistent" if tm.is_winding_consistent else "inconsistent winding"))
    lo, hi = after.bbox()
    longest = float((hi - lo).max())
    out.append(check("post.size", abs(longest - scene_size) <= SIZE_TOL * scene_size,
                     f"longest side {longest:.3f} (target {scene_size:.3f} ±{SIZE_TOL:.0%})"))
    centred = float(np.abs((lo + hi) / 2).max()) <= 1e-3 * scene_size
    out.append(check("post.centred", centred, "bbox centred on the origin" if centred else "not centred"))
    if after.uvs is not None:
        ok = bool(np.isfinite(after.uvs).all())
        out.append(check("post.uvs", ok, "UVs finite" if ok else "NaN/inf UVs"))
    if after.texture is not None:
        from PIL import Image

        size = Image.open(io.BytesIO(after.texture)).size
        out.append(check("post.texture_size", max(size) <= MAX_TEXTURE, f"texture {size[0]}x{size[1]}"))
    return out
