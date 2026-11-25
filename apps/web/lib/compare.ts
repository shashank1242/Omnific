// Tuning and comparing runs: what the user changed, and how the result moved because of it.
import { fmtNum } from "./format";
import { isFieldSeries } from "./playback";
import type { Playback, SimSpec, VariantChanges } from "./types";

/** Form state: every editable value as the text in its input. */
export interface TuneValues {
  parameters: Record<string, string>;
  initial: Record<string, string>;
  end: string;
}

export function valuesOf(spec: SimSpec): TuneValues {
  return {
    parameters: Object.fromEntries(spec.parameters.map((p) => [p.name, String(p.value)])),
    initial: Object.fromEntries(spec.state.map((s) => [s.name, String(s.initial_value)])),
    end: String(spec.time.end),
  };
}

const same = (a: number, b: number) => a === b || Math.abs(a - b) <= 1e-12 * Math.max(1, Math.abs(a), Math.abs(b));

/** The changes relative to the original spec, or an error naming the first value that isn't a number. */
export function changesFrom(spec: SimSpec, v: TuneValues): { changes: VariantChanges; error: string | null } {
  const changes: VariantChanges = { parameters: [], initial: [], end: null };
  const read = (label: string, text: string | undefined): number | null => {
    const n = Number((text ?? "").trim());
    if ((text ?? "").trim() === "" || !Number.isFinite(n)) throw new Error(`${label} needs a number`);
    return n;
  };
  try {
    for (const p of spec.parameters) {
      const n = read(p.name, v.parameters[p.name]);
      if (n !== null && !same(n, p.value)) changes.parameters.push({ name: p.name, value: n });
    }
    for (const s of spec.state) {
      const n = read(`Starting ${s.name}`, v.initial[s.name]);
      if (n !== null && !same(n, s.initial_value)) changes.initial.push({ name: s.name, value: n });
    }
    const end = read("Duration", v.end);
    if (end !== null && !same(end, spec.time.end)) {
      if (end <= spec.time.start) throw new Error(`Duration must end after ${fmtNum(spec.time.start)} ${spec.time.unit}`);
      changes.end = end;
    }
  } catch (e) {
    return { changes, error: e instanceof Error ? e.message : String(e) };
  }
  return { changes, error: null };
}

export const changeCount = (c: VariantChanges) => c.parameters.length + c.initial.length + (c.end === null || c.end === undefined ? 0 : 1);

/** One line per change, e.g. "g 9.81 → 1.62 m/s**2". */
export function describeChanges(spec: SimSpec, c: VariantChanges): string[] {
  const unitOf = (name: string) => spec.parameters.find((p) => p.name === name)?.unit ?? "";
  const was = (name: string) => spec.parameters.find((p) => p.name === name)?.value;
  const initUnit = (name: string) => spec.state.find((s) => s.name === name)?.unit ?? "";
  const initWas = (name: string) => spec.state.find((s) => s.name === name)?.initial_value;
  return [
    ...c.parameters.map((x) => `${x.name} ${fmtNum(was(x.name) ?? NaN)} → ${fmtNum(x.value)}${unitOf(x.name) ? ` ${unitOf(x.name)}` : ""}`),
    ...c.initial.map((x) => `starting ${x.name} ${fmtNum(initWas(x.name) ?? NaN)} → ${fmtNum(x.value)}${initUnit(x.name) ? ` ${initUnit(x.name)}` : ""}`),
    ...(c.end === null || c.end === undefined ? [] : [`duration ${fmtNum(spec.time.end)} → ${fmtNum(c.end)} ${spec.time.unit}`]),
  ];
}

export interface Impact {
  name: string;
  unit: string;
  /** Value at the end of each run. */
  from: number;
  to: number;
  /** Largest value reached in each run. */
  peakFrom: number;
  peakTo: number;
  /** Relative change of the end value; null when the base is ~0. */
  pct: number | null;
}

function lastFinite(values: number[]): number {
  for (let i = values.length - 1; i >= 0; i--) if (Number.isFinite(values[i])) return values[i] as number;
  return NaN;
}
function peak(values: number[]): number {
  let m = -Infinity;
  for (const v of values) if (Number.isFinite(v) && v > m) m = v;
  return m === -Infinity ? NaN : m;
}

/** How each quantity both runs share ended up (and peaked), base run first. Biggest relative change first. */
export function impacts(base: Playback, other: Playback): Impact[] {
  const out: Impact[] = [];
  for (const [name, a] of Object.entries(base.series)) {
    const b = other.series[name];
    if (!b || isFieldSeries(a) || isFieldSeries(b)) continue;
    const from = lastFinite(a.values), to = lastFinite(b.values);
    const pct = Number.isFinite(from) && Math.abs(from) > 1e-12 ? ((to - from) / Math.abs(from)) * 100 : null;
    out.push({ name, unit: a.unit, from, to, peakFrom: peak(a.values), peakTo: peak(b.values), pct });
  }
  const weight = (i: Impact) => (i.pct === null ? (same(i.from, i.to) ? 0 : Infinity) : Math.abs(i.pct));
  return out.sort((x, y) => weight(y) - weight(x));
}
