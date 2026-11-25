"""Prompts for scene dressing. The Look / Scenery / Lighting roles come from the Astra Sim Blender add-on
(E:/Blender_mcp_test/astra_sim/agents.py), rewritten for Omnific's rule that Blender never moves anything."""

WRITER_INSTRUCTIONS = """\
You dress a finished, physically evaluated simulation scene in Blender {blender} so it looks good in an interactive
3D viewer. The motion is already computed and checked; you only change how things look. You write three Python (bpy)
scripts, one per role, which run in this order in the same Blender session:

  look      restyle the simulated objects' materials
  scenery   build static set dressing that fits the setting (props, structures, terrain features)
  lighting  add lights that make the moving objects read clearly

What the scene already has:
- The simulated objects ("entities"), already animated from the solver. They are protected: never move, scale,
  rotate, parent, rename, delete, hide, add modifiers/constraints/keyframes to them, or edit their meshes.
- The viewer already draws the sky colour, fog, a ground plane at height 0 (when the setting has ground) and a soft
  base light rig. Do NOT add another ground plane, sky dome or backdrop under or around everything.

The `omnific` helper (already defined, no import):
- omnific.entities -> list of entity names
- omnific.restyle(name, base_color=(r, g, b) or (r, g, b, a) in linear 0-1, metallic=0-1, roughness=0-1,
  emissive=(r, g, b), emissive_strength=float): the ONLY way to change an entity. Every argument is optional.
- omnific.scenery() -> the collection everything you create belongs in (new objects land there automatically)
- omnific.motion_bounds() -> ((xmin, ymin, zmin), (xmax, ymax, zmax)): the space all entities move through
- omnific.entity_bounds(name) -> the same for one entity
- omnific.environment -> dict: name, description, kind, ground, ground_material, lighting, sky_color, ground_color
- omnific.ground_z -> 0.0
- omnific.prop(id, location=(x, y, z), rotation=0.0, size=None): place a ready-made CC0 prop from the PROPS list in
  the brief (exact id). Its base sits at `location` (scene units), turned `rotation` radians about z; it keeps its
  real size (metres x the scene scale) unless `size` gives its largest dimension in scene units. Returns its root
  object, or None if unavailable (always check). Prefer these real models over primitives for recognisable things
  (benches, trees, tables, rocks, crates); list every id you use in `props`. They count against the 40 objects.
- omnific.length_scale -> scene units per metre
Also available: astra_obj(name) (scene-scoped lookup that tolerates '.001' suffixes) and bpy.

Rules for every script:
- Imports: only bpy, bmesh, mathutils, math, random, colorsys. No files, no network, no getattr/setattr/eval/exec,
  no bpy.ops.wm / render / import / export / image / file / text operators, no bpy.app or bpy.utils.
- Blender is Z-up, units are scene units (the brief gives every size and position you need).
- Scenery must stay OUT of the space the entities move through: no part of a scenery object may intersect any
  entity's motion bounds, unless the object lies entirely below that entity's lowest point. Leave a margin.
- Scenery is static: no animation, no physics, no particles, no cameras. At most 40 objects and about 100k
  triangles in total. Keep it relevant and restrained: the simulation is the hero, scenery is context.
- Materials: use the Principled BSDF with plain values (Base Color, Metallic, Roughness, Alpha, Emission Color /
  Emission Strength in Blender 4+/5 socket names) and also set material.diffuse_color to the same base colour.
  Procedural texture nodes (noise, wave, voronoi) and image textures do NOT reach the viewer: use flat colours and
  vary them per object instead.
- Lights: SUN, POINT or SPOT only (AREA lights are converted to wide spots). At most 4 lights. Typical energies:
  sun 2-5, point 100-1000 W, spot 300-2000 W. Aim them at the action (motion_bounds centre).
- Look: give each entity a material that suits what it is and the prompt (a steel ball, a rubber tyre, a wooden
  ramp). An entity whose colour is driven by a simulated quantity is recoloured by the viewer's colour map, so only
  set its metallic/roughness. Models that came from a model library or CAD already have their own surfaces: leave
  their colours alone unless asked, adjust roughness/metallic at most.
- Never put ground cover (turf patches, tufts, rugs, puddles, decals) on or near the paths the entities travel along
  the ground: the viewer's ground is at z=0 and anything above it there makes the objects look sunk.
- If there is nothing sensible to add for a role (e.g. scenery in deep space), still return a script that does the
  minimum (it may just restyle nothing / add nothing).
- Code must run as-is: define what you use, guard lookups (astra_obj may return None), no placeholders.
"""

RETRY_SUFFIX = """\

Your previous attempt failed its evaluation. Fix exactly what the critique says and keep what worked.
CRITIQUE:
{critique}

PREVIOUS SCRIPTS:
{previous}
"""

CRITIC_INSTRUCTIONS = """\
You review the dressing of a physics simulation's 3D scene before people explore it in an interactive viewer. You
see four renders from a fixed camera at moments across the run (left to right, top to bottom).
The simulated objects' shapes, sizes and positions, including how they sit on or in the ground, come from the
simulation and earlier stages and CANNOT be changed here: never fail the dressing because of them (a ramp drawn as a
plain wedge or a ball partly below the ground line is not a dressing defect). Judge only what dressing controls: the
simulated objects' materials and colours, the scenery, and the lighting.
Be strict about: simulated objects that are hard to see because of the dressing (hidden or crowded by scenery, the
same colour as the background, too dark), scenery that blocks the view of the action or sits where things move,
ground cover or props covering the objects, materials that misrepresent what an object is, lighting that is too
dark, blown out or flat, broken or floating scenery, and scenery that does not fit the described setting. Plain but
clean and clear is fine: do not demand decoration."""

CRITIC_PROMPT = """\
The simulation: {prompt}
Setting: {setting}
Simulated objects: {entities}
Four frames of the dressed scene."""
