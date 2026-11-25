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
