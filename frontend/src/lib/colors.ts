// Shared colormaps so the scatter, matrix, facets and parallel-coordinates encode
// color identically.
//
// MARKS FOLLOW THE THEME; THE HEATMAP RAMP DOES NOT.
//
// A mark drawn ON the plot has to stay visible against whatever it sits on, so
// the mark ramp is theme-dependent (see MARK_DARK / MARK_LIGHT below).
//
// The heatmap ramp used to be theme-dependent too — viridis on dark, a single-hue
// teal ramp on light — on the argument that a sequential ramp's low end should
// recede into ITS surface, which viridis does backwards on white. That was
// dropped deliberately: the matrix is a value-encoding legend-read chart, and a
// cell that changes hue when you flip the theme reads as a different VALUE. One
// ramp, both themes, so the reader can carry a colour between them.
//
// The recede-into-the-surface property is genuinely lost on light; what replaces
// it is that the ramp no longer has to. The two things that DO flip are the ones
// that are not carrying the value: the 1px gap between cells (--panel-glass) and
// the axis labels. The in-cell number is chosen against the CELL, not the theme
// (see DependenceMatrix `.val.dark`), which is now well-defined precisely because
// the cell colour no longer depends on the theme.

let activeTheme: "dark" | "light" = "dark";
/** Set by App on every theme change. Components that paint colors take the
 *  `themeTick` prop so they re-render after this moves. */
export function setColorTheme(t: "dark" | "light") { activeTheme = t; }
export function getColorTheme() { return activeTheme; }

// Categorical marks (pins, sampler-walk chains, region labels). Three of the dark
// six drop below 3:1 on white (green 2.42, yellow 1.64, orange 2.87), so light
// mode gets darkened steps rather than an automatic reuse.
const PALETTE_DARK = [
  "#e64a3b", "#2e8cdc", "#2ebd78", "#f2c52e", "#9c59b6", "#e67d21",
];
const PALETTE_LIGHT = [
  "#c62828", "#1565c0", "#00796b", "#a16207", "#6a1b9a", "#bf5000",
];
// PIN colours. Kept apart from PALETTE_* because pins are big filled marks (a
// lettered badge, a radar polygon, a 2 px polyline) rather than small dots, so
// dark mode wants the airy pastels below. Those pastels are 1.2-1.6:1 on white,
// though — invisible — so light mode borrows the contrast-checked categorical
// steps instead (worst on white: teal 4.0:1).
const PINS_DARK = ["#7adfff", "#ff7ab2", "#ffd47a", "#b4ff7a", "#d49bff", "#ff9b7a"];
const PINS_LIGHT = ["#1565c0", "#c62828", "#a16207", "#00796b", "#6a1b9a", "#bf5000"];
/** Pin colour for slot `i`. Takes `theme` explicitly so a Svelte template
 *  re-renders on a flip — pins store their SLOT, never a resolved colour. */
export function pinColor(i: number, theme: "dark" | "light"): string {
  const P = theme === "light" ? PINS_LIGHT : PINS_DARK;
  return P[i % P.length];
}
export const PIN_SLOTS = PINS_DARK.length;

/** Categorical colors for the current theme. */
export function palette(): string[] {
  return activeTheme === "light" ? PALETTE_LIGHT : PALETTE_DARK;
}
// back-compat for call sites that read it as a constant
export const PALETTE = PALETTE_DARK;

// A HEATMAP and a SCATTER need different ramps, which is the thing that was wrong.
// In a heatmap the low end SHOULD sink into the cell background — that is how you
// read "nothing here". A scatter point that sinks into the background is just
// invisible. Measured: viridis's darkest steps sit at 1.22:1 on the dark backdrop
// and the teal ramp's lightest at 1.10:1 on white, so with the full range a chunk
// of every colour-by encoding was unreadable in BOTH themes.
//
// So marks get their own ramp, confined to the lightness band that clears 3:1 on
// its surface (dark: OKLab L 0.50-0.97, light: 0.20-0.66 — solved numerically) and
// carrying magnitude through lightness AND hue inside it. Worst step 4.28:1 dark,
// 3.62:1 light; both in gamut.
const MARK_DARK: [number, number, number][] = [
  [0.329, 0.475, 0.746], [0.123, 0.633, 0.768], [0.255, 0.762, 0.686],
  [0.608, 0.842, 0.563], [0.940, 0.879, 0.516],
];
const MARK_LIGHT: [number, number, number][] = [
  [0.378, 0.541, 0.709], [0.156, 0.496, 0.500], [0.247, 0.401, 0.235],
  [0.344, 0.264, 0.005], [0.330, 0.129, 0.079],
];

// viridis-ish perceptual ramp — dark end first (recedes on a dark surface)
const STOPS_DARK: [number, number, number][] = [
  [0.27, 0.0, 0.33], [0.23, 0.32, 0.55], [0.13, 0.57, 0.55],
  [0.37, 0.79, 0.38], [0.99, 0.91, 0.14],
];
// single-hue teal, light end first (recedes on a light surface); monotonic in
// OKLab lightness 0.97 -> 0.32, verified
const STOPS_LIGHT: [number, number, number][] = [
  [0.929, 0.965, 0.957], [0.686, 0.882, 0.855], [0.400, 0.760, 0.718],
  [0.145, 0.588, 0.541], [0.043, 0.416, 0.376], [0.016, 0.235, 0.212],
];

