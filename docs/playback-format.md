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
