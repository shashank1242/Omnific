import type { Channel, FieldSeries, Fidelity, Playback, ScalarSeries, Series, Track } from "./types";

export class PlaybackError extends Error {}

const CHANNELS: readonly Channel[] = [
  "position_x", "position_y", "position_z", "rotation_z", "scale", "color", "color_field",
];
const FIDELITIES: readonly Fidelity[] = ["analytic", "validated_numerical", "heuristic"];

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const num = (v: unknown): number => (typeof v === "number" ? v : NaN);
const isNum = (v: unknown) => typeof v === "number" && Number.isFinite(v);

export function isFieldSeries(s: Series): s is FieldSeries {
  return Array.isArray(s.values[0]);
}

/** Validate and normalise playback.json v1; throws PlaybackError with a human-readable reason. */
export function parsePlayback(raw: unknown): Playback {
  if (!isObj(raw)) throw new PlaybackError("playback is not an object");
  if (raw.version !== 1) throw new PlaybackError(`unsupported playback version ${String(raw.version)}`);
  const t = raw.t;
  if (!Array.isArray(t) || t.length < 1 || !t.every(isNum)) {
    throw new PlaybackError("`t` must be a non-empty array of numbers");
  }
  for (let i = 1; i < t.length; i++) {
    if ((t[i] as number) < (t[i - 1] as number)) throw new PlaybackError("`t` must be ascending");
  }
  if (!isObj(raw.series)) throw new PlaybackError("`series` must be an object");
  const series: Record<string, Series> = {};
  for (const [name, s] of Object.entries(raw.series)) {
    if (!isObj(s) || typeof s.unit !== "string" || !Array.isArray(s.values)) {
      throw new PlaybackError(`series ${name}: expected {unit, values}`);
    }
    if (s.values.length !== t.length) {
      throw new PlaybackError(`series ${name}: ${s.values.length} values for ${t.length} frames`);
    }
    if (Array.isArray(s.values[0])) {
      const n = (s.values[0] as unknown[]).length;
      const rows = s.values.map((row: unknown) => {
        if (!Array.isArray(row) || row.length !== n) throw new PlaybackError(`series ${name}: ragged field rows`);
        return row.map(num);
      });
      const fs: FieldSeries = { unit: s.unit, values: rows };
      if (typeof s.field_axis === "string") fs.field_axis = s.field_axis;
      series[name] = fs;
    } else {
      const ss: ScalarSeries = { unit: s.unit, values: s.values.map(num) };
      series[name] = ss;
    }
  }
  const tracks: Track[] = [];
  const rawTracks: unknown[] = Array.isArray(raw.tracks) ? raw.tracks : [];
  rawTracks.forEach((tr, i) => {
    if (!isObj(tr) || typeof tr.entity !== "string" || typeof tr.state !== "string") {
      throw new PlaybackError(`track ${i}: expected entity and state`);
    }
    if (!CHANNELS.includes(tr.channel as Channel)) {
      throw new PlaybackError(`track ${i}: unknown channel ${String(tr.channel)}`);
    }
    const s = series[tr.state];
    if (!s) throw new PlaybackError(`track ${i}: unknown state ${tr.state}`);
    const channel = tr.channel as Channel;
    if ((channel === "color_field") !== isFieldSeries(s)) {
      throw new PlaybackError(`track ${i}: channel ${channel} does not match ${tr.state} dimensionality`);
    }
    tracks.push({
      entity: tr.entity,
      channel,
      state: tr.state,
      min: isNum(tr.min) ? (tr.min as number) : 0,
      max: isNum(tr.max) ? (tr.max as number) : 1,
      scale: isNum(tr.scale) ? (tr.scale as number) : 1,
    });
  });
  const rawEntities: unknown[] = Array.isArray(raw.entities) ? raw.entities : [];
  const entities = rawEntities.flatMap((e) =>
    isObj(e) && typeof e.name === "string"
      ? [{
          name: e.name,
          kind: typeof e.kind === "string" ? e.kind : "",
          bound_state: Array.isArray(e.bound_state)
            ? e.bound_state.filter((x: unknown): x is string => typeof x === "string")
            : [],
        }]
      : [],
  );
  return {
    version: 1,
    title: typeof raw.title === "string" ? raw.title : "Simulation",
    fidelity: FIDELITIES.includes(raw.fidelity as Fidelity) ? (raw.fidelity as Fidelity) : "heuristic",
    t: t as number[],
    unit_t: typeof raw.unit_t === "string" ? raw.unit_t : "second",
    series,
    entities,
    tracks,
  };
}

/** Bracketing frame indices and blend factor for `time` (clamped to the run). */
export function locate(t: readonly number[], time: number): { i: number; j: number; f: number } {
  const last = t.length - 1;
  if (last <= 0 || time <= (t[0] as number)) return { i: 0, j: 0, f: 0 };
  if (time >= (t[last] as number)) return { i: last, j: last, f: 0 };
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if ((t[mid] as number) <= time) lo = mid;
    else hi = mid;
  }
  const span = (t[hi] as number) - (t[lo] as number);
  return { i: lo, j: hi, f: span > 0 ? (time - (t[lo] as number)) / span : 0 };
}

const lerp = (a: number, b: number, f: number) => a + (b - a) * f;

export function sampleScalar(values: readonly number[], t: readonly number[], time: number): number {
  const { i, j, f } = locate(t, time);
  const a = values[i] as number;
  const b = values[j] as number;
  if (!Number.isFinite(a)) return b;
  if (!Number.isFinite(b)) return a;
  return lerp(a, b, f);
}

export function sampleField(rows: readonly (readonly number[])[], t: readonly number[], time: number): number[] {
  const { i, j, f } = locate(t, time);
  const a = rows[i] as readonly number[];
  const b = rows[j] as readonly number[];
  return a.map((v, k) => lerp(v, b[k] as number, f));
}

/** Value of a field row at fractional position u in [0,1] along its axis. */
export function sampleAlong(row: readonly number[], u: number): number {
  if (row.length === 0) return NaN;
  const p = Math.min(1, Math.max(0, u)) * (row.length - 1);
  const k = Math.min(row.length - 1, Math.floor(p));
  const next = Math.min(row.length - 1, k + 1);
  return lerp(row[k] as number, row[next] as number, p - k);
}
