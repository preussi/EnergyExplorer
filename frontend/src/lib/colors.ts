// Shared colormaps so the scatter and parallel-coordinates encode color identically.

export const PALETTE = [
  "#e64a3b", "#2e8cdc", "#2ebd78", "#f2c52e", "#9c59b6", "#e67d21",
];

// viridis-ish perceptual ramp
const STOPS: [number, number, number][] = [
  [0.27, 0.0, 0.33], [0.23, 0.32, 0.55], [0.13, 0.57, 0.55],
  [0.37, 0.79, 0.38], [0.99, 0.91, 0.14],
];

function hex([r, g, b]: number[]): string {
  const h = (x: number) => Math.round(x * 255).toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

export function rampHex(n: number): string[] {
  return Array.from({ length: n }, (_, i) => {
    const x = (i / (n - 1)) * (STOPS.length - 1);
    const j = Math.min(Math.floor(x), STOPS.length - 2);
    const f = x - j;
    const a = STOPS[j], b = STOPS[j + 1];
    return hex([
      a[0] + (b[0] - a[0]) * f,
      a[1] + (b[1] - a[1]) * f,
      a[2] + (b[2] - a[2]) * f,
    ]);
  });
}

export const RAMP = rampHex(64);

/** Map a [0,1] value to a ramp color. */
export function rampColor(t: number): string {
  const i = Math.round(Math.max(0, Math.min(1, t)) * (RAMP.length - 1));
  return RAMP[i];
}

/** Color for a raw value, given the field's min/max (or category id if categorical). */
export function colorFor(
  v: number, min: number, max: number, categorical: boolean,
): string {
  if (categorical) return PALETTE[(v | 0) % PALETTE.length];
  return rampColor((v - min) / (max - min || 1));
}
