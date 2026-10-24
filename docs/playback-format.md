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
