export type RGB = [number, number, number];
export type ColormapName = "viridis" | "inferno";

const hex = (h: string): RGB => [
  parseInt(h.slice(0, 2), 16) / 255,
  parseInt(h.slice(2, 4), 16) / 255,
  parseInt(h.slice(4, 6), 16) / 255,
];

// Evenly spaced anchor colours sampled from matplotlib's perceptually uniform maps.
const STOPS: Record<ColormapName, RGB[]> = {
  viridis: ["440154", "472d7b", "3b528b", "2c728e", "21918c", "28ae80", "5ec962", "addc30", "fde725"].map(hex),
  inferno: ["000004", "1b0c41", "4a0c6b", "781c6d", "a52c60", "cf4446", "ed6925", "fb9b06", "f7d13d", "fcffa4"].map(hex),
};

/** Colour for x in [0,1] (clamped; NaN maps to 0). */
export function colormap(name: ColormapName, x: number): RGB {
  const stops = STOPS[name];
  const u = Number.isFinite(x) ? Math.min(1, Math.max(0, x)) : 0;
  const p = u * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(p));
  const f = p - i;
  const a = stops[i] as RGB;
  const b = stops[i + 1] as RGB;
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

export function normalize(value: number, min: number, max: number): number {
  return max > min ? (value - min) / (max - min) : 0;
}

export function toCss([r, g, b]: RGB): string {
  return `rgb(${Math.round(r * 255)} ${Math.round(g * 255)} ${Math.round(b * 255)})`;
}

export function gradientCss(name: ColormapName, direction = "to right", n = 12): string {
  const parts = Array.from(
    { length: n },
    (_, i) => `${toCss(colormap(name, i / (n - 1)))} ${((i / (n - 1)) * 100).toFixed(1)}%`,
  );
  return `linear-gradient(${direction}, ${parts.join(", ")})`;
}

/** Heat-like units get inferno, everything else viridis. */
export function colormapForUnit(unit: string): ColormapName {
  return /kelvin|celsius|fahrenheit|degC|degF|^K$/i.test(unit) ? "inferno" : "viridis";
}
