/** Compact number formatting for readouts and axes. */
export function fmtNum(v: number): string {
  if (!Number.isFinite(v)) return "n/a";
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(2);
  return String(Number(v.toPrecision(4)));
}

/** Smallest and largest finite values, unpadded ([0, 0] when there are none): for formatting, not for plotting. */
export function finiteBounds(values: readonly number[]): [number, number] {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (Number.isFinite(v)) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  return lo === Infinity ? [0, 0] : [lo, hi];
}

export function finiteRange(values: readonly number[]): [number, number] {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (Number.isFinite(v)) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (lo === Infinity) return [0, 1];
  // flat, or flat up to rounding (29532.500761 +- 1e-9): plot it as a level line, not as amplified noise
  const big = Math.max(Math.abs(lo), Math.abs(hi));
  if (hi - lo <= big * 1e-9) {
    const pad = big > 0 ? big * 0.05 : 0.5;
    return [lo - pad, hi + pad];
  }
  return [lo, hi];
}

/**
 * A steady formatter for a value that changes while the simulation plays: a fixed number of decimals for the whole
 * range (enough to resolve about a thousandth of it), and the widest string that range can produce, so a live
 * readout never changes width and jumps. Very large or tiny magnitudes use a fixed exponential form.
 */
export function steadyFormatter(lo: number, hi: number): { format: (v: number) => string; width: number } {
  const big = Math.max(Math.abs(lo), Math.abs(hi));
  if (!Number.isFinite(big)) return { format: fmtNum, width: 9 };
  if (big !== 0 && (big >= 1e5 || big < 1e-3)) {
    const format = (v: number) => (Number.isFinite(v) ? v.toExponential(2) : "n/a");
    // the longest a value can print: "-1.00e-308" with a sign, "1.00e-308" without
    return { format, width: lo < 0 || hi < 0 ? 10 : 9 };
  }
  const span = Math.abs(hi - lo) > big * 1e-9 ? Math.abs(hi - lo) : big || 1; // flat up to rounding: as flat
  const decimals = Math.min(6, Math.max(0, Math.ceil(-Math.log10(span / 1000))));
  const tiny = 0.5 * 10 ** -decimals;
  const format = (v: number) => {
    if (!Number.isFinite(v)) return "n/a";
    return (Math.abs(v) < tiny ? 0 : v).toFixed(decimals); // never "-0.000"
  };
  const width = Math.max(format(lo).length, format(hi).length, lo < 0 || hi < 0 ? format(-big).length : 0, 3);
  return { format, width };
}
