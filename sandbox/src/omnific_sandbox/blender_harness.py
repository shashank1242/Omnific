"""Blender runner harness. Executed INSIDE the isolated Blender runner (container, or subprocess in local dev), never
in a worker: in `dress` mode it executes LLM-written Blender scripts.

Usage: python blender_harness.py <input_dir> <output_dir>
  input_dir:  job.json      {"mode": "dress"|"export", "entities": [...], "fps": 30, "stills": 4, "still_size": [w, h]}
              scene.gltf    SceneBuild's scene (self-contained, data URIs)
              playback.json the evaluated run (the only source of motion)
              scripts/*.py  dress mode: the Look / Scenery / Lighting scripts, run in name order
              props/<id>.glb ready-made CC0 props the scenery may place with omnific.prop (job.json "props")
  output_dir: result.zip =  report.json, scene.blend, and in dress mode scene.gltf (dressed) and stills/*.png
Exit 0 = the harness itself worked (script errors and failed checks are reported in report.json, not as a crash).
Next to this file at runtime: harness.py (result emission), playback_math.py, gltf_merge.py, blender_safety.py.
"""

from __future__ import annotations

import copy
import io
import json
import math
import sys
import time
import traceback
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_safety
import blender_story
import gltf_merge
import playback_math as pm
from harness import _excepthook, emit_mode, emit_result, fail

STAGE = "omnific_stage"      # viewer-equivalent ground/lights: in the .blend, never exported (the viewer draws its own)
SCENERY = gltf_merge.SCENERY_ROOT
CAMERA = "Omnific Camera"
FIDELITY_TOL = 1e-3          # scene units
FRAMING_MIN = 0.95           # fraction of each moving entity's bbox corners inside the default camera's view
CHECK_FRAMES = 7
LIGHT_RIGS = {  # apps/web/components/Viewport.tsx LIGHT_RIGS (sun position in viewer axes, colour, intensity)
    "day": ((8, 14, 6), "fff6e8", 2.4), "dusk": ((12, 3, -4), "ffb070", 1.8), "night": ((-6, 10, 4), "9fb4ff", 0.6),
    "studio": ((6, 10, 8), "ffffff", 2.0), "space": ((14, 6, 10), "ffffff", 3.0),
}


# --- axes: the viewer/glTF is y-up, Blender is z-up (the glTF importer's own conversion) ---------------------------

def to_blender(v):
    return (v[0], -v[2], v[1])


def from_blender(v):
    return (v[0], v[2], -v[1])


def quat_xyz(e):
    """three.js Euler 'XYZ' -> quaternion (w, x, y, z)."""
    c1, c2, c3 = (math.cos(a / 2) for a in e)
    s1, s2, s3 = (math.sin(a / 2) for a in e)
    return (c1 * c2 * c3 - s1 * s2 * s3, s1 * c2 * c3 + c1 * s2 * s3, c1 * s2 * c3 - s1 * c2 * s3,
            c1 * c2 * s3 + s1 * s2 * c3)


def euler_xyz(q):
    """quaternion (w, x, y, z) -> three.js Euler 'XYZ' (Euler.setFromQuaternion)."""
    w, x, y, z = q
    m11, m12, m13 = 1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)
    m22, m23 = 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)
    m32, m33 = 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)
    ry = math.asin(max(-1.0, min(1.0, m13)))
    if abs(m13) < 0.9999999:
        return (math.atan2(-m23, m33), ry, math.atan2(-m12, m11))
    return (math.atan2(m32, m22), ry, 0.0)


