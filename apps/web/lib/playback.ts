import type { EffectKind, OutputMeasure, PlaybackEffect, PlaybackEnvironment, PlaybackOutput, PlaybackPhase, PlaybackStop, Channel, FieldSeries, Fidelity, Playback, PlaybackEntity, RGB3, ScalarSeries, Series, Track, Vec3 } from "./types";

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
  const version = raw.version === undefined ? 1 : raw.version;
  if (version !== 1 && version !== 2) throw new PlaybackError(`unsupported playback version ${String(version)}`);
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
  const entities: PlaybackEntity[] = [];
  for (const e of rawEntities) {
    if (!isObj(e) || typeof e.name !== "string") continue;
    const ent: PlaybackEntity = {
      name: e.name,
      kind: typeof e.kind === "string" ? e.kind : "",
      bound_state: Array.isArray(e.bound_state)
        ? e.bound_state.filter((x: unknown): x is string => typeof x === "string")
        : [],
    };
    if (version === 2) {
      if (e.parent !== undefined && e.parent !== null && typeof e.parent !== "string") {
        throw new PlaybackError(`entity ${e.name}: parent must be a string or null`);
      }
      if (e.parent !== undefined) ent.parent = e.parent as string | null;
      if (e.pivot !== undefined && e.pivot !== null) ent.pivot = vec3(e.pivot, `entity ${e.name}: pivot`);
      else if (e.pivot === null) ent.pivot = null;
      if (e.arm !== undefined && e.arm !== null) ent.arm = vec3(e.arm, `entity ${e.name}: arm`);
    }
    if (isNum(e.appears_at)) ent.appears_at = e.appears_at as number;
    if (isNum(e.disappears_at)) ent.disappears_at = e.disappears_at as number;
    if (isNum(e.enlarged) && (e.enlarged as number) > 1) ent.enlarged = e.enlarged as number;
    entities.push(ent);
  }
  if (version === 2) checkParents(entities);
  const base = {

    title: typeof raw.title === "string" ? raw.title : "Simulation",
    fidelity: FIDELITIES.includes(raw.fidelity as Fidelity) ? (raw.fidelity as Fidelity) : "heuristic",
    t: t as number[],
    unit_t: typeof raw.unit_t === "string" ? raw.unit_t : "second",
    series,
    entities,
    tracks,
    ...(parseStop(raw.stop) ? { stop: parseStop(raw.stop) as PlaybackStop } : {}),
    ...(parseEnvironment(raw.environment) ? { environment: parseEnvironment(raw.environment) as PlaybackEnvironment } : {}),
    ...story(raw, (t as number[]).length),
  };
  if (version === 1) return { version: 1, ...base };
  const ls = raw.length_scale;
  if (ls !== undefined && (!isNum(ls) || (ls as number) <= 0)) {
    throw new PlaybackError("`length_scale` must be a positive number");
  }
  return { version: 2, length_scale: ls === undefined ? 1 : (ls as number), ...base };
}

function vec3(v: unknown, what: string): Vec3 {
  if (!Array.isArray(v) || v.length !== 3 || !v.every(isNum)) {
    throw new PlaybackError(`${what} must be an array of 3 finite numbers`);
  }
  return [v[0], v[1], v[2]] as Vec3;
}

function checkParents(entities: PlaybackEntity[]): void {
  const byName = new Map(entities.map((e) => [e.name, e]));
  for (const e of entities) {
    if (typeof e.parent === "string") {
      if (!byName.has(e.parent)) throw new PlaybackError(`entity ${e.name}: unknown parent ${e.parent}`);
      if (e.parent === e.name) throw new PlaybackError(`entity ${e.name}: parent cycle`);
    }
  }
  for (const e of entities) {
    const seen = new Set<string>();
    let cur: PlaybackEntity | undefined = e;
    while (cur && typeof cur.parent === "string") {
      if (seen.has(cur.name)) throw new PlaybackError(`entity ${e.name}: parent cycle`);
      seen.add(cur.name);
      cur = byName.get(cur.parent);
    }
  }
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

/** How the run ended (playback-format.md `stop`); undefined for files written before stop rules existed. */
function parseStop(raw: unknown): PlaybackStop | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const o = raw as Record<string, unknown>;
  if ((o.rule !== "settle" && o.rule !== "fixed") || !isNum(o.at)) return undefined;
  return {
    rule: o.rule,
    at: o.at as number,
    window_s: isNum(o.window_s) ? (o.window_s as number) : 3,
    settled: o.settled === true,
    reason: typeof o.reason === "string" ? o.reason : "",
  };
}

const HEX = /^#[0-9a-fA-F]{6}$/;
const KINDS = ["outdoor", "indoor", "space", "underwater", "planetary"] as const;
const LIGHTS = ["day", "dusk", "night", "studio", "space"] as const;