function hex([r, g, b]: number[]): string {
  const h = (x: number) => Math.round(x * 255).toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

export function rampHex(n: number, theme = activeTheme, marks = false): string[] {
  const STOPS = marks
    ? (theme === "light" ? MARK_LIGHT : MARK_DARK)
    : (theme === "light" ? STOPS_LIGHT : STOPS_DARK);
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

const RAMP_HEAT = rampHex(64, "dark");
const MARKS_DARK = rampHex(64, "dark", true);
const MARKS_LIGHT = rampHex(64, "light", true);

/** Heatmap ramp (matrix cells). Viridis in BOTH themes — takes no theme, so a
 *  call site cannot reintroduce the drift by passing one. */
export function ramp(): string[] {
  return RAMP_HEAT;
}
/** MARK ramp (scatter points, parallel-coords lines, facet dots) — every step
 *  stays visible against the surface. Use this for anything drawn ON the plot. */
export function markRamp(theme = activeTheme): string[] {
  return theme === "light" ? MARKS_LIGHT : MARKS_DARK;
}
export const RAMP = RAMP_HEAT;   // back-compat

/** Map a [0,1] value to the heatmap ramp (theme-independent). */
export function rampColor(t: number): string {
  const R = ramp();
  return R[Math.round(Math.max(0, Math.min(1, t)) * (R.length - 1))];
}
/** Map a [0,1] value to the mark ramp. */
export function markColor(t: number, theme = activeTheme): string {
  const R = markRamp(theme);
  return R[Math.round(Math.max(0, Math.min(1, t)) * (R.length - 1))];
}

function gradientOf(R: string[]): string {
  return `linear-gradient(90deg, ${R.filter((_, i) => i % 8 === 0).join(", ")})`;
}
/** Legend swatch for the heatmap ramp — same in both themes, like the cells. */
export function heatGradient(): string {
  return gradientOf(ramp());
}
/** Legend swatch for the on-plot mark ramp, which does follow the theme. */
export function markGradient(theme = activeTheme): string {
  return gradientOf(markRamp(theme));
}

/** Color for a raw value, given the field's min/max (or category id if categorical). */
export function colorFor(
  v: number, min: number, max: number, categorical: boolean,
  theme = activeTheme,
): string {
  const P = theme === "light" ? PALETTE_LIGHT : PALETTE_DARK;
  if (categorical) return P[(v | 0) % P.length];
  return markColor((v - min) / (max - min || 1), theme);
}

/** Heatmap cell colour. Theme-independent by design (see the header): the same
 *  dependence value is the same colour on both surfaces. It used to take `theme`
 *  so the Svelte template would re-run on a flip — it no longer needs to, because
 *  there is nothing left for a flip to change here. */
export function cellColor(v: number, min: number, max: number): string {
  return rampColor((v - min) / (max - min || 1));
}

// ---- physical units ----
// The polytope file ships verbose pint-style unit strings ("gigawatt * hour",
// "kilotCO2eq / hour"). Abbreviate them for axis labels and tooltips; anything
// unrecognized falls through unchanged so a new dataset still shows *something*.
const UNIT_SHORT: Record<string, string> = {
  gigawatt: "GW",
  "gigawatt * hour": "GWh",
  "kilotCO2eq / hour": "ktCO₂/h",
  megaEuro: "M€",
};
export const shortUnit = (u: string | undefined): string =>
  !u ? "" : UNIT_SHORT[u] ?? u;

// ---- theme tokens for canvas drawing ----
// CSS can restyle a <div>, but a canvas holds baked pixels: anything drawn with a
// literal stays that colour when the theme flips. These read the live token values
// so the canvas layers match the sheet. Cheap enough to call per redraw, and the
// redraw is driven by the `themeTick` prop.
export function themeToken(name: string, fallback = ""): string {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Every colour the plot canvases need, resolved once per redraw. */
export function canvasTokens() {
  return {
    fg: themeToken("--fg", "#e6edf3"),
    muted: themeToken("--muted", "#8b949e"),
    accent: themeToken("--accent", "#2dd4bf"),
    amberSoft: themeToken("--amber-soft", "rgba(244,180,60,0.10)"),
    amberLine: themeToken("--amber-line", "rgba(244,180,60,0.7)"),
    neutralFill: themeToken("--neutral-fill", "rgba(139,148,158,0.05)"),
    neutralLine: themeToken("--neutral-line", "rgba(139,148,158,0.35)"),
    violinFill: themeToken("--violin-fill", "rgba(139,148,158,0.13)"),
    violinLine: themeToken("--violin-line", "rgba(139,148,158,0.4)"),
    tick: themeToken("--tick", "#fff"),
  };
}