def q_to_blender(q):
    w, x, y, z = q
    return (w, x, -z, y)


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgb(h: str, default=(0.5, 0.5, 0.5)):
    try:
        h = h.strip().lstrip("#")
        return tuple(srgb_to_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return default


# --- Blender side -------------------------------------------------------------------------------------------------

class Run:
    def __init__(self, in_dir: Path, out_dir: Path):
        import bpy

        self.bpy = bpy
        self.in_dir, self.out_dir = in_dir, out_dir
        self.job = json.loads((in_dir / "job.json").read_text(encoding="utf-8"))
        self.mode = self.job.get("mode", "dress")
        self.pb = json.loads((in_dir / "playback.json").read_text(encoding="utf-8"))
        self.base_gltf = json.loads((in_dir / "scene.gltf").read_text(encoding="utf-8"))
        self.entities = [e for e in self.job.get("entities") or pm.driven_entities(self.pb)]
        self.fps = int(self.job.get("fps", 30))
        self.env = self.pb.get("environment") or {}
        self.report: dict = {"mode": self.mode, "blender": bpy.app.version_string, "scripts": [], "notes": [],
                             "checks": {}, "overrides": {}}
        self.overrides: dict[str, dict] = {}
        self.objs: dict = {}
        self.props: dict[str, dict] = dict(self.job.get("props") or {})  # id -> {"name", "credit"}
        self.props_used: list[str] = []
        self.length_scale = float(self.pb.get("length_scale") or 1.0)  # scene units per metre

    # -- setup
    def import_scene(self) -> None:
        bpy = self.bpy
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.render.fps = self.fps
        res = bpy.ops.import_scene.gltf(filepath=str(self.in_dir / "scene.gltf"))
        if "FINISHED" not in res:
            fail(f"glTF import failed: {res}")
        missing = [e for e in self.entities if e not in bpy.data.objects]
        if missing:
            fail(f"scene.gltf has no root node for entities {missing}")
        self.objs = {e: bpy.data.objects[e] for e in self.entities}
        self.base_trs = {}
        nodes = self.base_gltf.get("nodes", [])
        for name, idx in gltf_merge.entity_roots(self.base_gltf, self.entities).items():
            n = nodes[idx]
            t, r, sc = n.get("translation", [0.0, 0.0, 0.0]), n.get("rotation", [0.0, 0.0, 0.0, 1.0]), \
                n.get("scale", [1.0, 1.0, 1.0])
            if "matrix" in n:  # glTF allows a matrix instead of TRS: decompose it (column-major)
                from mathutils import Matrix

                m = n["matrix"]
                loc, rot, size = Matrix([m[0:4], m[4:8], m[8:12], m[12:16]]).transposed().decompose()
                t, r, sc = list(loc), [rot.x, rot.y, rot.z, rot.w], list(size)
            self.base_trs[name] = (list(t), list(euler_xyz(self._wxyz(r))), list(sc))
        for name in self.entities:
            self.base_trs.setdefault(name, ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]))

    @staticmethod
    def _wxyz(q):  # glTF stores (x, y, z, w)
        return (q[3], q[0], q[1], q[2])

    def hierarchy(self, obj) -> list:
        out, stack = [], [obj]
        while stack:
            o = stack.pop()
            out.append(o)
            stack.extend(o.children)
        return out

    def protected(self) -> list:
        return [o for e in self.objs.values() for o in self.hierarchy(e)]

    def stage(self) -> None:
        """Viewer-equivalent backdrop for the .blend and the stills: world colour, ground disk, light rig."""
        bpy = self.bpy
        scene = bpy.context.scene
        col = bpy.data.collections.new(STAGE)
        scene.collection.children.link(col)
        world = bpy.data.worlds.new("Omnific World")
        scene.world = world
        sky = hex_rgb(self.env.get("sky_color", "#dfe6ee"), (0.75, 0.8, 0.86))
        try:
            world.use_nodes = True
            bg = world.node_tree.nodes.get("Background")
            bg.inputs["Color"].default_value = (*sky, 1.0)
            bg.inputs["Strength"].default_value = 0.6
        except (AttributeError, KeyError, TypeError):
            world.color = sky
        if self.env.get("ground", "flat") == "flat":
            bpy.ops.mesh.primitive_circle_add(vertices=64, radius=120, fill_type="NGON", location=(0, 0, -0.001))
            ground = bpy.context.active_object
            ground.name = "omnific_ground"
            mat = bpy.data.materials.new("omnific_ground")
            gc = hex_rgb(self.env.get("ground_color", "#c9ccd1"))
            mat.diffuse_color = (*gc, 1.0)
            bsdf = self._bsdf(mat)
            if bsdf:
                bsdf.inputs["Base Color"].default_value = (*gc, 1.0)
                bsdf.inputs["Roughness"].default_value = 1.0
            ground.data.materials.append(mat)
            self._move_to(ground, col)
        pos, color, intensity = LIGHT_RIGS.get(self.env.get("lighting", "studio"), LIGHT_RIGS["studio"])
        sun_data = bpy.data.lights.new("omnific_sun", "SUN")
        sun_data.energy = intensity * 1.6
        sun_data.color = hex_rgb(color)
        sun = bpy.data.objects.new("omnific_sun", sun_data)
        col.objects.link(sun)
        d = to_blender(pos)
        sun.rotation_mode = "QUATERNION"
        sun.rotation_quaternion = self._look_rotation((0, 0, 0), d)
        self.stage_lights = [sun_data]
        self.stage_col = col

    def _look_rotation(self, target, origin):
        from mathutils import Vector

        direction = Vector(target) - Vector(origin)
        return direction.to_track_quat("-Z", "Y")

    def _move_to(self, obj, col) -> None:
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        col.objects.link(obj)

    # -- motion (the .blend and the motion bounds)
    def expected(self) -> tuple[list[float], dict[str, list]]:
        """Frame times and, per entity, the (location, quaternion, scale, colour) at each frame in Blender axes."""
        times = pm.frame_times(self.pb, self.fps)
        base_pos = {e: trs[0] for e, trs in self.base_trs.items()}
        out: dict[str, list] = {e: [] for e in self.entities}
        for tm in times:
            poses = pm.evaluate_poses(self.pb, tm, lambda n: base_pos.get(n))
            for e in self.entities:
                bp, br, bs = self.base_trs[e]
                pose = poses.get(e, {"position": {}})
                p, r, s = pm.resolve_pose(pose, bp, br)
                scale = (s, s, s) if s is not None else (bs[0], bs[2], bs[1])
                out[e].append((to_blender(p), q_to_blender(quat_xyz(r)), scale, pose.get("color")))
        return times, out

    def key_motion(self) -> None:
        bpy = self.bpy
        scene = bpy.context.scene
        self.times, self.poses = self.expected()
        scene.frame_start, scene.frame_end = 1, len(self.times)
        scene.render.fps = self.fps
        for e, obj in self.objs.items():
            obj.rotation_mode = "QUATERNION"
            frames = self.poses[e]
            moving = len({(f[0], f[1], f[2]) for f in frames}) > 1
            mats = self._entity_materials(e) if any(f[3] for f in frames) else []
            for k, (loc, q, s, color) in enumerate(frames, start=1):
                if not moving and k > 1 and not color:
                    break
                obj.location, obj.rotation_quaternion, obj.scale = loc, q, s
                if moving or k == 1:
                    for path in ("location", "rotation_quaternion", "scale"):
                        obj.keyframe_insert(data_path=path, frame=k)
                if color:
                    lin = tuple(srgb_to_linear(c) for c in color)
                    for m in mats:
                        self._set_color(m, lin, frame=k)
            self._linear(obj)
        self._clips()
        scene.frame_set(1)

    def _linear(self, idb) -> None:
        for fc in self.fcurves(idb):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"

    def fcurves(self, idb) -> list:
        ad = getattr(idb, "animation_data", None)
        if not ad or not ad.action:
            return []
        act = ad.action
        if hasattr(act, "fcurves"):
            return list(act.fcurves)
        try:
            from bpy_extras import anim_utils

            bag = anim_utils.action_get_channelbag_for_slot(act, ad.action_slot)
            return list(bag.fcurves) if bag else []
        except Exception:  # noqa: BLE001
            return []

    def _clips(self) -> None:
        """Best effort: play each declared clip from simulation time, like the viewer (NLA strip with animated time).
        Clips are secondary motion; a clip that can't be wired is noted, never an error."""
        nodes = self.base_gltf.get("nodes", [])
        for name, idx in gltf_merge.entity_roots(self.base_gltf, self.entities).items():
            clips = (nodes[idx].get("extras") or {}).get("omnific", {}).get("clips", [])
            for clip in clips:
                full = f"{name}:{clip.get('name')}"
                strips = [(o, tr, s) for o in self.hierarchy(self.objs[name]) if o.animation_data
                          for tr in o.animation_data.nla_tracks for s in tr.strips
                          if s.action and s.action.name.startswith(full)]
                if not strips:
                    self.report["notes"].append(f"clip {full}: not found after import; not animated in the .blend")
                    continue
                ref = pm.drive_reference(self.pb, clip.get("drive_state", ""))
                for obj, track, strip in strips:
                    act = strip.action
                    start, end = act.frame_range
                    duration = (end - start) / self.fps
                    strip.use_animated_time = True
                    strip.mute = False
                    track.mute = False  # the glTF importer leaves every clip's track muted
                    phase, last = 0.0, self.times[0]
                    for k, tm in enumerate(self.times, start=1):
                        rate = pm.clip_rate(self.pb, clip.get("drive_state", ""), tm, ref)
                        phase = pm.advance_phase(phase, tm - last, rate, duration) if k > 1 else 0.0
                        last = tm
                        strip.strip_time = start + phase * self.fps
                        strip.keyframe_insert("strip_time", frame=k)
                    if obj.animation_data.action is not None and obj.animation_data.action.name.startswith(full):
                        obj.animation_data.action = None

    # -- materials
    def _entity_materials(self, entity: str) -> list:
        """The entity's materials, copied the first time so changes never leak onto another entity."""
        cache = getattr(self, "_mat_cache", None)
        if cache is None:
            cache = self._mat_cache = {}
        if entity in cache:
            return cache[entity]
        mats = []
        for o in self.hierarchy(self.objs[entity]):
            if o.type != "MESH":
                continue
            for slot in o.material_slots:
                if slot.material is None:
                    continue
                if not slot.material.name.startswith(f"{entity}:"):
                    # entities may share mesh data (one glTF mesh, many nodes), and a DATA slot belongs to the mesh:
                    # move the slot onto the object first, or the copy would recolour every entity using that mesh
                    src = slot.material
                    copy_ = src.copy()
                    copy_.name = f"{entity}:{src.name}"
                    slot.link = "OBJECT"
                    slot.material = copy_
                mats.append(slot.material)
        cache[entity] = mats
        return mats

    def _bsdf(self, mat):
        if mat.node_tree is None:  # Blender 5 materials always have one; use_nodes is deprecated there
            mat.use_nodes = True
        return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)

    def _set_color(self, mat, lin, frame=None) -> None:
        mat.diffuse_color = (*lin, 1.0)
        bsdf = self._bsdf(mat)
        if bsdf and "Base Color" in bsdf.inputs:
            bsdf.inputs["Base Color"].default_value = (*lin, 1.0)
            if frame is not None:
                bsdf.inputs["Base Color"].keyframe_insert("default_value", frame=frame)
        if frame is not None:
            mat.keyframe_insert("diffuse_color", frame=frame)

    def restyle(self, entity: str, base_color=None, metallic=None, roughness=None, emissive=None,
                emissive_strength=None) -> None:
        """For scripts: restyle an entity's materials. The only way scripts may change an entity."""
        if entity not in self.objs:
            raise ValueError(f"no entity named {entity!r}; entities: {self.entities}")
        ov = self.overrides.setdefault(entity, {})
        for m in self._entity_materials(entity):
            bsdf = self._bsdf(m)
            if base_color is not None:
                rgba = tuple(float(c) for c in base_color) + ((1.0,) if len(base_color) == 3 else ())
                m.diffuse_color = rgba
                if bsdf:
                    self._tint(m, bsdf, rgba)
                    if "Alpha" in bsdf.inputs:
                        bsdf.inputs["Alpha"].default_value = rgba[3]
                ov["base_color"] = list(rgba)
            if bsdf and metallic is not None:
                bsdf.inputs["Metallic"].default_value = float(metallic)
                ov["metallic"] = float(metallic)
            if bsdf and roughness is not None:
                bsdf.inputs["Roughness"].default_value = float(roughness)
                ov["roughness"] = float(roughness)
            if bsdf and emissive is not None:
                key = "Emission Color" if "Emission Color" in bsdf.inputs else "Emission"
                if key in bsdf.inputs:
                    bsdf.inputs[key].default_value = (*[float(c) for c in emissive], 1.0)
                strength = 1.0 if emissive_strength is None else float(emissive_strength)
                if "Emission Strength" in bsdf.inputs:
                    bsdf.inputs["Emission Strength"].default_value = strength
                ov["emissive"] = [float(c) for c in emissive]
                ov["emissive_strength"] = strength

    @staticmethod
    def _tint(mat, bsdf, rgba) -> None:
        """Set the base colour; when a texture feeds it (library / CAD models), multiply the texture by the colour
        instead, like the glTF baseColorFactor the dressed scene gets."""
        sock = bsdf.inputs["Base Color"]
        sock.default_value = rgba
        if not sock.is_linked:
            return
        tree = mat.node_tree
        link = sock.links[0]
        node = next((n for n in tree.nodes if n.name == "omnific_tint"), None)
        if node is None:
            node = tree.nodes.new("ShaderNodeVectorMath")
            node.name, node.operation = "omnific_tint", "MULTIPLY"
            tree.links.new(link.from_socket, node.inputs[0])
            tree.links.new(node.outputs[0], sock)
        node.inputs[1].default_value = rgba[:3]

    # -- props
    def prop(self, prop_id: str, location=(0.0, 0.0, 0.0), rotation: float = 0.0, size: float | None = None):
        """For scripts: place a ready-made prop from the brief's list. Its base sits at `location` (scene units, z up),
        turned `rotation` radians about z. `size` = its largest dimension in scene units; default its real size
        (metres x the scene's scale). Returns the prop's root object, or None when the prop is not available."""
        bpy = self.bpy
        path = self.in_dir / "props" / f"{prop_id}.glb"
        if prop_id not in self.props or not path.exists():
            self.report["notes"].append(f"prop {prop_id!r} is not available")
            return None
        before = set(bpy.data.objects)
        res = bpy.ops.import_scene.gltf(filepath=str(path))
        new = [o for o in bpy.data.objects if o not in before]
        if "FINISHED" not in res or not new:
            self.report["notes"].append(f"prop {prop_id!r} could not be imported")
            return None
        holder = bpy.data.objects.new(f"prop:{prop_id}", None)
        bpy.data.collections[SCENERY].objects.link(holder)
        for o in new:
            if o.parent is None or o.parent not in new:
                o.parent = holder
        bpy.context.view_layer.update()
        bb = self.world_bbox(new)
        if bb is not None:
            extent = max(bb[1][i] - bb[0][i] for i in range(3))
            target = float(size) if size else extent * self.length_scale
            k = target / extent if extent > 1e-9 and target > 0 else 1.0
            holder.scale = (k, k, k)
        holder.rotation_euler = (0.0, 0.0, float(rotation))
        bpy.context.view_layer.update()
        bb = self.world_bbox(new)
        if bb is not None:  # stand it on its base, centred on `location`
            holder.location = (location[0] - (bb[0][0] + bb[1][0]) / 2, location[1] - (bb[0][1] + bb[1][1]) / 2,
                               location[2] - bb[0][2])
        bpy.context.view_layer.update()
        if prop_id not in self.props_used:
            self.props_used.append(prop_id)
        return holder

    # -- bounds
    def world_bbox(self, objs) -> tuple | None:
        from mathutils import Vector

        lo, hi = [math.inf] * 3, [-math.inf] * 3
        for o in objs:
            if o.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
                continue
            for c in o.bound_box:
                w = o.matrix_world @ Vector(c)
                for i in range(3):
                    lo[i], hi[i] = min(lo[i], w[i]), max(hi[i], w[i])
        return (lo, hi) if lo[0] != math.inf else None

    def sample_frames(self, n: int = CHECK_FRAMES) -> list[int]:
        last = len(self.times)
        return sorted({1 + round(i * (last - 1) / max(1, n - 1)) for i in range(n)})

    def swept_bounds(self) -> dict[str, tuple]:
        bpy = self.bpy
        out: dict[str, tuple] = {}
        for f in self.sample_frames(len(self.times)):
            bpy.context.scene.frame_set(f)
            for e, obj in self.objs.items():
                bb = self.world_bbox(self.hierarchy(obj))
                if bb is None:
                    loc = obj.matrix_world.translation
                    bb = ([loc[0] - 0.05, loc[1] - 0.05, loc[2] - 0.05], [loc[0] + 0.05, loc[1] + 0.05, loc[2] + 0.05])
                lo, hi = out.get(e, ([math.inf] * 3, [-math.inf] * 3))
                out[e] = ([min(lo[i], bb[0][i]) for i in range(3)], [max(hi[i], bb[1][i]) for i in range(3)])
        bpy.context.scene.frame_set(1)
        return out

    # -- scripts
    def helpers(self):
        import types

        bpy = self.bpy
        run = self
        swept = self.swept

        def astra_obj(name, scene=None):
            scene = scene or bpy.context.scene
            obj = scene.objects.get(name)
            if obj is not None:
                return obj
            matches = sorted((o for o in scene.objects if o.name.startswith(name + ".") and o.name[len(name) + 1:].isdigit()),
                             key=lambda o: o.name)
            return matches[0] if matches else None

        union_lo = [min(b[0][i] for b in swept.values()) for i in range(3)] if swept else [0.0, 0.0, 0.0]
        union_hi = [max(b[1][i] for b in swept.values()) for i in range(3)] if swept else [0.0, 0.0, 0.0]
        omnific = types.SimpleNamespace(
            entities=list(self.entities),
            environment=dict(self.env),
            restyle=self.restyle,
            prop=self.prop,
            length_scale=self.length_scale,
            scenery=lambda: bpy.data.collections[SCENERY],
            motion_bounds=lambda: (tuple(union_lo), tuple(union_hi)),
            entity_bounds=lambda name: tuple(map(tuple, swept[name])),
            ground_z=0.0,
        )
        return {"bpy": bpy, "omnific": omnific, "astra_obj": astra_obj, "astra_fcurves": run.fcurves,
                "__name__": "__omnific_dress__"}

    def fingerprint(self) -> dict:
        fp = {}
        for o in self.protected():
            fp[o.name] = {
                "parent": o.parent.name if o.parent else None,
                "matrix": [round(v, 5) for row in o.matrix_local for v in row],
                "verts": len(o.data.vertices) if o.type == "MESH" else None,
                "geometry": self._geometry_sum(o) if o.type == "MESH" else None,
                "modifiers": [m.type for m in o.modifiers],
                "constraints": [c.type for c in o.constraints],
                "action": (o.animation_data.action.name if o.animation_data and o.animation_data.action else None),
                "hidden": (o.hide_viewport, o.hide_render),
            }
        return fp

    @staticmethod
    def _geometry_sum(o) -> list[float]:
        """A cheap fingerprint of a mesh's vertex positions (moved vertices change it)."""
        n = len(o.data.vertices)
        co = [0.0] * (3 * n)
        o.data.vertices.foreach_get("co", co)
        return [round(sum(co[0::3]), 4), round(sum(co[1::3]), 4), round(sum(co[2::3]), 4),
                round(sum(abs(c) for c in co), 4)]

    def run_scripts(self) -> None:
        bpy = self.bpy
        scripts = sorted((self.in_dir / "scripts").glob("*.py")) if (self.in_dir / "scripts").is_dir() else []
        scenery = bpy.data.collections.new(SCENERY)
        bpy.context.scene.collection.children.link(scenery)
        before_fp = self.fingerprint()
        known = {o.name for o in bpy.data.objects}
        ns_base = self.helpers()
        for path in scripts:
            code = path.read_text(encoding="utf-8")
            entry = {"name": path.stem, "ok": True, "error": ""}
            reason = blender_safety.check(code)
            if reason:
                entry.update(ok=False, error=f"blocked before running: {reason}")
            else:
                layer = bpy.context.view_layer.layer_collection.children[SCENERY]
                bpy.context.view_layer.active_layer_collection = layer
                bpy.context.scene.frame_set(1)
                try:
                    exec(compile(code, f"<{path.stem}>", "exec"), dict(ns_base))  # noqa: S102 - sandboxed runner
                except Exception as e:  # noqa: BLE001 - reported back to the writer
                    entry.update(ok=False, error=self._format_error(e, code, f"<{path.stem}>"))
            # whatever a script made belongs to the scenery (the viewer and the export only see that collection)
            for o in bpy.data.objects:
                if o.name in known or o.name in self.objs:
                    continue
                if o.parent is not None and o.parent.name in {p.name for p in self.protected()}:
                    o.parent = None
                    self.report["notes"].append(f"{o.name}: unparented from an entity (scenery stays separate)")
                if STAGE not in {c.name for c in o.users_collection}:
                    self._move_to(o, scenery)
            known = {o.name for o in bpy.data.objects}
            self.report["scripts"].append(entry)
        bpy.context.scene.frame_set(1)
        after_fp = self.fingerprint()
        changed = sorted(n for n in set(before_fp) | set(after_fp) if before_fp.get(n) != after_fp.get(n))
        self.report["checks"]["protected"] = {"ok": not changed, "changed": changed[:20]}
        self.report["overrides"] = self.overrides

    @staticmethod
    def _format_error(exc, code, filename) -> str:
        lines = code.splitlines()
        out = [f"{type(exc).__name__}: {exc}"]
        for fr in traceback.extract_tb(exc.__traceback__):
            if fr.filename == filename and fr.lineno and 0 < fr.lineno <= len(lines):
                out.append(f"  at line {fr.lineno}: {lines[fr.lineno - 1].strip()}")
        return "\n".join(out)

    # -- checks
    def check_clearance(self) -> None:
        bpy = self.bpy
        col = bpy.data.collections.get(SCENERY)
        objs = list(col.all_objects) if col else []
        hits = []
        for o in objs:
            bb = self.world_bbox([o])
            if bb is None:
                continue
            for e, (lo, hi) in self.swept.items():
                size = max(hi[i] - lo[i] for i in range(3)) or 1.0
                if bb[1][2] <= lo[2] + 0.02 * size:  # ground-like: entirely below the lowest point of the motion
                    continue
                overlap = all(bb[0][i] < hi[i] and bb[1][i] > lo[i] for i in range(3))
                if overlap:
                    hits.append(f"{o.name} intersects the space {e} moves through")
        self.report["checks"]["clearance"] = {"ok": not hits, "hits": hits[:20], "scenery_objects": len(objs)}

    def check_fidelity(self) -> None:
        bpy = self.bpy
        worst, where = 0.0, ""
        for f in self.sample_frames():
            bpy.context.scene.frame_set(f)
            for e, obj in self.objs.items():
                want = self.poses[e][f - 1][0]
                got = obj.matrix_basis.to_translation()
                err = max(abs(got[i] - want[i]) for i in range(3))
                if err > worst:
                    worst, where = err, f"{e} at frame {f}"
        bpy.context.scene.frame_set(1)
        self.report["checks"]["fidelity"] = {"ok": worst <= FIDELITY_TOL, "max_error": worst, "where": where,
                                             "frames": len(self.times), "fps": self.fps}

    def camera(self) -> None:
        """Default camera for the .blend: a fixed three-quarter view that keeps the whole run in frame."""
        bpy = self.bpy
        from mathutils import Vector

        boxes = list(self.swept.values()) + blender_story.effect_boxes(self.pb, self.swept)  # fireballs etc. too
        lo = [min(b[0][i] for b in boxes) for i in range(3)] if boxes else [-1, -1, 0]
        hi = [max(b[1][i] for b in boxes) for i in range(3)] if boxes else [1, 1, 1]
        center = Vector([(lo[i] + hi[i]) / 2 for i in range(3)])
        radius = max(0.5, (Vector(hi) - Vector(lo)).length / 2)
        cam_data = bpy.data.cameras.new(CAMERA)
        cam_data.lens = 35
        cam_data.clip_end = max(1000.0, radius * 50)
        cam = bpy.data.objects.new(CAMERA, cam_data)
        self.stage_col.objects.link(cam)
        r = bpy.context.scene.render
        fov_h = cam_data.angle_x  # sensor_fit AUTO on a landscape render: the sensor width is horizontal
        fov = min(fov_h, 2 * math.atan(math.tan(fov_h / 2) * r.resolution_y / max(1, r.resolution_x)))
        direction = Vector((0.55, -1.0, 0.45)).normalized()
        dist = radius / math.sin(fov / 2) * 1.08
        cam.location = center + direction * dist
        cam.rotation_mode = "QUATERNION"
        cam.rotation_quaternion = (center - cam.location).to_track_quat("-Z", "Y")
        bpy.context.scene.camera = cam

    def check_framing(self) -> None:
        bpy = self.bpy
        from bpy_extras.object_utils import world_to_camera_view
        from mathutils import Vector

        scene = bpy.context.scene
        moving = [e for e in self.entities if len({p[0] for p in self.poses[e]}) > 1]
        worst = {e: 1.0 for e in moving}
        for f in self.sample_frames(len(self.times)):
            scene.frame_set(f)
            for e in moving:
                pts = []
                for o in self.hierarchy(self.objs[e]):
                    if o.type == "MESH":
                        pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
                if not pts:
                    pts = [self.objs[e].matrix_world.translation]
                inside = 0
                for p in pts:
                    v = world_to_camera_view(scene, scene.camera, p)
                    inside += 0.0 <= v.x <= 1.0 and 0.0 <= v.y <= 1.0 and v.z > 0
                worst[e] = min(worst[e], inside / len(pts))
        scene.frame_set(1)
        bad = {e: round(v, 3) for e, v in worst.items() if v < FRAMING_MIN}
        self.report["checks"]["framing"] = {"ok": not bad, "worst": bad, "moving": len(moving)}

    # -- outputs
    def export_dressed(self) -> bytes:
        bpy = self.bpy
        col = bpy.data.collections.get(SCENERY)
        objs = list(col.all_objects) if col else []
        dressed = copy.deepcopy(self.base_gltf)
        notes = gltf_merge.apply_material_overrides(dressed, self.entities, self.overrides)
        self.report["notes"] += notes
        n_lights = 0
        for o in objs:  # glTF (KHR_lights_punctual) has no area lights: the exporter would silently drop them
            if o.type == "LIGHT" and o.data.type == "AREA":
                o.data.type = "SPOT"
                o.data.spot_size = math.radians(150)
                o.data.spot_blend = 0.6
                self.report["notes"].append(f"{o.name}: area light converted to a wide spot light (glTF has no area lights)")
        if objs:
            bpy.context.scene.frame_set(1)
            for o in bpy.context.scene.objects:
                o.select_set(o in objs)
            glb = self.out_dir / "_scenery.glb"
            kwargs = {"filepath": str(glb), "export_format": "GLB", "use_selection": True, "export_cameras": False,
                      "export_animations": False, "export_extras": True, "export_apply": True, "export_yup": True,
                      "export_lights": True}
            try:
                res = bpy.ops.export_scene.gltf(**kwargs)
            except TypeError:  # an option this Blender version doesn't have
                res = bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB", use_selection=True)
            if "FINISHED" not in res or not glb.exists():
                fail(f"scenery export failed: {res}")
            add = gltf_merge.glb_to_gltf(glb.read_bytes())
            glb.unlink()
            n_lights = len(add.get("extensions", {}).get(gltf_merge.LIGHTS_EXT, {}).get("lights", []))
            gltf_merge.merge_scenery(dressed, add)
        else:
            gltf_merge.merge_scenery(dressed, {"scenes": [{"nodes": []}]})
        gltf_merge.mark_dressed(dressed, {"lighting": "scene" if n_lights else "stage", "lights": n_lights,
                                          "scenery_objects": len(objs)})
        credits = [self.props[pid]["credit"] for pid in self.props_used if self.props[pid].get("credit")]
        if credits:  # every reused model is credited in the scene, like SceneBuild's library models
            asset = dressed.setdefault("asset", {})
            asset["copyright"] = "; ".join(x for x in [asset.get("copyright", ""), *credits] if x)
        self.report["props_used"] = list(self.props_used)
        gltf_merge.finalize(dressed)
        same = (gltf_merge.entity_snapshot(self.base_gltf, self.entities)
                == gltf_merge.entity_snapshot(dressed, self.entities))
        self.report["checks"]["gltf_protected"] = {"ok": same}
        self.scene_lights = n_lights
        return gltf_merge.dumps(dressed)

    def stills(self) -> list[Path]:
        bpy = self.bpy
        n = int(self.job.get("stills", 4))
        if n <= 0 or bpy.context.scene.camera is None:
            return []
        scene = bpy.context.scene
        w, h = self.job.get("still_size", [480, 270])
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = int(self.job.get("still_samples", 16))
        try:
            scene.cycles.use_denoising = True
        except AttributeError:
            pass
        scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = w, h, 100
        scene.render.image_settings.file_format = "PNG"
        last = len(self.times)
        frames = sorted({1 + round(i * (last - 1) / max(1, n - 1)) for i in range(n)})
        out = []
        for k, f in enumerate(frames):
            scene.frame_set(f)
            path = self.out_dir / f"still_{k}.png"
            scene.render.filepath = str(path)
            try:
                bpy.ops.render.render(write_still=True)
            except RuntimeError as e:
                self.report["notes"].append(f"still {k} not rendered: {e}")
                continue
            if path.exists():
                out.append(path)
        scene.frame_set(1)
        return out

    def save_blend(self) -> Path:
        bpy = self.bpy
        path = self.out_dir / "scene.blend"
        scene = bpy.context.scene
        for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):  # 4.2-4.4 / 5.x name for EEVEE
            try:
                scene.render.engine = engine
                break
            except TypeError:
                continue
        scene["omnific"] = json.dumps({"title": self.pb.get("title", ""), "fidelity": self.pb.get("fidelity", ""),
                                       "sim_time": [self.pb["t"][0], self.pb["t"][-1]], "run_seconds": pm.RUN_SECONDS})
        try:
            bpy.ops.file.pack_all()
        except RuntimeError as e:
            self.report["notes"].append(f"pack_all: {e}")
        bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True, copy=True)
        return path

    def main(self) -> None:
        t0 = time.monotonic()
        self.import_scene()
        self.stage()
        self.key_motion()
        self.swept = self.swept_bounds()
        self.camera()
        files: dict[str, bytes | Path] = {}
        if self.mode == "dress":
            self.run_scripts()
            self.check_clearance()
            files["scene.gltf"] = self.export_dressed()
            if self.scene_lights:
                for light in self.stage_lights:  # the viewer dims its rig to a fill when the scene brings lights
                    light.energy *= 0.35
        self.check_fidelity()
        self.check_framing()
        # effects, timed objects and phase markers: after the dressed glTF is exported (the viewer draws its own) and
        # after the scenery checks, before the critic's stills (which should show the blast) and the .blend
        self.report["story"] = blender_story.Story(self).build()
        if self.mode == "dress":
            for k, p in enumerate(self.stills()):
                files[f"stills/{k}.png"] = p
        files["scene.blend"] = self.save_blend()
        self.report["materials"] = {e: sorted({s.material.name for o in self.hierarchy(obj) if o.type == "MESH"
                                                for s in o.material_slots if s.material})
                                    for e, obj in self.objs.items()}
        self.report["seconds"] = round(time.monotonic() - t0, 2)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("report.json", json.dumps(self.report, indent=1))
            for name, data in files.items():
                z.writestr(name, data if isinstance(data, bytes) else Path(data).read_bytes())
        result = self.out_dir / "result.zip"
        result.write_bytes(buf.getvalue())
        for name, data in files.items():
            if isinstance(data, Path) and data.parent == self.out_dir and data.exists():
                data.unlink()
        if emit_mode():
            emit_result(result)


def main(argv: list[str]) -> None:
    sys.excepthook = _excepthook
    if len(argv) != 3:
        fail("usage: blender_harness.py <input_dir> <output_dir>", 64)
    in_dir, out_dir = Path(argv[1]), Path(argv[2])
    for need in ("job.json", "scene.gltf", "playback.json"):
        if not (in_dir / need).exists():
            fail(f"missing input {need}")
    Run(in_dir, out_dir).main()


if __name__ == "__main__":
    main(sys.argv)
