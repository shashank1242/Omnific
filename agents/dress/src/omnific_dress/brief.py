"""The scene brief the dress writer reads: setting, and per entity its kind, asset, size, rest position and the space
it moves through, all in Blender axes (z up) and scene units. Approximate on purpose: at run time the scripts get
exact bounds from omnific.motion_bounds() / entity_bounds()."""

from __future__ import annotations

import math

from omnific_common.contracts import DressPropOption, SceneReport
from omnific_sim.spec import SimSpec


def to_blender(v) -> tuple[float, float, float]:
    return (v[0], -v[2], v[1])


def _fmt(v) -> str:
    return "(" + ", ".join(f"{x:.2f}" for x in v) + ")"


def _entity_size(gltf: dict, root: int) -> tuple[float, float, float] | None:
    """Rough extent of an entity's meshes (POSITION accessor bounds x node scale), in viewer axes."""
    nodes, meshes, accessors = gltf.get("nodes", []), gltf.get("meshes", []), gltf.get("accessors", [])
    lo, hi, stack = [math.inf] * 3, [-math.inf] * 3, [(root, (1.0, 1.0, 1.0))]
    while stack:
        i, parent_scale = stack.pop()
        if not 0 <= i < len(nodes):
            continue
        n = nodes[i]
        s = tuple(parent_scale[k] * n.get("scale", [1, 1, 1])[k] for k in range(3))
        if "mesh" in n and 0 <= n["mesh"] < len(meshes):
            for p in meshes[n["mesh"]].get("primitives", []):
                acc = accessors[p["attributes"]["POSITION"]] if "POSITION" in p.get("attributes", {}) else {}
                if "min" in acc and "max" in acc:
                    for k in range(3):
                        lo[k] = min(lo[k], acc["min"][k] * s[k])
                        hi[k] = max(hi[k], acc["max"][k] * s[k])
        stack.extend((c, s) for c in n.get("children", []))
    if lo[0] == math.inf:
        return None
    return tuple(hi[k] - lo[k] for k in range(3))


def motion_ranges(playback: dict, gltf: dict) -> dict[str, tuple[list[float], list[float]]]:
    """Per entity, the (lo, hi) of its origin over the run in viewer axes: position tracks' ranges, articulated
    entities' pivot +- arm length, everything else its rest translation."""
    nodes = gltf.get("nodes", [])
    rest = {n.get("name"): n.get("translation", [0.0, 0.0, 0.0]) for n in nodes}
    out: dict[str, tuple[list[float], list[float]]] = {}
    for e in playback.get("entities") or []:
        base = list(rest.get(e["name"], [0.0, 0.0, 0.0]))
        lo, hi = base[:], base[:]
        if e.get("arm"):
            pv = e.get("pivot") or [0.0, 0.0, 0.0]
            r = math.sqrt(sum(a * a for a in e["arm"]))
            lo, hi = [pv[k] - r for k in range(3)], [pv[k] + r for k in range(3)]
        for tr in playback.get("tracks") or []:
            if tr["entity"] != e["name"] or not tr["channel"].startswith("position_"):
                continue
            k = "xyz".index(tr["channel"][-1])
            a, b = tr["min"] * tr["scale"], tr["max"] * tr["scale"]
            lo[k], hi[k] = min(a, b), max(a, b)
        out[e["name"]] = (lo, hi)
    return out


def _blender_box(lo, hi):
    a, b = to_blender(lo), to_blender(hi)
    return tuple(min(a[k], b[k]) for k in range(3)), tuple(max(a[k], b[k]) for k in range(3))


def props_section(options: list[DressPropOption]) -> str:
    if not options:
        return "PROPS: none available (build scenery from primitives)."
    lines = ["PROPS (ready-made CC0 models for omnific.prop; real size x, y, z in metres):"]
    for o in options:
        size = " x ".join(f"{d:.2f}" for d in o.size) if o.size else "size unknown"
        tags = f" [{', '.join(o.tags[:5])}]" if o.tags else ""
        lines.append(f"- {o.id}: {o.name}, {size} m{tags}")
    return "\n".join(lines)


def build_brief(prompt: str, spec: SimSpec, playback: dict, gltf: dict, report: SceneReport | None,
                prop_options: list[DressPropOption] | None = None) -> str:
    env = playback.get("environment") or {}
    lines = [f"PROMPT: {prompt}", f"TITLE: {spec.title}", f"FIDELITY: {spec.fidelity.value}"]
    if env:
        lines.append(f"SETTING: {env.get('name', '')}: {env.get('description', '')} (kind {env.get('kind')}, "
                     f"ground {env.get('ground')} {env.get('ground_material', '')}, lighting {env.get('lighting')}, "
                     f"sky {env.get('sky_color')}, ground colour {env.get('ground_color')})")
    else:
        lines.append("SETTING: a neutral studio (the viewer draws a light grey backdrop and ground)")
    scale = playback.get("length_scale")
    if scale:
        lines.append(f"SCALE: 1 metre = {scale:.3g} scene units")
    by_report = {r.entity: r for r in (report.entities if report else [])}
    colour_driven = {tr["entity"]: tr["state"] for tr in playback.get("tracks") or []
                     if tr["channel"] in ("color", "color_field")}
    roots = {n.get("name"): i for i, n in enumerate(gltf.get("nodes", []))}
    ranges = motion_ranges(playback, gltf)
    lines.append("ENTITIES (Blender axes, z up, scene units):")
    all_lo, all_hi = [math.inf] * 3, [-math.inf] * 3
    for ent in spec.entities:
        parts = [f"- {ent.name}: a {ent.kind}"]
        if ent.look:
            parts.append(f"(the user wants it to look like: {ent.look})")
        rep = by_report.get(ent.name)
        if rep is not None and rep.route_used != "procedural":
            src = f"{rep.route_used} model"
            if rep.attribution:
                src += f" '{rep.attribution.title}' by {rep.attribution.author} ({rep.attribution.license})"
            parts.append(f"[{src}: keep its own colours]")
        size = _entity_size(gltf, roots[ent.name]) if ent.name in roots else None
        if size:
            parts.append(f"size {_fmt(to_blender(size)).replace('-', '')}")
        if ent.name in ranges:
            lo, hi = _blender_box(*ranges[ent.name])
            parts.append(f"moves within {_fmt(lo)}..{_fmt(hi)}" if lo != hi else f"rests at {_fmt(lo)}")
            pad = [s / 2 for s in to_blender(size)] if size else [0.0, 0.0, 0.0]
            for k in range(3):
                all_lo[k] = min(all_lo[k], lo[k] - abs(pad[k]))
                all_hi[k] = max(all_hi[k], hi[k] + abs(pad[k]))
        if ent.name in colour_driven:
            parts.append(f"[colour shows {colour_driven[ent.name]}: set only metallic/roughness]")
        lines.append(" ".join(parts))
    if all_lo[0] != math.inf:
        lines.append(f"ALL MOTION (approx.): {_fmt(all_lo)}..{_fmt(all_hi)}; keep scenery outside this box "
                     "(except below the lowest point)")
    lines.append(props_section(prop_options or []))
    return "\n".join(lines)
