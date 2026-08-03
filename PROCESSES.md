# Energy Explorer — Processes & How They Work

Technical companion to [DESIGN.md](DESIGN.md) (architecture/roadmap) and
[README.md](README.md) (quickstart). This document explains **every process in
the system**: where the data comes from, the math behind each transformation,
how each visualization is computed, and how the pieces interact.

---

## Contents

1. [System architecture](#1-system-architecture)
2. [Data provenance — where the polytope comes from](#2-data-provenance)
3. [The near-optimal polytope](#3-the-near-optimal-polytope)
4. [Uniform sampling — how hit-and-run works (now the app's own cloud)](#4-uniform-sampling--hit-and-run)
5. [Normalized vs physical space](#5-normalized-vs-physical-space)
6. [Projections (PCA)](#6-projections)
7. [Cluster labels](#7-cluster-labels)
8. [Candidate generation (steering)](#8-candidate-generation-steering)
9. [Extreme designs (MGA alternatives)](#9-extreme-designs)
10. [Visual analytics overlays](#10-visual-analytics-overlays) (incl. 10.13 settings & dimension toggles)
11. [Interaction model](#11-interaction-model)
12. [Color pipeline](#12-color-pipeline)
13. [API reference](#13-api-reference)
14. [Development & deployment workflow](#14-development--deployment-workflow)
15. [Landing page & dataset building](#15-landing-page--dataset-building)
16. [Sessions: multi-tenancy & persistence](#16-sessions-multi-tenancy--persistence)

---

## 1. System architecture

Two Docker containers (a third, Postgres, is planned for persistence):

```
┌───────────────────────────────────────────────────────────┐
│ frontend (nginx)  — Svelte 5 + Vite + regl-scatterplot     │
│   full-bleed WebGL scatter · parallel coordinates ·        │
│   floating glass panels · overlays (topo/compass/spokes/   │
│   paths/walk) · compare tray with radar glyphs             │
└──────────────▲────────────────────────────────────────────┘
               │ /api/* (nginx proxy → backend:8000)
┌──────────────┴────────────────────────────────────────────┐
│ backend (FastAPI + numpy + scikit-learn + scipy)            │
│   loads the polytope · GENERATES the sample cloud (hit-    │
│   and-run) at build time · PCA live · clusters · extremes  │
│   (LP) · shadows / flexibility / volume · generation       │
└──────────────▲────────────────────────────────────────────┘
               │ read-only volume
              data/polytope.npz   (no samples file — generated)
```

- The **frontend** never computes statistics on the 9-D data; it renders what
  the backend serves and handles interaction (selection, brushing, pinning).
- The **backend** holds each dataset in memory and caches expensive results
  **on that dataset** (dependence, shadows, extremes, flexibility). A dataset
  **build** (§15) generates the cloud and eagerly warms all of these so the first
  view of every tab is instant. The only thing on disk is a few-KB **session
  recipe** per built dataset (§16) — enough to regenerate it after a restart.

## 2. Data provenance

The polytope was produced **upstream** of this project by an energy-system MGA
pipeline (the reference sample set below was historically stamped in the now-dropped
`polytope_samples.npz → config`):

1. **Energy-system model** — ZEN-garden-family model, scenario
   `Crystal_Ball_2050gf` (2050 green-field European system). A cost-minimization
   chooses technology capacities to meet demand/climate constraints at minimum
   **net present cost**. The single optimum: `c_star ≈ 1,258,542` with mix
   `u_star` (nuclear 110 / PV 7271 / wind-on 3371 / … ).
2. **MGA relaxation** — cost is allowed to rise by `ε = 0.05` (5 %). The set of
   all feasible designs with cost ≤ 1.05·c\* is carved out by sweeping each
   technology to its feasible extremes (the "ORACLE" step, `fmax_eps0.05.json`),
   yielding a **convex polytope** in 10-D (9 technologies + cost) →
   `polytope.npz` (`A` 172×10, `b` 172, plus 153 vertex points `X`).
3. **Preconditioning** — `PolyRound 0.4.0` rounds the polytope so MCMC mixes
   well; this defines the normalized space (§5).
4. **Sampling** — `hopsy 1.7.0` draws uniform samples with two independent
   samplers (CHRRT and HAR; §4): 4 chains × 5,000 each, burn-in 1,000,
   thinning 100/20, seeds 42–45.
5. **Convergence checks** — `arviz` rank-R̂ ≈ 1.00 and bulk-ESS (CHRRT ≈ 20k,
   HAR ≈ 3–9k); both samplers pass, and their agreement cross-validates
   uniformity.

Every scatter point = one feasible 2050 European energy-system design within
5 % of minimum cost.

**What the app actually uses now (2026-07).** Only the **polytope** (`(A, b)` +
`u_star`/`z_star`) is shipped and consumed; the upstream sample file was dropped.
The app **generates its own uniform cloud** from the polytope with hit-and-run at
build time (§4, §15), sized by the user on the landing page. Steps 4–5 above thus
describe how the *reference* cloud was validated upstream, not what ships — the
polytope provenance (steps 1–3) is what still matters.

## 3. The near-optimal polytope

All constraints are linear, so the near-optimal set is a convex polytope

```
P = { x ∈ ℝ¹⁰ : A·x ≤ b }        A ∈ ℝ¹⁷²ˣ¹⁰, b ∈ ℝ¹⁷²
```

Two properties the app exploits:

- **Convexity** — for any two designs `u, v ∈ P`, every point on the segment
  `(1−t)·u + t·v` is also in `P`. This is the basis of the **A→B design paths**
  (§10.4): every intermediate morph step is a *guaranteed feasible* design.
- **Linearity of constraints** — a user requirement like `nuclear ≤ 20 GW` is
  just one more row in `(A, b)`, so steering (§8) never needs to re-run the
  energy model.

## 4. Uniform sampling — hit-and-run

Goal: sample **uniformly from inside P**. Grids and rejection sampling fail in
9-D (cost is projected out at load, §1), so an MCMC random walk is used. One
hit-and-run step from point `x`:

1. **Direction** — draw `d ~ N(0, I)`, normalize to unit length.
2. **Chord** — the line `x + t·d` stays inside P while every constraint holds.
   With `residᵢ = bᵢ − aᵢ·x ≥ 0` (slack to face *i*):
   - faces with `aᵢ·d > 0` cap `t ≤ residᵢ/(aᵢ·d)` → `t_max` = the tightest;
   - faces with `aᵢ·d < 0` floor `t ≥ residᵢ/(aᵢ·d)` → `t_min` = the tightest.
3. **Jump** — `x ← x + t·d` with `t ~ Uniform(t_min, t_max)`.

The stationary distribution of this Markov chain is uniform on P. Practical
hygiene: **burn-in** (discard early steps; remove dependence on the start) and
**thinning** (keep every k-th step; reduce autocorrelation).

Variants in the dataset:

| | HAR | CHRRT |
|---|---|---|
| direction | random unit vector | coordinate axes |
| polytope | raw | **rounded** (PolyRound) |
| thinning | 20 | 100 |
| ESS (of 20k) | ~3,000–9,000 | ~20,000 |

The app's own generator (`backend/app/generate.py:hit_and_run`) implements the HAR
variant in ~40 lines of numpy (burn-in 250, thinning 8). It now produces **the
base sample cloud** too, not just steered candidates: `Dataset.__init__` calls it
once at build time (§15) with the landing-page count (default 20k, 1k–100k),
seeded 42. Because it's a **single chain**, `meta.diagnostics` R̂/ESS are empty —
these samples are for interactive analytics, not a convergence-certified result;
`hopsy` remains the rigorous option for final published numbers. Every downstream
view (projection, dependence, violins, volume) reads this generated cloud.

**Starting point / feasibility** — the **Chebyshev center**: the LP

```
max r  s.t.  aᵢ·x + ‖aᵢ‖·r ≤ bᵢ ,  r ≥ 0
```

finds the center `x` of the largest inscribed ball. `r > 0` ⇒ the polytope has
an interior (start there); `r ≤ 0` ⇒ **empty** ⇒ the app reports the user's
constraint set as infeasible.

The **sampler walk** overlay (§10.5) animates these chains: consecutive thinned
states drawn as fading comet trails, one color per chain.

## 5. Normalized vs physical space

Two coordinate systems for the same designs:

- **`_norm`** — the PolyRound-rounded space, ≈ [0,1] per axis. The polytope
  `(A, b)` lives here. Distances are comparable across axes → **all geometry
  (projection, clustering, sampling, kNN) happens here.**
- **`_phys`** — physical units (GW, t/h, NPC …), spanning 1e2–1e6 across axes →
  **all display (tooltips, parallel-coordinate scales, constraints UI) happens
  here.**

The map between them was *recovered empirically* from the paired samples: per
dimension, least-squares fit of `phys = s·norm + o` (residual ~1e-9, perfectly
diagonal). User constraints in physical units are converted into polytope rows
through this map: `physᵢ ≤ hi  ⇔  (sᵢ·eᵢ)·x ≤ hi − oᵢ`.

Because the map is affine and per-axis, **linear interpolation commutes**:
lerping two designs in phys space equals lerping in norm space — the morph
(§10.4) can be computed directly on displayed values.

## 6. Projections

9-D → 2-D for the map view. Computed on **normalized technology axes only**
(cost is excluded from the geometry and encoded as color).

| method | runs | optimum placement | axis meaning |
|---|---|---|---|
| **PCA** | live (<100 ms) | exact (`transform`) | linear combos; loadings shown |
| **STAR** | live, frontend | linear | user-placed axis anchors |

t-SNE/UMAP were removed along with the projection cache — PCA is fast enough to
run per request, so there is no build step and no HTTP 425 path.

- **PCA** — eigendecomposition of the covariance; the app reports explained
  variance (PC1 ≈ 27.9 %, PC2 ≈ 16.1 %) and **loadings** (`components`), which
  drive the axis captions ("PC1 · ↑nuclear ↓PV") and the tech compass (§10.2).
- **t-SNE / UMAP** — neighborhood-preserving embeddings with no out-of-sample
  transform, so the **optimum is appended to the data before fitting** and its
  embedded position is stored in a `.opt.npy` sidecar. Cache builder:
  `docker compose exec backend python -m app.projections [--force]` — skips
  existing files, logs timing (~30–45 s per method × sampler).
- UI honesty rule: non-metric methods get a "axes are non-metric" note, and
  features that need exact projection of *new* points (paths, spokes, compass,
  generation) are PCA-only.

## 7. Region labels (not clusters!)

`GET /api/clusters` runs **k-means (k=6)** on the projected 2-D points, then
characterizes each region in the original technology space: for region *c*
and technology *j*, the z-score

```
z_cj = (mean_c(tech_j) − mean_all(tech_j)) / std_all(tech_j)
```

The 1–2 technologies with the largest |z| become the label ("↑nuclear ↓PV").
The frontend draws labels at the projected centroids with an iterative
**declutter** pass (push-apart + keep clear of the axis-caption margins).

**Honesty note:** the samples are *uniform* over a convex body — there are no
clusters in this data. k-means here just tiles the projection into named
*regions* ("around here, designs tend to be nuclear-heavy"), which is useful
for orientation but must not be read as discovered structure. The UI therefore
calls them **region labels**.

## 8. Candidate generation (steering)

`POST /api/generate` — the phase-3 core. Filtering the 20k samples gets sparse
as constraints tighten; generation **re-samples the constrained region** so it
stays densely populated:

1. **Constraints → rows.** Each `{axis, min, max}` (physical units) becomes 1–2
   rows appended to `(A, b)` via the §5 map. Brush constraints from the
   parallel coordinates and manual chips are merged.
2. **Feasibility.** Chebyshev-center LP (§4). Radius ≤ 0 → `feasible: false` →
   UI: "No feasible designs under these constraints."
3. **Sampling.** Hit-and-run from the Chebyshev center (seeded, burn-in 250,
   thin 8) → up to 5,000 fresh designs, *guaranteed* to satisfy both the
   original near-optimality and the user's constraints.
4. **Projection.** PCA is fit on the base sampler's normalized technologies and
   the candidates are `transform`ed into the **same map space**, so they render
   in place. (~0.4 s for 2,000 designs.)

The UI swaps the views to the candidate set (banner + "back to full space");
brushing the candidates and regenerating gives an iterative co-adaptive loop.

## 9. Extreme designs

`GET /api/extremes` — the classic MGA question "how far can each lever go?".
For each technology *i*, two LPs over the polytope:

```
min / max  eᵢ·x   s.t.  A·x ≤ b
```

→ 18 vertex designs ("max wind-off", "min nuclear", …), returned with physical
values, cost, and base-PCA coordinates (cached in memory; ~0.1 s total).
Example finding: **zero-nuclear is feasible at +3.0 % cost; max-nuclear at
+1.6 %.**

## 10. Visual analytics overlays

All overlays live in `ScatterGL.svelte` and re-position on every pan/zoom via
the `view` event's d3 scales (data → screen is affine).

### 10.1 Option topography (density contours)
KDE contours (`d3-contour`, bandwidth 14, 10 thresholds) over the projected
points, computed once per dataset in a fixed 512² virtual grid, rendered as SVG
paths under a single affine `matrix(...)` transform (with
`vector-effect: non-scaling-stroke`) so pan/zoom needs **no recompute**.
*Reading it:* the samples are uniform in 9-D, so 2-D density = the polytope's
**thickness along the 7 projected-out dimensions**. Ridges = mixes with many
distinct realizations (flexible regions); sparse fringes = tightly constrained
corners.

### 10.2 Tech compass (PCA biplot arrows)
The PCA loading vector of each technology `(comp₁ⱼ, comp₂ⱼ)` drawn as an arrow
from the data centroid (length ∝ loading magnitude). *Reading it:* moving along
an arrow increases that technology; opposite arrows = trade-offs (e.g. nuclear
vs PV); short arrows = technologies the map barely distinguishes (they vary in
the projected-out dimensions).

### 10.3 Extreme spokes
Dashed lines from the optimum ◯ to each §9 extreme (dots are clickable → pin).
*Reading it:* the reachable envelope of the near-optimal space, anchored at the
cost optimum.

### 10.4 Design paths + morphing (A→B)
Pin two designs (click a point / an extreme dot / "+ u*"). The map shows the
straight segment A→B with a moving marker; the parallel coordinates show the
**interpolated design** `lerp(A, B, t)` as a glowing line, animated by ▶ or
scrubbed by slider. By convexity (§3) *every* intermediate profile is a
feasible near-optimal design — the morph is a continuous family of valid
transition strategies, with per-technology values readable at every step.
(PCA is linear, so the data-space segment projects exactly to the screen
segment — no approximation.)

### 10.5 Sampler walk
Animates the MCMC chains (§4): per chain, the last ~70 consecutive (thinned)
states drawn as an alpha-fading trail with a bright comet head, advancing ~2
states/frame on a dedicated canvas. *Reading it:* well-mixed chains sweep the
whole body quickly and the 4 colors blend — a visceral convergence diagnostic
(and the "how was this cloud made" story in one animation).

### 10.6 Radar glyphs + compare tray
Each pinned design renders as a 9-spoke star plot, normalized per technology
against the **full-space min/max** (so glyph shapes are comparable across
pins), with Δcost vs optimum. The tray is the working memory of an exploration
session: extremes, candidates and hand-picked designs side by side.

### 10.7 Find similar (kNN)
For a pinned design, per-technology range-normalized Euclidean distance over
the 9 technologies; the 250 nearest displayed designs become the linked
selection (highlighted in both views). *Use:* "are there many designs like
this one?" — a robustness probe.

### 10.8 Facet view (exact 2-D shadow) — opened from the Coupling matrix
Why: a 2-D projection of this 9-D body is *supposed* to look like a filled
blob — measured: the strongest tech-tech correlation is |r|=0.36, the PCA
spectrum is nearly flat (28/16/12/11/11/8/6/5/2 %), and 23/172 constraints are
plain per-tech bounds, so the body is a weakly-coupled, corner-trimmed box.
The information lives in the **boundary**, so the facet view shows it exactly:
- `GET /api/shadow_pairs` ranks all **36** axis pairs (C(9,2) — cost is projected
  out, so there are no cost pairs) by **boxiness** = area(exact shadow polygon) /
  area(its bounding box). Low boxiness = real trade-off facets (best:
  CCS×biomass 0.56; worst: every nuclear pair ≈ 1.0 — nuclear is decoupled from
  everything). This ranking no longer drives a picker — the Coupling matrix is the
  only way to choose a pair — but it still supplies the fallback order when the
  drawn pair's axes get disabled.
- `POST /api/shadow {x, y, constraints}` computes the polygon by
  **support-function LPs**: for 72 directions θ, maximize cosθ·xᵢ + sinθ·xⱼ
  over `A·x ≤ b` (+ user constraint rows); the maximizers' convex hull is the
  exact orthogonal projection of the polytope. Unconstrained shadows are cached.
- UI: one facet at a time — the sample cloud, the exact white boundary, the
  optimum, and — when constraints/brushes are active — the **constrained polygon**
  in teal (debounced 300 ms).
*Reading it:* samples thin out near the boundary because the body's thickness
→ 0 there — the boundary designs are feasible but have few variations.

The facet is drawn in a **square plot box** (both axes get equal pixel length,
centered in the panel) so the shadow's shape isn't stretched by the panel's aspect
ratio, and a **⇄ swap axes** button transposes the pair in place. Disabled
dimensions (§10.13) can't be drawn: the panel falls back to the top still-active
pair.

### 10.9 Remaining-flexibility bars
`POST /api/flexibility {constraints}` solves 2 LPs per axis (18 total, ~50 ms)
for the **exact** remaining [min, max] of every technology under the
user's constraints. The sidebar shows: full near-optimal range (track), the
surviving range (teal band, animated), and the optimum (white tick). Unlike
filtering the 20k samples (which goes sparse after 2–3 brushes), LP ranges are
exact at any constraint depth, and an empty polytope is reported as infeasible.
This is the MGA "how far can each lever still go?" question, answered live.

### 10.10 Star coordinates (user-steered projection) + tour
Method "STAR ✦": each technology gets a draggable 2-D **anchor vector** v⃗ⱼ; a
design with normalized values tⱼ renders at Σⱼ tⱼ·v⃗ⱼ. This is a *linear*
projection, so convexity, straight facets, the cost ramp, pins, and A→B paths
all remain exact (the literature recommends star coordinates over RadViz for
precisely this reason). Anchors start on a circle; one-click resets to the PCA
loadings or the circle.

**Grand tour (proper).** The **tour** button animates toward random orthonormal
2-frames, but the interpolation between two frames is itself re-orthonormalized
every tick (Gram–Schmidt on the frame's two 9-vectors, then rescaled to a fixed
radius). This keeps every *intermediate* frame area-true — a real grand tour
(per the Distill 2020 "Grand Tour" guide) rather than a linear blend that squishes
mid-transition. Moving shadows reveal 3-D+ shape no static projection shows.

**Direct manipulation (drag-to-rotate).** In star mode the optimum ◯ and any
pinned design are draggable; dragging one rotates the *whole* projection so that
design follows the cursor (the Distill "data-point mode"). Math: for the dragged
design's tech vector t, nudge each frame column fₖ by `(q−fₖ·t)/‖t‖ · t̂` toward
the cursor target q (eased), then re-orthonormalize. Because we only add a
component along t̂, the other designs move minimally — you "grab" a design and
swing the space around it. Screen pixels are inverted back to projection space
through the scatter's live d3 scales.

This linear-projection family is the honest choice for this data (the Grand Tour
paper's "data-visual correspondence"): a change in one design moves only that
design, and convexity / straight facets / the cost ramp all stay exact — unlike
t-SNE/UMAP, where one point's position depends on the whole distribution.
Implementation: 5k×9 dot products per frame (<10 ms); contours debounced 220 ms.

### 10.11 Coupling matrix (statistical dependence) — the "Coupling" tab
The Facets tab measures coupling **geometrically** (boxiness of the exact LP
shadow). The Coupling tab measures it **statistically** from the samples, which
matters because the linear correlation is nearly useless here (max |Pearson|
≈ 0.39) while the real couplings are nonlinear (polytope facets/corners). Three
pairwise measures over the 9 technology axes (`GET /api/dependence`, cached per sampler,
computed on a 1,500-point subsample since distance correlation is O(n²)):
- **Distance correlation** (default) — `dCor ∈ [0,1]`, **0 iff independent**,
  catches nonlinear/non-monotonic dependence. Computed from double-centered
  pairwise-distance matrices: one per axis, then each pair is a cheap
  `mean(Aᵢ∘Aⱼ)` (dCov²) normalized by the distance variances. The statistical
  analogue of facet boxiness.
- **Mutual information** — k-NN (KSG-style) estimator (`mutual_info_regression`,
  no binning), symmetrized; surfaces couplings dCor/Pearson rank lower (e.g.
  DAC×battery).
- **Pearson** — the linear baseline, for contrast.

All three are affine-invariant per axis, so normalized vs physical units give
identical results. (An earlier build carried a companion **dCor vs (1 − boxiness)**
scatter in the side rail as a sampler sanity check; it was dropped to keep the
view to one idea. The same cross-check can be read pair-by-pair from the matrix
itself — the lower-left value against the upper-right outline.)

**Matrix layout (current).** Coupling is the **default tab**, and the only entry
point to the facet view (§10.8) — there is no separate Facets tab. The matrix is
symmetric, so rather than mirror the numbers the two triangles show two reads of
each pair: the **lower-left** holds the dependence value (color + number), the
**upper-right** holds that pair's **exact 2-D facet outline** (the LP shadow
polygon, fetched per pair and scaled into the cell) — statistical coupling on one
side, geometric shape on the other.

**Axis order — clustered by proximity.** The axes are ordered by **hierarchical
clustering** on the coupling values, the construction used by
[Amumo](https://github.com/ginihumer/Amumo)'s "cluster matrix by similarity"
(`utils.get_cluster_sorting`): agglomerative **complete linkage** over
`1 − similarity`, then read the axes off the dendrogram. Mutually-coupled axes end
up adjacent, so a coupled group reads as a bright block on the diagonal instead of
scattered cells. Two differences from Amumo:
- It cuts the tree with `fcluster(…, 10, "maxclust")` and sorts by cluster id.
  With 9 axes that puts every axis in its own cluster and degenerates to the input
  order, so we use the **leaf order**, which is what does the grouping.
- A dendrogram leaves each subtree's flip free (2^(k−1) equally valid readings, very
  visible at this size), so we take the flip set minimizing the summed distance
  between neighbouring axes — **optimal leaf ordering**, by the DP that keeps only
  the cheapest arrangement per (first, last) endpoint pair.

Coupling is a *distance* here via `1 − v/max(v)`: MI is unbounded, so an absolute
`1 − v` would not be comparable across metrics. The clustering is keyed to the
metric on display and to the enabled axes, so switching dcor/MI/Pearson or toggling
a dimension re-clusters. Verified against `scipy.cluster.hierarchy`: the merge
sequence is identical, and the resulting order is within ~3 % of the best of all
9! permutations (which need not respect the tree at all).

**Coupling → Facet split.** Clicking any cell slides a **Facet panel in from the
right** (docked split: matrix keeps the left half, facet the right), showing that
pair's exact shadow. Its header carries **⛶ full view** — which hides the matrix
so the facet fills the region, toggling back with ⛶ exit full view — and a close
(✕). Re-clicking cells re-drills the open panel.

Both the matrix and the split honour the dimension toggles (§10.13): the
dependence matrices are subset to the enabled axes, and a docked facet whose axis
gets disabled is closed rather than left captioning a hidden dimension.

### 10.12 Volume retained (how much of the space survives) — subset simulation
The Consequences panel answers *"how much smaller does the near-optimal space get
under these constraints?"* as a **relative volume**:
`vol({Ax≤b} ∩ box) / vol({Ax≤b})`, where the box is the current constraint set
(manual + brush + slice). Because the samples are **uniform** on the polytope, the
fraction of the sample cloud that falls in the box is already a Monte-Carlo
estimate of this ratio — computed client-side, with a **Wilson 95% interval** for
honesty. This is the quantitative form of the slice idea: sweep a slice and watch
the retained volume shrink.

The catch is the **tail**. Once the box drops below ~1/N of the volume, *zero*
samples land in it — the old "0 selected, but still feasible" confusion. So when
fewer than **200** samples land in-region (where the raw fraction gets noisy or
zero), the frontend debounce-fetches `POST /api/volume`, a **subset-simulation**
(multilevel-splitting) estimate — the standard rare-event Monte-Carlo technique —
that reuses the same hit-and-run sampler:

- Write the box as extra halfspaces `r·x ≤ βᵣ` and define a normalized violation
  `h(x) = maxᵣ (r·x − βᵣ)/scaleᵣ` (each `scaleᵣ` = that row's max positive excess
  over the base polytope, from one support LP). The target region is `{h ≤ 0}`.
- Lower an intermediate threshold `h_ℓ` level by level so each **conditional**
  probability `P(h ≤ h_{ℓ+1} | h ≤ h_ℓ) ≈ p₀` (= 0.1): pick `h_ℓ` as the p₀-quantile
  of `h` over the current population, then draw a fresh population with hit-and-run
  restricted to `{Ax ≤ b, r·x ≤ βᵣ + h_ℓ·scaleᵣ}` — still a polytope, so the sampler
  is unchanged. Level 0 reuses the precomputed uniform cloud (already uniform on
  `{Ax≤b}`), so it's free.
- Multiply the per-level conditionals: `ratio = p₀^L · (final in-target fraction)`.
  Reaching 10⁻⁸ takes ~8 levels (~8·pop samples), not the ~10⁸ a naive count needs.

The response carries `ratio`, `log10`, `levels`, `method` (`trivial` / `direct` /
`subset_simulation` / `empty`) and an approximate coefficient of variation `cv`
(`δ² ≈ Σ_ℓ (1−p₀)/(p₀·pop)`; it ignores MCMC autocorrelation, so it's optimistic —
shown as a `±cv%` band, not a hard interval). The UI shows the raw fraction + CI
when ≥ 200 samples land, otherwise the subset-sim estimate, otherwise
`< (1/N)%` as an upper bound. **Validation:** in the bulk the estimator matches
the raw fraction within ~2 % (0.40 vs 0.41; 0.119 vs 0.125); in the deep tail it is
seed-stable to a few ×10⁻⁴. There the estimator and the raw 400k-sample fraction
can disagree by ~5× — expected, since the upstream samples are a **single** MCMC
chain that under-samples deep-tail corners exactly where subset simulation pushes;
both are estimates good to roughly a factor there, so tail values are
order-of-magnitude, not two-sig-fig. The **exact** feasibility and per-lever LP
ranges (§10.9) are unaffected — they hold at any constraint depth regardless of how
many samples land.

### 10.13 Settings panel & dimension toggles
The **⚙ settings** popover (top bar) holds three controls:
- **Dimensions on/off** — a checkbox per technology (minimum 2 stay on). Disabling
  an axis removes it from the **parallel coords** strip and the **Coupling** matrix,
  closes a docked facet drawn on it, and clears it from the color-by / slice pickers
  (each resets gracefully).
- **Map clusters (k)** — 2–12, the number of k-means region labels (§7).
- **Reduce motion** — disables the facet slide-in and other animations.

**Semantics — dropping a dimension is display-level *marginalization*, not model
surgery.** Nothing is recomputed: the samples, the dependence matrices, and the
facet shadows are all filtered/subset at the presentation layer only (the core
`dispFields`/`dispValues` stay full-width, indexed by axis name). Consequences:
- A point `(x, y, z)` with `z` disabled just isn't *drawn* on `z`; it keeps its
  value and plots at `(x, y)`. Marginals are invariant — the `x` violin, dCor(x,y),
  and the (x,y) facet are **identical** whether or not `z` is shown, because each
  was already a marginal/projection over the full space.
- The three views are **not** derivable from one another: violins are 1-D
  marginals (no coupling info), dCor is a scalar coupling summary of the *sample
  density*, the facet is the *geometric* feasible boundary. Related (uniform
  sampling ties density to volume) but distinct — hence the dCor-vs-boxiness check.
- Crucially, a dropped axis **still constrains the ones kept**: the (x,y) facet is
  the projection of the full polytope, so it already integrates out `z`. Hiding
  `z` ≠ *constraining* `z` (which would shrink the shadow) and ≠ *eliminating* `z`
  (the Fourier–Motzkin removal of cost at load, §3, which genuinely drops a var).
- **The Map (PCA) ignores the toggles** — PCA is a linear combination of all 9
  technologies, so honouring a drop would require refitting on the subset (a
  backend recompute we chose not to do); a note in the panel says so.

## 11. Interaction model

- **Linked selection** — one row-index set drives both views: lasso (Select
  mode) or axis-brush → highlight in scatter (others dimmed 0.12×) + colored
  lines in parallel coords + live constraint chips.
- **Click = pin** (Pan mode, single point) — feeds the compare tray and paths.
- **Brush → Generate** — brush ranges become constraints automatically; chips
  (solid = manual ×-removable, dashed = brush-derived) are merged on Generate.
- **Hover** — full physical profile tooltip (z-index above all panels).
- **View state machine** — full space ⇄ candidate set (banner; cluster labels,
  spokes and walk auto-disable in candidate mode; pins persist since both are
  in the same PCA space). Changing sampler/method resets selection, candidates
  and pins (projection coordinates change meaning).

## 12. Color pipeline

One shared colormap module (`colors.ts`): a 64-step viridis-like ramp for
continuous fields and a 6-color categorical palette (chains). The same encoding
feeds the scatter (`pointColor` + `colorBy: valueA` with values normalized to
[0,1]), the parallel-coordinate lines (canvas strokes, grouped by quantized
color so lines ≈ 64 stroke calls), and the legend. Color-by defaults to
**— none —** (no encoding: uniform points/lines); pick any technology (phys units)
to encode it. (Cost was dropped as an axis, so it is no longer a color option.)

## 13. API reference

Every endpoint below reads **the dataset named by the request** — `X-Dataset-Id`
header or `?ds=<session id>`, falling back to the shipped default when neither is
present, and 404 when the id is unknown (§16).

| endpoint | method | purpose |
|---|---|---|
| `/api/health` | GET | liveness + session counts (resident / on disk / cap) |
| `/api/meta` | GET | axes, samplers, methods, n, optimum (u\*, c\*, ε), R̂/ESS |
| `/api/projection?method&sampler&dims[&sample]` | GET | 2-D points + index + optimum + (PCA) explained variance & loadings; 425 if an expensive method isn't cached |
| `/api/color?sampler&field&space` | GET | per-point scalar (cost / technology / chain) + min/max + categorical flag |
| `/api/samples?sampler&space&fields[&sample]` | GET | raw per-design values (parallel coords, tooltips) |
| `/api/clusters?method&sampler&k` | GET | k-means region centroids + z-score characterization |
| `/api/extremes?sampler` | GET | 18 LP extreme designs (values, cost, PCA coords) |
| `/api/shadow_pairs` | GET | all 36 axis pairs ranked by shadow boxiness (cached) |
| `/api/dependence?sampler` | GET | 9×9 distance-correlation, mutual-information & Pearson matrices (cached, 1.5k subsample) |
| `/api/shadow` | POST | exact 2-D shadow polygon of the (constrained) polytope |
| `/api/flexibility` | POST | exact remaining [min,max] per axis under constraints |
| `/api/volume` | POST | relative volume of the constrained region vs the full space (subset-simulation estimate; accurate in the tail) |
| `/api/generate` | POST | constraints → feasibility LP → hit-and-run candidates + PCA coords |
| `/api/datasets` | GET | preloaded polytopes available to build from (`{id, name, n_axes}`) |
| `/api/build/preloaded` | POST | `{dataset_id, n_samples}` → build a dataset from a shipped polytope: generate the cloud + eagerly warm every cache; returns `/api/meta` **with the new session id** |
| `/api/build/upload` | POST | multipart polytope-only `.npz` + `n_samples` form field → `validate_polytope`, build, warm; returns `/api/meta` with the new session id |

## 14. Development & deployment workflow

```bash
# everything in Docker
docker compose up --build            # frontend :5173 · backend :8000 (/docs)

# local dev loop (hot reload)
cd backend && $env:DATA_DIR="./data"; python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev           # Vite proxies /api → 127.0.0.1:8000

# quality gates
cd frontend && npm run check && npm run build
```

(No projection cache anymore — PCA is live, t-SNE/UMAP were removed. No samples
file is shipped either; the cloud is generated per §4/§15.)

Gotchas learned along the way:
- **Module-global state made the backend single-tenant** — the "active dataset"
  and every polytope-derived cache (shadows, dependence, extremes, base
  flexibility) were module globals, so a second user's build silently replaced the
  first user's data and served them its cached LPs. Both are now per request /
  per dataset (§16). The rule: if it derives from the polytope, it belongs to the
  dataset, not the module.
- **Docker images are immutable** — source edits need `docker compose build
  <svc> && docker compose up -d <svc>`; "my changes vanished" usually means a
  stale image (check `docker compose ps` creation times).
- **Vite proxy must target `127.0.0.1`, not `localhost`** — Node ≥17 may
  resolve localhost to IPv6 `::1` while uvicorn listens on IPv4 only.
- **Svelte 5 `$effect` discipline** — an effect must never write state it also
  reads (infinite loop; it froze the app once). Layout effects *write*
  scales, render effects only *read* them.
- **regl-scatterplot encoding** — color value goes in the **3rd** tuple slot
  (`valueA`), normalized to [0,1] for continuous data; `zDataType` set
  explicitly.
- **Up to 100k generated points, ≤ 50k displayed** — WebGL for the scatter; canvas
  (not SVG) for the polylines. `/api/projection` and `/api/samples` share one
  seeded `sample=DISPLAY_N` subset so their rows stay positionally aligned;
  precompute/cache anything slower than ~100 ms.
- **Debounce ≠ cancellation** — `clearTimeout` can't stop a fetch already in
  flight; every debounced fetch whose response writes state needs a sequence
  token (`const my = ++seq; … if (my !== seq) return;`) or a response-identity
  check (e.g. shadow responses carry their `x`/`y` pair), or out-of-order
  responses clobber newer state. Found by adversarial review in three places.

## 15. Landing page & dataset building

The app boots to a **landing page** (`frontend/src/lib/Landing.svelte`) rather than
straight into the tool. It:

1. Lists the preloaded polytopes (`GET /api/datasets`) as pickable cards, plus an
   **upload-your-own-polytope** (`.npz`) card. (Only the polytope is needed — a
   few tens of KB — because samples are generated, not uploaded.)
2. Takes a **sample count** (slider + number, default 20k). The UI gates the range;
   the API rejects anything outside 1k–100k with a 422 (it does not clamp).
3. On **Generate & Explore**, calls `POST /api/build/preloaded` or
   `/api/build/upload`. The backend generates the cloud (hit-and-run, §4) and
   **eagerly precomputes** dependence, all shadow pairs, extremes, and base
   flexibility, then returns `/api/meta`. Only then does `App.svelte` flip its
   `started` gate and `reloadAll()` the tool — every view opens against warm
   caches. A **⟳ change dataset** link in the top bar returns here.

Timings (v13 polytope, ~219 rows): build + warm ≈ **5 s @ 20k**, ≈ **13 s @ 100k**
(the eager `shadow_pairs` LPs dominate). The build is one synchronous request; the
button shows a progress state and nginx keeps a long `proxy_read_timeout`.

A build returns a **session id** alongside the meta; see §16.

## 16. Sessions: multi-tenancy & persistence

The backend used to hold one *active dataset* in a module global, with the LP /
dependence caches keyed globally too. That is a single-tenant model with two
visible failures: a second user's build replaces the first user's data (and hands
them its cached LP results), and a page refresh drops everyone back to the landing
page because nothing on the client remembers which dataset it was looking at.

**A dataset is now a session.** `sessions.create` mints a short URL-safe id
(`secrets.token_urlsafe(9)`) and every request names the dataset it wants:

| transport | used by | why |
|---|---|---|
| `X-Dataset-Id` header | the frontend (set once in `api.ts`'s `fetchRetry`) | can't be dropped by a caller assembling a URL |
| `?ds=<id>` query | shared links, curl, `/docs` | makes a dataset linkable |
| *(absent)* | API explorers | falls back to the shipped default |

Resolution is a FastAPI dependency (`current_dataset`); endpoints declare
`ds: Dataset = Depends(current_dataset)` and never consult module state. An id the
backend doesn't know is a **404**, which is the frontend's signal to forget it and
show the landing page.

### Persistence: store the recipe, not the cloud
Hit-and-run is seeded, so `(polytope, n_samples, seed)` *is* the sample cloud.
A session therefore persists as a recipe:

```
SESSION_DIR/<id>/session.json   {source, stem, n_samples, seed, name, created}
SESSION_DIR/<id>/polytope.npz   uploads only (~46 KB — it exists nowhere else)
```

A few KB per session instead of the 1.4 MB (20k) – 7 MB (100k) a snapshot would
cost, and a cold hit regenerates the *same* points (verified byte-identical across
a container restart) for ~5 s @ 20k / ~13 s @ 100k.

### Bounded memory
A warm dataset is tens of MB once its LP caches fill, so only the `MAX_SESSIONS`
(env, default 8) most-recently-used stay resident, LRU-evicted. Eviction is not
loss: the id still resolves, it just pays a rebuild. A **per-id build lock** stops
N concurrent requests from regenerating the same cloud N times after an eviction —
verified with 48 concurrent requests across 4 sessions at cap 2, zero cross-talk.

Every polytope-derived cache now hangs off `ds.cache` (`generate._slot`), so a
fresh dataset starts cold and nothing ever needs invalidating (`reset_caches()` is
gone). `SESSION_TTL_DAYS` (env, default 30) sweeps stale recipes at startup.

### Frontend
The id lives in `localStorage` **and** the URL (`?ds=`), the URL winning so a
shared link opens the dataset it names. On boot `App.svelte` sets the id and tries
`getMeta()`: success enters the tool directly (no landing flash — a `restoring`
gate covers the round-trip), `UnknownDataset` (404) clears it and shows the
landing page. "⟳ change dataset" clears both id and URL param.
