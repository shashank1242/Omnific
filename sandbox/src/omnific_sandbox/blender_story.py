"""The .blend's story: the playback's effects (fireball, flash, shock front, smoke, glow, accretion disk, jet) as keyed
emission meshes and lights, objects hiding and showing at their appears_at / disappears_at, and a timeline marker at
the start of every phase. Values come per frame from the playback (computed from the solved states by the Data agent),
the same numbers the browser viewer draws (apps/web/components/Effects.tsx). Runs inside the Blender runner only."""

from __future__ import annotations

import math

import playback_math as pm

EFFECTS = "Omnific effects"
OFF = 0.003  # apps/web/components/Effects.tsx OFF: dimmer or smaller than this is not drawn


def to_blender(v):
    return (v[0], -v[2], v[1])


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def frame_of(times: list[float], t: float) -> int:
    """The first frame (1-based) at or after simulation time `t`."""
    for k, tm in enumerate(times, start=1):
        if tm >= t - 1e-12:
            return k
    return len(times)


def sample_effect(fx: dict, t: list[float], time: float) -> tuple[float, float, tuple[float, float, float]]:
    """Radius (scene units), brightness 0..1 and sRGB colour at `time`: apps/web/lib/effects.ts sampleEffect."""
    def at(values):
        v = pm.sample_scalar(values, t, time)
        return v if math.isfinite(v) else 0.0
    radius = max(0.0, at(fx["radius"]))
    level = min(1.0, max(0.0, at(fx["intensity"])))
    c = fx["color"]
    if c and isinstance(c[0], list):
        color = tuple(at([row[i] for row in c]) for i in range(3))
    else:
        color = tuple(float(x) for x in c)
    return radius, level, color  # type: ignore[return-value]


def effect_boxes(pb: dict, swept: dict) -> list[tuple[list[float], list[float]]]:
    """Blender-space boxes each effect fills at its largest, for the default camera."""
    out = []
    for fx in pb.get("effects") or []:
        r = max((v for v in fx["radius"] if v is not None and math.isfinite(v)), default=0.0)
        if r <= 0:
            continue
        if fx.get("anchor") and fx["anchor"] in swept:  # wherever the anchor goes, the effect reaches r further
            lo, hi = swept[fx["anchor"]]
        elif fx.get("position"):
            lo = hi = list(to_blender(fx["position"]))
        else:
            continue
        out.append(([lo[i] - r for i in range(3)], [hi[i] + r for i in range(3)]))
    return out


