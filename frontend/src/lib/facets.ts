// Facet shape taxonomy — the classification rule, shared by the coupling matrix
// and the docked facet so they can never disagree.
//
// The expensive, objective part (the corner gaps) is computed once on the backend
// and cached. Turning gaps into a label is trivial arithmetic, so it lives here:
// that lets the threshold be a live control instead of a rebuild.
//
// WHY A THRESHOLD AT ALL, AND WHY NOT A DERIVED ONE
// The shadow polygon is the hull of 72 support-LP maximisers. A corner that is
// genuinely attained is the maximiser for every direction in its quadrant, so it
// is always recovered — `gap = 0` is exact — and the inscribed-hull error is
// bounded by R(1 − cos(Δθ/2)) ≈ 7e-4 on the unit square. Numerical noise is ~1e-3.
// The threshold is therefore a MATERIALITY choice ("how much of a corner must be
// cut before it is worth reporting"), not a noise floor.
//
// A data-driven "natural break" was tried and rejected: on polytope_13 the largest
// jump in the sorted gaps falls at 0.216, which reclassifies DAC×biomass (the only
// `at_least_one` pair, gap 0.18) and photovoltaics×biomass (0.16) as independent.
// The distribution is dominated by the 21 trade-offs at 0.25–0.42, so the break
// tracks that cluster and swallows the rare categories. Hence: a documented
// default, exposed as a control, with the continuous gap always shown.

import type { CornerGaps, FacetCategory, FacetShape } from "./api";

/** Default materiality threshold. Near-box pairs on polytope_13 top out at 0.11
 *  and the trade-offs start at 0.25, so 0.15 sits clear of both. */
export const GAP_EPS_DEFAULT = 0.15;
/** A label decided by a gap this close to the threshold could flip; say so. */
export const BORDERLINE_BAND = 0.08;

export const CATEGORIES: FacetCategory[] =
  ["tradeoff", "dependency", "at_least_one", "independent"];

// Validated with the dataviz checker on the ALL-PAIRS pairlist against this app's
// panel (#141b24) — a matrix puts any two categories side by side, so
// adjacent-only validation is not enough. Three hues is the documented cap: every
// 4th candidate failed the normal-vision floor, so `band`/`locked` share the
// neutral fill and are told apart by a dashed outline plus their label.
export const CAT: Record<FacetCategory, { color: string; label: string; hint: string }> = {
  tradeoff:     { color: "#3987e5", label: "trade-off",    hint: "cannot both be large" },
  dependency:   { color: "#d95926", label: "dependency",   hint: "one at scale requires the other" },
  at_least_one: { color: "#199e70", label: "at least one", hint: "cannot both be small" },
  independent:  { color: "#8b949e", label: "independent",  hint: "no corner ruled out — choose freely" },
  band:         { color: "#8b949e", label: "band",         hint: "substitute one-for-one; total pinned" },
  locked:       { color: "#8b949e", label: "locked",       hint: "must move together" },
  unknown:      { color: "#8b949e", label: "—",            hint: "" },
};

/** Mirrors `generate.py:classify_facet`. The diagonal pairs are tested FIRST: a
 *  facet cutting both LL and UR is a band, not a trade-off with a footnote. */
export function classifyFacet(
  gaps: CornerGaps | null | undefined,
  x: string,
  y: string,
  eps: number = GAP_EPS_DEFAULT,
): FacetShape {
  if (!gaps) return { category: "unknown", strength: 0, cut: [], detail: "" };
  const hot = (Object.entries(gaps) as [keyof CornerGaps, number][])
    .filter(([, v]) => v >= eps);
  const strength = hot.length ? Math.max(...hot.map(([, v]) => v)) : 0;
  const cut = hot.sort((a, b) => b[1] - a[1]).map(([k]) => k as string);
  const has = (k: string) => cut.includes(k);
  const borderline = strength > 0 && strength < eps + BORDERLINE_BAND;

  if (!cut.length)
    return { category: "independent", strength: 0, cut: [],
             detail: `${x} and ${y} can be chosen independently` };
  if (has("LL") && has("UR"))
    return { category: "band", strength, cut, borderline,
             detail: `${x} and ${y} substitute one-for-one — their total is pinned` };
  if (has("LR") && has("UL"))
    return { category: "locked", strength, cut, borderline,
             detail: `${x} and ${y} have to move together` };
  if (cut[0] === "UR")
    return { category: "tradeoff", strength, cut, borderline,
             detail: `${x} and ${y} cannot both be large` };
  if (cut[0] === "LL")
    return { category: "at_least_one", strength, cut, borderline,
             detail: `${x} and ${y} cannot both be small — at least one is required` };
  // dependency is DIRECTIONAL: which one needs the other is the useful part
  const [needs, needed] = cut[0] === "LR" ? [x, y] : [y, x];
  return { category: "dependency", strength, cut, borderline, needs, needed,
           detail: `${needs} at scale requires ${needed}` };
}
