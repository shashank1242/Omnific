# Playback format v1 (`jobs/<id>/playback.json`)

Written by the Data agent (`agents/data`), read by the browser viewer (`apps/web`). The scene comes separately from
`scene.gltf` (Mesh agent): one glTF node per `SimSpec.entities` entry, **named exactly as the entity**.

```jsonc
{
  "version": 1,
  "title": "Pizza baking on Venus",
  "fidelity": "heuristic",                 // SimSpec.fidelity
  "t": [0.0, 0.5, ...],                    // ≤ 600 frames, ascending
  "unit_t": "second",
  "series": {                              // every result state variable
    "T_crust": {"unit": "kelvin", "values": [300.0, ...]},        // scalar: shape (frames,)
    "T_profile": {"unit": "kelvin", "values": [[...], ...],       // field: shape (frames, n)
                  "field_axis": "x"}                                // optional label
  },
  "entities": [{"name": "pizza", "kind": "pizza", "bound_state": ["T_crust"]}],
  "tracks": [                              // how series drive entity visuals
    {"entity": "ball", "channel": "position_x", "state": "x",  "min": 0.0, "max": 120.0, "scale": 0.0833},
    {"entity": "ball", "channel": "position_y", "state": "y",  "min": 0.0, "max": 30.0,  "scale": 0.0833},
    {"entity": "pendulum", "channel": "rotation_z", "state": "theta", "min": -0.5, "max": 0.5, "scale": 1.0},
    {"entity": "pizza", "channel": "color", "state": "T_crust", "min": 300.0, "max": 520.0, "scale": 1.0}
  ]
}
```