class Story:
    def __init__(self, run):
        self.run = run
        self.bpy = run.bpy
        self.pb = run.pb
        self.times: list[float] = run.times
        self.report: dict = {"effects": [], "phases": [], "timed": []}

    # -- meshes
    def _mesh(self, name: str, kind: str):
        import bmesh

        bpy = self.bpy
        me = bpy.data.meshes.new(name)
        if kind == "disk":  # a flat ring from r = 0.3 to 1 in the XY (ground) plane
            n, verts, faces = 128, [], []
            for k in range(n):
                a = 2 * math.pi * k / n
                verts += [(0.3 * math.cos(a), 0.3 * math.sin(a), 0.0), (math.cos(a), math.sin(a), 0.0)]
            for k in range(n):
                i, j = 2 * k, 2 * ((k + 1) % n)
                faces.append((i, i + 1, j + 1, j))
            me.from_pydata(verts, [], faces)
            return me
        bm = bmesh.new()
        if kind == "cone":  # narrow at the source (z = 0), widening to z = 1
            bmesh.ops.create_cone(bm, cap_ends=False, cap_tris=False, segments=24, radius1=0.03, radius2=0.12, depth=1.0)
            bmesh.ops.translate(bm, vec=(0.0, 0.0, 0.5), verts=bm.verts)
        else:
            bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=32, radius=1.0)
        bm.to_mesh(me)
        bm.free()
        for p in me.polygons:
            p.use_smooth = True
        return me

    # -- materials: emission fading into transparency, a glowing rim, or a smoke volume
    def _material(self, name: str, kind: str):
        bpy = self.bpy
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        for attr, value in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
            try:
                setattr(mat, attr, value)
            except (AttributeError, TypeError):
                pass
        if kind == "smoke":
            vol = nt.nodes.new("ShaderNodeVolumePrincipled")
            nt.links.new(vol.outputs[0], out.inputs["Volume"])
            return mat, {"color": vol.inputs["Color"], "level": vol.inputs["Density"], "level_k": 3.0}
        em = nt.nodes.new("ShaderNodeEmission")
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(tr.outputs[0], mix.inputs[1])
        nt.links.new(em.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs["Surface"])
        sockets = {"color": em.inputs["Color"], "strength": em.inputs["Strength"], "level": mix.inputs[0],
                   "level_k": 1.0}
        if kind == "shockwave":  # bright at the rim, nearly clear face-on: a thin shell
            rim = nt.nodes.new("ShaderNodeLayerWeight")
            rim.inputs["Blend"].default_value = 0.25
            mul = nt.nodes.new("ShaderNodeMath")
            mul.operation = "MULTIPLY"
            nt.links.new(rim.outputs["Facing"], mul.inputs[0])
            nt.links.new(mul.outputs[0], mix.inputs[0])
            sockets["level"] = mul.inputs[1]
        return mat, sockets

    def _object(self, name: str, mesh, mat, col):
        bpy = self.bpy
        obj = bpy.data.objects.new(name, mesh)
        mesh.materials.append(mat)
        col.objects.link(obj)
        return obj

    def _light(self, name: str, col):
        bpy = self.bpy
        data = bpy.data.lights.new(name, "POINT")
        obj = bpy.data.objects.new(name, data)
        col.objects.link(obj)
        return obj

    # -- one effect: its parts, positioned at the anchor (Copy Location) or a fixed place, then keyed per frame
    def effect(self, fx: dict, col) -> None:
        kind = fx["kind"]
        name = fx["name"]
        root = self.bpy.data.objects.new(f"effect:{name}", None)
        col.objects.link(root)
        anchor = self.run.objs.get(fx.get("anchor") or "")
        if anchor is not None:
            c = root.constraints.new("COPY_LOCATION")
            c.target = anchor
        elif fx.get("position"):
            root.location = to_blender(fx["position"])
        parts = []  # (object, sockets, stretch z, offset factor)
        mesh_kind = {"accretion_disk": "disk", "jet": "cone", "splash": "disk"}.get(kind, "sphere")
        mat_kind = {"shockwave": "shockwave", "smoke": "smoke"}.get(kind, "emit")
        if kind in ("explosion", "fire", "shockwave", "smoke", "accretion_disk", "flash", "glow", "splash"):
            mat, sockets = self._material(f"{name} material", mat_kind)
            obj = self._object(f"{name}", self._mesh(name, mesh_kind), mat, col)
            obj.parent = root
            stretch = 1.6 if kind == "fire" else 1.0
            core = {"flash": 0.15, "glow": 0.25}.get(kind, 1.0)  # a flash or glow is mostly light, with a small core
            if kind == "accretion_disk":
                obj.rotation_euler = (0.12, 0.0, 0.0)
            if kind == "splash":  # the crown's ring, thrown up into a shallow bowl of droplets
                tex = self.bpy.data.textures.new(f"{name} droplets", "CLOUDS")
                tex.noise_scale = 0.12
                mod = obj.modifiers.new("droplets", "DISPLACE")
                mod.texture = tex
                mod.strength = 0.25
                mod.direction = "Z"
            if kind in ("explosion", "fire"):  # turbulent surface
                tex = self.bpy.data.textures.new(f"{name} turbulence", "CLOUDS")
                tex.noise_scale = 0.35
                mod = obj.modifiers.new("turbulence", "DISPLACE")
                mod.texture = tex
                mod.strength = 0.3
                mod.mid_level = 0.5
            parts.append((obj, sockets, stretch, core))
        if kind == "jet":
            mat, sockets = self._material(f"{name} material", "emit")
            mesh = self._mesh(name, "cone")
            for sign in (1, -1):
                obj = self._object(f"{name} {'up' if sign > 0 else 'down'}", mesh, mat, col)
                obj.parent = root
                obj["omnific_sign"] = sign
                parts.append((obj, sockets, 1.0, 1.0))
        light = None
        if kind in ("explosion", "flash", "fire", "glow", "accretion_disk"):
            light = self._light(f"{name} light", col)
            light.parent = root
        power = {"explosion": 3000.0, "flash": 20000.0, "fire": 1500.0, "glow": 800.0, "accretion_disk": 1200.0}.get(kind, 0)
        t = self.pb["t"]
        for k, tm in enumerate(self.times, start=1):  # every frame, linear: the same values the viewer samples
            r, level, srgb = sample_effect(fx, t, tm)
            on = level > OFF and r > OFF
            lin = tuple(srgb_to_linear(c) for c in srgb) + (1.0,)
            for obj, sockets, stretch, core in parts:
                sign = obj.get("omnific_sign", 1)
                s = max(r * core, 1e-4)
                obj.scale = (s, s, s * stretch * sign)
                obj.keyframe_insert("scale", frame=k)
                obj.hide_render = obj.hide_viewport = not on
                obj.keyframe_insert("hide_render", frame=k)
                obj.keyframe_insert("hide_viewport", frame=k)
                sockets["color"].default_value = lin
                sockets["color"].keyframe_insert("default_value", frame=k)
                sockets["level"].default_value = level * sockets["level_k"]
                sockets["level"].keyframe_insert("default_value", frame=k)
                if "strength" in sockets:
                    sockets["strength"].default_value = 2.0 + 10.0 * level
                    sockets["strength"].keyframe_insert("default_value", frame=k)
            if light is not None:
                light.data.color = lin[:3]
                light.data.energy = power * level * max(1.0, (r * 3) ** 2) if on else 0.0
                light.data.shadow_soft_size = max(0.05, r * 0.5)
                for path in ("color", "energy", "shadow_soft_size"):
                    light.data.keyframe_insert(path, frame=k)
        for obj, _sockets, _stretch, _core in parts:
            self.run._linear(obj)
            self.run._linear(obj.active_material.node_tree)
        if light is not None:
            self.run._linear(light.data)
        self.report["effects"].append({"name": name, "kind": kind, "anchor": fx.get("anchor"),
                                       "parts": len(parts), "light": light is not None})

    def timed_entities(self) -> None:
        """Objects that appear or disappear (a bomb at detonation): hide_render / hide_viewport keyed at the change."""
        for e in self.pb.get("entities") or []:
            if e.get("appears_at") is None and e.get("disappears_at") is None:
                continue
            root = self.run.objs.get(e["name"])
            if root is None:
                continue
            frames = {1}
            if e.get("appears_at") is not None:
                frames.add(frame_of(self.times, e["appears_at"]))
            if e.get("disappears_at") is not None:
                frames.add(frame_of(self.times, e["disappears_at"]))
            for k in sorted(frames):
                tm = self.times[k - 1]
                shown = not ((e.get("appears_at") is not None and tm < e["appears_at"] - 1e-12)
                             or (e.get("disappears_at") is not None and tm >= e["disappears_at"] - 1e-12))
                for o in self.run.hierarchy(root):
                    o.hide_render = o.hide_viewport = not shown
                    o.keyframe_insert("hide_render", frame=k)
                    o.keyframe_insert("hide_viewport", frame=k)
            for o in self.run.hierarchy(root):  # a step, not a blend: a keyed boolean flips where the curve crosses 0.5
                for fc in self.run.fcurves(o):
                    if fc.data_path in ("hide_render", "hide_viewport"):
                        for kp in fc.keyframe_points:
                            kp.interpolation = "CONSTANT"
            self.report["timed"].append(e["name"])

    def markers(self) -> None:
        scene = self.bpy.context.scene
        for ph in self.pb.get("phases") or []:
            scene.timeline_markers.new(ph["name"], frame=frame_of(self.times, ph["start"]))
            self.report["phases"].append(ph["name"])

    def build(self) -> dict:
        bpy = self.bpy
        if self.pb.get("effects"):
            col = bpy.data.collections.new(EFFECTS)
            bpy.context.scene.collection.children.link(col)
            for fx in self.pb["effects"]:
                self.effect(fx, col)
        self.timed_entities()
        self.markers()
        bpy.context.scene.frame_set(1)
        return self.report
