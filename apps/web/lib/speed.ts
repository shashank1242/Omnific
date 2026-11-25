/** Playback speed: 1x plays a whole run in RUN_SECONDS of wall clock (components/player.ts). */
export const RUN_SECONDS = 10;
export const PRESET_SPEEDS = [0.1, 0.25, 0.5, 1, 2, 4, 8, 16];
export const MIN_SPEED = 0.01;
export const MAX_SPEED = 1000;

const SECONDS_PER: Record<string, number> = {
  s: 1, sec: 1, second: 1, seconds: 1, ms: 1e-3, millisecond: 1e-3, milliseconds: 1e-3,
  us: 1e-6, microsecond: 1e-6, microseconds: 1e-6, min: 60, minute: 60, minutes: 60,
  h: 3600, hr: 3600, hour: 3600, hours: 3600, day: 86400, days: 86400, d: 86400,
  week: 604800, weeks: 604800, year: 31557600, years: 31557600, yr: 31557600,
};

/** Seconds per unit of simulation time, or null for units that aren't a time we know. */
export function secondsPer(unit: string): number | null {
  return SECONDS_PER[unit.trim().toLowerCase()] ?? null;
}

/** The speed at which one second of simulated time takes one second of wall clock, or null when not meaningful. */
export function realTimeSpeed(spanInUnit: number, unit: string): number | null {
  const k = secondsPer(unit);
  if (k === null || !(spanInUnit > 0)) return null;
  const speed = RUN_SECONDS / (spanInUnit * k);
  return speed >= MIN_SPEED && speed <= MAX_SPEED ? speed : null;
}

/** A typed custom speed, or null if it isn't a usable number ("3", "0.5", "2x", "2.5×"). */
export function parseSpeed(text: string): number | null {
  const v = Number(text.trim().replace(/[x×]$/i, "").trim());
  return Number.isFinite(v) && v >= MIN_SPEED && v <= MAX_SPEED ? v : null;
}

/** "1×", "0.25×", "2.5×" (at most 3 significant digits). */
export function speedLabel(v: number): string {
  return `${Number(v.toPrecision(3))}×`;
}