/** The setting to draw; undefined when absent or malformed (the viewer keeps its studio backdrop). */
function parseEnvironment(raw: unknown): PlaybackEnvironment | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const o = raw as Record<string, unknown>;
  const str = (v: unknown, d = "") => (typeof v === "string" ? v : d);
  if (!HEX.test(str(o.sky_color)) || !HEX.test(str(o.ground_color))) return undefined;
  return {
    name: str(o.name, "Setting"),
    description: str(o.description),
    kind: (KINDS as readonly string[]).includes(str(o.kind)) ? (o.kind as PlaybackEnvironment["kind"]) : "outdoor",
    ground: o.ground === "none" ? "none" : "flat",
    ground_material: str(o.ground_material),
    lighting: (LIGHTS as readonly string[]).includes(str(o.lighting)) ? (o.lighting as PlaybackEnvironment["lighting"]) : "day",
    sky_color: str(o.sky_color),
    ground_color: str(o.ground_color),
    fog: isNum(o.fog) ? Math.min(1, Math.max(0, o.fog as number)) : 0,
  };
}

const EFFECT_KINDS: readonly EffectKind[] = ["explosion", "flash", "shockwave", "fire", "smoke", "glow", "accretion_disk", "jet", "splash"];
const MEASURES: readonly OutputMeasure[] = ["max", "min", "final", "initial", "mean", "time_of_max", "time_of_min"];
const nullableNums = (v: unknown, n: number): (number | null)[] | null =>
  Array.isArray(v) && v.length === n && v.every((x) => x === null || typeof x === "number") ? (v as (number | null)[]) : null;
const rgb = (v: unknown): RGB3 | null =>
  Array.isArray(v) && v.length === 3 && v.every(isNum) ? (v as RGB3) : null;

/** Effects, phases and key outputs (playback-format.md "Effects, phases and key outputs"). Each is optional and a
 *  malformed item is dropped rather than failing the whole playback: they illustrate, the series are the data. */
function story(raw: Record<string, unknown>, n: number): { effects?: PlaybackEffect[]; phases?: PlaybackPhase[]; outputs?: PlaybackOutput[] } {
  const out: { effects?: PlaybackEffect[]; phases?: PlaybackPhase[]; outputs?: PlaybackOutput[] } = {};
  const effects: PlaybackEffect[] = [];
  for (const e of Array.isArray(raw.effects) ? raw.effects : []) {
    if (!isObj(e) || typeof e.name !== "string" || !EFFECT_KINDS.includes(e.kind as EffectKind)) continue;
    const radius = nullableNums(e.radius, n);
    const intensity = nullableNums(e.intensity, n);
    const perFrame = Array.isArray(e.color) && e.color.length === n && e.color.every((c) => rgb(c)); // rows, not one triple
    const color = perFrame ? (e.color as RGB3[]) : rgb(e.color);
    if (!radius || !intensity || !color) continue;
    const position = e.position == null ? null : rgb(e.position);
    effects.push({
      name: e.name, kind: e.kind as EffectKind, anchor: typeof e.anchor === "string" && e.anchor ? e.anchor : null,
      position: position as Vec3 | null, description: typeof e.description === "string" ? e.description : "",
      radius, intensity, color,
      ...(isNum(e.enlarged) && (e.enlarged as number) > 1 ? { enlarged: e.enlarged as number } : {}),
      ...(e.auto === true ? { auto: true } : {}),
    });
  }
  if (effects.length) out.effects = effects;
  const phases: PlaybackPhase[] = [];
  for (const p of Array.isArray(raw.phases) ? raw.phases : []) {
    if (!isObj(p) || typeof p.name !== "string" || !isNum(p.start) || !isNum(p.end)) continue;
    phases.push({ name: p.name, start: p.start as number, end: p.end as number, description: typeof p.description === "string" ? p.description : "" });
  }
  phases.sort((a, b) => a.start - b.start);
  if (phases.length) out.phases = phases;
  const outputs: PlaybackOutput[] = [];
  for (const o of Array.isArray(raw.outputs) ? raw.outputs : []) {
    if (!isObj(o) || typeof o.label !== "string" || !isNum(o.value) || !MEASURES.includes(o.measure as OutputMeasure)) continue;
    outputs.push({
      label: o.label, state: typeof o.state === "string" ? o.state : "", measure: o.measure as OutputMeasure,
      value: o.value as number, unit: typeof o.unit === "string" ? o.unit : "", at: isNum(o.at) ? (o.at as number) : null,
      why: typeof o.why === "string" ? o.why : "",
    });
  }
  if (outputs.length) out.outputs = outputs;
  return out;
}
