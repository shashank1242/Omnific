import json
import math
import tempfile
from pathlib import Path

import numpy as np
from omnific_common.contracts import ACTIVITY, PlaybackInput, PlaybackOutput, TaskQueue
from omnific_common.storage import job_key, store_from_env
from omnific_sim.result import SimResult
from omnific_sim.units import ureg
from temporalio import activity
from temporalio.exceptions import ApplicationError

MAX_FRAMES = 600

get_store = store_from_env


def resample(t: np.ndarray, values: np.ndarray, t_new: np.ndarray) -> np.ndarray:
    """Linear interpolation along axis 0 (time); trailing (field) axes keep their shape."""
    if len(t_new) == len(t) and np.array_equal(t_new, t):
        return values
    idx = np.clip(np.searchsorted(t, t_new, side="right") - 1, 0, len(t) - 2)
    span = t[idx + 1] - t[idx]
    w = np.where(span > 0, (t_new - t[idx]) / np.where(span > 0, span, 1.0), 0.0)
    w = np.clip(w, 0.0, 1.0).reshape((-1,) + (1,) * (values.ndim - 1))
    return values[idx] * (1 - w) + values[idx + 1] * w


def _unit(u: str):
    try:
        return ureg.parse_units(u) if u else ureg.dimensionless
    except Exception:  # noqa: BLE001 - unknown unit strings just get no special channel
        return None


def _kind(unit: str, ndim: int) -> tuple[str, float]:
    """Classify a series as ("field"|"length"|"angle"|"other", scale-to-radians for angles)."""
    if ndim >= 2:
        return "field", 1.0
    u = _unit(unit)
    if u is None:
        return "other", 1.0
    if u == ureg.radian:
        return "angle", 1.0
    if u == ureg.degree:
        return "angle", math.pi / 180.0
    if u.dimensionality == ureg.meter.dimensionality:
        return "length", 1.0
    return "other", 1.0


def _finite_range(v: np.ndarray) -> tuple[float, float]:
    f = v[np.isfinite(v)]
    if f.size == 0:
        return -0.5, 0.5
    lo, hi = float(f.min()), float(f.max())
    if not hi > lo:
        return lo - 0.5, hi + 0.5
    return lo, hi


def length_scale(spec, arrays: dict[str, np.ndarray], units: dict[str, str]) -> float:
    """Scene units per metre: 10 / max(|length series values|, |spec pivot and arm components|), else 1."""
    peaks = [
        float(np.max(np.abs(v[np.isfinite(v)])))
        for k, v in arrays.items()
        if _kind(units.get(k, ""), v.ndim)[0] == "length" and np.isfinite(v).any()
    ]
    for e in spec.entities:
        for vec in (e.pivot, e.arm):
            peaks.extend(abs(float(c)) for c in vec or [])
    peak = max(peaks, default=0.0)
    return 10.0 / peak if peak > 0 else 1.0


def _scaled(vec, k: float):
    return None if vec is None else [float(c) * k for c in vec]


def infer_tracks(spec, arrays: dict[str, np.ndarray], units: dict[str, str]) -> list[dict]:
    kinds = {k: _kind(units.get(k, ""), v.ndim) for k, v in arrays.items()}
    pos_scale = length_scale(spec, arrays, units)
    tracks = []
    for e in spec.entities:
        used: set[str] = set()

        def pick(options, used=used):
            return next((c for c in options if c not in used), None)

        for name in e.bound_state:
            if name not in arrays:
                continue
            kind, rad = kinds[name]
            if kind == "length":
                ch, scale = pick(["position_x", "position_y", "position_z"]), pos_scale
            elif kind == "angle":
                ch, scale = pick(["rotation_z"]), rad
            elif kind == "field":
                ch, scale = pick(["color_field"]), 1.0
            else:
                ch, scale = pick(["color", "scale"]), 1.0
            if ch is None:
                continue
            used.add(ch)
            lo, hi = _finite_range(arrays[name])
            tracks.append({"entity": e.name, "channel": ch, "state": name,
                           "min": lo, "max": hi, "scale": scale})
    return tracks


def _nullify(x):
    if isinstance(x, list):
        return [_nullify(i) for i in x]
    return x if math.isfinite(x) else None


def build_playback(spec, result: SimResult, max_frames: int = MAX_FRAMES,
                   field_axes: dict[str, str] | None = None) -> dict:
    t = np.asarray(result.t, dtype=float)
    t_new = np.linspace(t[0], t[-1], max_frames) if len(t) > max_frames else t
    series, arrays = {}, {}
    for name, vals in result.state.items():
        v = resample(t, np.asarray(vals, dtype=float), t_new)
        arrays[name] = v
        entry = {"unit": result.units.get(name, ""), "values": _nullify(v.tolist())}
        if v.ndim >= 2 and field_axes and name in field_axes:
            entry["field_axis"] = field_axes[name]
        series[name] = entry
    ls = length_scale(spec, arrays, result.units)
    doc = {
        "version": 2,
        "length_scale": ls,
        "title": spec.title,
        "fidelity": spec.fidelity.value,
        "t": _nullify(t_new.tolist()),
        "unit_t": spec.time.unit,
        "series": series,
        "entities": [
            {"name": e.name, "kind": e.kind, "bound_state": list(e.bound_state), "parent": e.parent,
             "pivot": _scaled(e.pivot, ls), "arm": _scaled(e.arm, ls)}
            for e in spec.entities
        ],
        "tracks": infer_tracks(spec, arrays, result.units),
    }
    return doc


def dumps(doc: dict) -> bytes:
    return json.dumps(doc, allow_nan=False).encode("utf-8")


@activity.defn(name=ACTIVITY[TaskQueue.DATA])
async def prepare_playback(inp: PlaybackInput) -> PlaybackOutput:
    store = get_store()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "result.npz"
        p.write_bytes(store.get(inp.result_key))
        try:
            result = SimResult.load_npz(str(p))
        except (ValueError, KeyError, OSError) as e:
            raise ApplicationError(f"unreadable result {inp.result_key}: {e}", non_retryable=True) from e
    if len(result.t) < 2:
        raise ApplicationError("result has fewer than 2 samples", non_retryable=True)
    doc = build_playback(inp.spec, result)
    key = job_key(inp.job_id, "playback.json")
    store.put(key, dumps(doc), "application/json")
    return PlaybackOutput(playback_key=key, n_frames=len(doc["t"]))