## stop (Data agent)
How the run ends, from the SimSpec's `time.stop` rule. `{"rule": "settle"|"fixed", "at": <last t>, "window_s": <s>,
"settled": <bool>, "reason": <text>}`. A `settle` run is trimmed after the last moment any state was more than 0.1% of
its range away from its final value, plus `window_s` seconds (default 3) as a buffer (`omnific_sim.stopping`); the
solver still integrates to `time.end`, which is only a cap. `fixed` runs keep their full span (the user asked for a
duration, or the motion never comes to rest); `reason` says why. Optional: files written before stop rules have none.

## environment (Data agent)
Where the simulation happens, as the viewer draws it: `{"name", "description", "kind": outdoor|indoor|space|underwater|
planetary, "ground": flat|none, "ground_material", "lighting": day|dusk|night|studio|space, "sky_color", "ground_color"
(hex), "fog": 0..1}`, copied from `SimSpec.environment` without its physics. The physics lives in the spec: the
environment's `parameters` (g, air density, ground friction...) are ordinary parameters the model must use (the
evaluator's `environment[...]` checks). Optional: older files have none and keep the studio backdrop.

## Channels
| channel | applies | meaning |
|---|---|---|
| `position_x` / `position_y` / `position_z` | length state | node translation on that axis = `value * scale` (scene units, glTF y is up). Replaces the node's base translation on that axis only. |
| `rotation_z` | angle state (rad) | node rotation about z = `value * scale` radians |
| `scale` | any scalar | uniform node scale = `0.5 + 1.0 * (value - min) / (max - min)` |
| `color` | any scalar | material color from a perceptual colormap over `[min, max]` (viewer shows a legend with `unit`) |
| `color_field` | field state | vertex/texture colormap along the entity over `[min, max]` |

## Track inference (Data agent)
For each entity, walk its `bound_state` in order and pick the first free channel by the state's dimensionality:
length → `position_x`, then `position_y`, then `position_z`; angle (unit radian or degree, converted to rad) →
`rotation_z`; field (ndim 2) → `color_field`; temperature or anything else scalar → `color`, then `scale`.
`min`/`max` are the series' finite range (`max > min`; pad degenerate ranges by ±0.5). Every position track
shares one `scale` = `10 / max(|value|)` over all length series, so the scene fits about 10 units while
relative distances stay true.

# Playback format v2: articulated entities

v2 is v1 plus these fields. Viewers must accept both: v1 files have no articulation.

```jsonc
{
  "version": 2,
  "length_scale": 4.0,                     // scene units per metre; the same factor as every position track's `scale`
  "entities": [
    {"name": "support", "kind": "box", "bound_state": []},
    {"name": "bob", "kind": "sphere", "bound_state": ["theta"],
     "parent": null, "pivot": [0, 4.0, 0], "arm": [0, -4.0, 0]}     // pivot/arm already multiplied by length_scale
  ],
  "tracks": [{"entity": "bob", "channel": "rotation_z", "state": "theta", "min": -1.0, "max": 1.0, "scale": 1.0}]
}
```

## Placement (viewer)
Process entities parents-first. For an entity with `arm`:
- `pivot_world = pivot` if `parent` is null, otherwise `position_world(parent) + pivot`. A null pivot means [0, 0, 0].
- `angle` = the value of its `rotation_z` track times `scale` (0 when there is no track). Angles are **absolute**: they
  don't accumulate down the chain.
- `position_world = pivot_world + Rz(angle) · arm`, where Rz(a)·(x, y, z) = (x cos a − y sin a, x sin a + y cos a, z).
  With arm (0, −L, 0) this gives (L sin a, −L cos a): a pendulum measured from the vertical.
- The node's rotation about z is also set to `angle`, and the viewer draws a thin link from `pivot_world` to
  `position_world`.
- Position tracks on an articulated entity are ignored. Its position comes from the articulation.

Entities without `arm` behave exactly as in v1.

## length_scale (Data agent)
`length_scale = 10 / max(|value| over all length series, |components| of every spec pivot and arm)`, so that
articulated geometry and position tracks share one scale. Without either, it is 1. Every position track's `scale` equals
`length_scale`.

# scene.gltf v3 extras (mesh v2)
`playback.json` is unchanged; `scene.gltf` (written by SceneBuild) may carry generated assets:

- One **root node per SimSpec entity, named exactly as the entity**. The viewer moves and rotates it from playback
  tracks, exactly as before.
- A generated mesh is a child node `mesh:<entity>` (scaled to the size of the procedural asset it replaces, centred
  on the entity origin). Its bones are nodes `bone:<entity>:<bone>` below it, with identity rest rotations, so the
  skeleton moves with the entity.
- `nodes[i].extras.omnific` on entity nodes: `{source: "generated" | "library" | "cad" | "procedural",
  joint: [pivot xyz, arm xyz] | [], clips: [{name, kind, drive_state}], attribution?: {title, author, license, url,
  source}}`. `attribution` is present only on reused library models. Absent on all-procedural scenes, which are
  byte-identical to the MVP.
- `asset.copyright` lists the credit line of every library model in the scene (CC-BY requires it).
- CAD models are skinned like generated ones: a `root` bone plus one bone per moving CAD joint at its origin, each
  part weighted 1 to its bone. A "spin" clip turns its joints a full revolution per period (unbounded rotation).
- Each clip is a glTF animation named `<entity>:<clip>` with rotation channels only. The viewer advances a clip's
  phase with simulation time × rate, where rate = |drive_state(t)| / max |drive_state| (1 when `drive_state` is
  empty), so a gait speeds up with the solver's speed and pauses/seeks with playback (`apps/web/lib/clips.ts`).

# scene.gltf v4: dressed scenes (SceneDress)
A Blender-dressed scene.gltf is the v3 file with additions only; every entity node, its `mesh:`/`bone:` children,
skins, clips and extras are byte-identical (checked by the dress evaluator):

- **`scenery` root node** (a scene root, never an entity): everything Blender's Scenery and Lighting scripts made,
  merged from Blender's glTF export with every index remapped. Static (no animations, skins or cameras). Lights are
  `KHR_lights_punctual` (directional / point / spot; Blender area lights are converted to wide spots), in the
  exporter's photometric units (W x 683 lm/W).
- **Entity materials** may be restyled: each restyled entity gets its own cloned materials (and its own mesh copy
  when a mesh was shared), so a restyle never leaks onto another entity. Colour tracks still override base colour.
- **`scenes[0].extras.omnific_scene`** = `{"dressed": true, "lighting": "scene" | "stage", "lights": n,
  "scenery_objects": n}`. The viewer scales the scenery's lights by 1/683 and, with `lighting: "scene"`, dims its own
  rig to a fill (`apps/web/lib/dressing.ts`). Viewers that ignore it still show a valid scene.

## The .blend export
`scene.blend` (Blender 4.2+) holds the same scene with every entity keyed from playback.json by
`sandbox/src/omnific_sandbox/playback_math.py`, a port of the viewer's math pinned by
`apps/web/lib/fixtures/playback-golden.json`. Axes: glTF/viewer (x, y, z) → Blender (x, -z, y) (Blender's glTF
importer's own convention), Euler XYZ rotations → quaternions. Timeline: the whole run in 10 s at 30 fps (301
frames, like the viewer at 1×); the simulation's time span is in the scene's `omnific` custom property. Clips play
through NLA strips whose time follows `drive_state` (best effort). It also holds a viewer-like stage (ground disk,
sky colour, sun) and a default camera that keeps every moving object in view.

# True sizes (entity geometry)
A SimSpec entity may carry `geometry` = {shape: sphere | box | cylinder | wedge | disk | plane, size [x, y, z] in metres,
position [x, y, z] in metres (static bodies only, the bounding-box centre; [] for moving ones), yaw_deg}. Position states
are the body's CENTRE, bound in x, y, z order; the ground is y = 0. When a job packages, the playback is written first
and SceneBuild gets its `length_scale` (`PlaybackOutput.length_scale`), so every entity with geometry is drawn at
size x length_scale (procedural shapes exactly; library / generated / CAD models fitted by their longest side), static
ones at position x length_scale and turned by yaw_deg about y. The scene and the motion then share one scale: a ball
of radius r with centre y = r sits on the ground. `length_scale` also counts every static body's corners. A `wedge` is a
right-angled prism centred on its bounding box whose sloped face goes down toward +x (yaw 180 for -x). The evaluator's
`geometry[...]` checks (`omnific_sim.visual`) fail the model when a body sinks more than a quarter of its half-height into
the ground or a sphere passes that far into a static wedge, box or plane; their feedback goes to the modeler. Specs
without geometry keep the MVP unit primitives.


# Effects, phases and key outputs
Events are drawn, not just motion. All of it is optional and additive (older playbacks and viewers are unaffected); the
Data agent (`agents/data/src/omnific_data/story.py`) writes it from the solved states, so the solver stays the truth.

- `effects[]`: `{name, kind, anchor, position, description, radius[], intensity[], color}`. `kind` is explosion |
  flash | shockwave | fire | smoke | glow | accretion_disk | jet. Per frame (same length as `t`): `radius` in scene
  units (the spec's `radius_state` x `length_scale`, or its fixed `size` in metres x `length_scale`) and `intensity`
  0..1 (the `intensity_state` over its peak; 1 when none). `color` is one sRGB triple, or one per frame when the spec
  gives a `temperature_state` (blackbody colour). `anchor` = entity it follows (else `position`, scene units, y up).
  An effect is not drawn while its intensity or radius is below 0.003. The browser draws them with shaders
  (`apps/web/components/Effects.tsx`) animated on simulation time; the .blend gets the same values keyed per frame as
  emission meshes and point lights in an "Omnific effects" collection (`sandbox/.../blender_story.py`).
- `phases[]`: `{name, start, end, description}` in the time unit, in order; the last ends with the run. The viewer
  shows the current one and a clickable strip; the .blend gets a timeline marker at each start.
- `entities[].appears_at` / `disappears_at` (time unit, only when set): the viewer hides the entity outside
  [appears_at, disappears_at); the .blend keys `hide_render` / `hide_viewport` as steps.
- `outputs[]`: `{label, state, measure, value, unit, at, why}`, the spec's key outputs computed from the full-resolution
  result (`measure` max | min | final | initial | mean | time_of_max | time_of_min; `at` = when, null for the mean).
  The job page shows them as "Key results"; clicking one jumps to that moment.

The modeler is told to make entities physical objects (validated: no "diagram", "chart", "light curve"...), to model
events as states that switch on at the event (Piecewise) with effects drawing them, and to pick 2-6 key outputs for
the kind of simulation (the clarifier's `outputs` multi-select question lets the user choose). The evaluator's
`effect[...]` checks fail an effect whose brightness or radius stays 0 for the whole run (an explosion that never
happens).
