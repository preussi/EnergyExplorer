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
│   (LP) · shadows / flexibility / marginals · generation   │
└──────────────▲────────────────────────────────────────────┘
               │ read-only
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
view (projection, dependence, violins) reads this generated cloud.

**Starting point / feasibility** — the **Chebyshev center**: the LP

```
max r  s.t.  aᵢ·x + ‖aᵢ‖·r ≤ bᵢ ,  r ≥ 0
```

finds the center `x` of the largest inscribed ball. `r > 0` ⇒ the polytope has
an interior (start there); `r ≤ 0` ⇒ **empty** ⇒ the app reports the user's
constraint set as infeasible.

The **sampler walk** overlay (§10.5) animates these chains: consecutive thinned
states drawn as fading comet trails, one color per chain.

## 4b. Measuring how much a constraint restricts the space

There are **two defensible answers**, they disagree, and neither is a special case
of the other. This section exists so the decision can be revisited with the
reasoning intact.

**What the app ships (2026-08-23).** The *volume readout was removed entirely* —
no percentage is shown for either reading (see "Why the readout went away" below).
What survives of B is the thing it was always better at: the **profile violins are
now measured in the projection**, so hiding an axis genuinely reshapes them.

### The two readings

Let `P ⊂ ℝⁿ` be the near-optimal body, `S` the enabled (shown) axes, `C` a
constraint box acting only on axes in `S` (guaranteed: hiding an axis drops its
constraint, §9 of CLAUDE.md).

    A   "what fraction of DESIGNS survive"
        R_A = vol_n(P ∩ C) / vol_n(P)
        estimated by counting the uniform cloud — free, exact in the limit

    B   "what fraction of reachable COMBINATIONS of the shown axes survive"
        R_B = vol_|S|(π_S(P) ∩ C_S) / vol_|S|(π_S(P))
        needs a uniform sample of the PROJECTION — LP hit-and-run, ~5.8 ms/sample

Because `C` touches only `S`, projection and intersection commute
(`π_S(P ∩ C) = π_S(P) ∩ C_S`), which is what makes B well posed.

### The one-line difference

> **A weights a value by *how many ways* it can be realized. B only asks *whether*
> it can be.**

Everything else follows from that sentence.

### Worked example (checkable by hand)

Two technologies, `P = {x ≥ 0, y ≥ 0, x + y ≤ 1}`. Hide `y`. Constrain `x ≥ 0.5`.

    A:  area(P ∩ C)/area(P) = 0.125 / 0.5      = 25%
    B:  len([0.5,1]) / len([0,1])              = 50%

At `x = 0.9` there is almost no room left for `y`, so those designs are **rare** —
A notices, B cannot. Same body, same constraint, factor of two.

### Consequences of choosing B

| | under B |
|---|---|
| volume readout | `R_B` — **not monotone** when axes are removed (*removed from the UI*) |
| violins | uniform-on-projection: density ∝ the (k−1)-D slice — **this is what ships** |
| ⇒ with k = 1 | `π_i(P)` is just `[min, max]`, so the violin is **flat** |
| dependence matrix | dCor/MI would change (different measure on the same axes) |
| facet outlines | **unchanged** — projection composes, `π_ij(π_S(P)) = π_ij(P)` |
| flexibility bands | **unchanged** — `max{xᵢ : x ∈ π_S(P)} = max{xᵢ : x ∈ P}` |
| shape categories | unchanged (they derive from the facets) |

**Monotonicity.** A common intuition is that fewer dimensions must retain ≥ volume.
That is true for *A with dropped constraints* — every sample projects into the
reduced space so the denominator is fixed at `N`, and dropping a constraint can only
add survivors. It is **false for B**, because B moves the denominator too. Measured
on polytope_13, constraining `wind_onshore` (exact k=2 path, no sampling noise):

| constraint | A (full 9-D) | B (projected 2-D) |
|---|---|---|
| middle band 40–60% | 26.07% | 22.52% |
| lower edge 0–20% | 25.72% | 24.85% |
| **upper edge 80–100%** | **0.34%** | **10.34%** |
| wide middle 20–80% | 73.95% | 64.81% |

Both directions occur. The upper-edge row is the clearest illustration of the whole
distinction: high onshore wind is *reachable* but only under a knife-edge
configuration of the other seven technologies.

### What is actually implemented (2026-08-23)

- `generate.py:projected_marginals` draws a uniform sample of `π_S(P)`, in physical
  units, restricted to the shown axes. **No projection is ever constructed** —
  Fourier–Motzkin explodes on this polytope (measured: one axis 219 → ~2k–9.4k rows,
  two → ~19M, OOM). Instead hit-and-run runs *inside* `π_S(P)` using the original
  system: from `q` in direction `d` the chord endpoints are
  `max/min t s.t. A x ≤ b, x_S = q + t·d`, two LPs per step
  (`generate.py:_projection_chord`).
- Served by `POST /api/marginals` `{axes, n}`; `n` clamps to 200–4000, default 1500.
  Cached on `ds.cache["proj_marginals"]` keyed by the **axis set** (order-independent),
  so toggling an axis back, or merely reordering the rows, is free.
- **`|S| = n` short-circuits**: `π_S(P) = P`, so the base cloud is returned as-is
  (capped + seeded at 20k rows for direct API callers). The frontend does not even
  send the request in that case — it already holds the cloud.
- **Measured cost** ~3.5 ms/sample: 6.4 s for the default 1500 on a cold axis set,
  0.02 s cached. The frontend debounces 350 ms and dims the violins while in flight.

**Verification.** For `|S| = 2` the projection *is* the facet shadow we already
compute, which gives an independent reference: rejection-sampling uniformly inside
the exact shadow polygon of `photovoltaics × wind_onshore` and comparing marginals,

| | KS vs the exact-polygon reference |
|---|---|
| `projected_marginals` (B) | **0.009** / 0.017 — sampling noise at n = 1500 |
| the full-space cloud (A) | **0.218** / 0.232 |

and every returned point lies inside the exact polygon. So the sampler is uniform
on `π_S(P)`, and B is a genuinely different distribution from A rather than a
noisier version of it.

### Why the readout went away

The `R_B` percentage (and the `R_A` one before it) were removed from the UI on
2026-08-23. The reasoning, which is *not* that the math was wrong:

- A single headline percentage reads as a much harder number than a Monte-Carlo
  volume ratio over a 9-D polytope can be.
- The same constraint produced two different percentages depending on which axes
  happened to be *shown* — a display toggle silently changing a headline figure.
- `R_B`'s non-monotonicity under hiding an axis (the table above) is correct and
  almost impossible to explain in a rail with no room for a paragraph.

What the UI says about a constraint is now only what it can state **exactly**: the
LP feasibility verdict, and the remaining `[min, max]` on every row. The in-region
sample *count* survives, but only where it is a statement about the cloud rather
than about the space — deciding whether to offer a resample.

`generate.py:volume_ratio` (subset simulation) and `projected_volume_ratio`, and
the `POST /api/volume` endpoint, were deleted along with it. §10.12 below is kept
as a record of the subset-simulation method, which is worth having if the readout
is ever revived.

### The inconsistency that used to be here

Previously: *the violins showed A while the volume showed B.* Both halves of that
are now resolved — the volume is gone, and the violins are B. The deferred cost
(~5.8 ms/sample estimated then; ~3.5 ms/sample measured now) is paid on a debounced,
cached, per-axis-set basis rather than on every interaction, which is what made it
affordable.

Measured shape of the change (photovoltaics violin, mass in the outer 20% of the
range): full-space marginal 24.4% → k=2 projection 34.0% → k=1 uniform 36.4% (flat).
The k=1 flattening is real and expected: with one axis shown, "the reachable
combinations" is just the interval, and every value in it is reachable.

**Still A, deliberately:** the dependence matrix (dCor/MI), the facet outlines and
the flexibility bands. The last two are *invariant* (`π_ij(π_S(P)) = π_ij(P)`, and
per-axis extents survive projection), so they are not inconsistent at all. The
matrix is a real remaining asymmetry: making it B would change dCor under a display
toggle, which is hard to explain and much harder to justify than reshaping a density.

### How to switch the violins back to A

One line: stop passing `marginalValues={pcMarginals}` to `ParallelCoords` in
`App.svelte`. The component falls back to the base cloud when it is null, which is
exactly A. (It also falls back on a failed fetch — and says so in the Profiles
header, because silently drawing a different distribution is the failure mode this
whole section is about.)

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

> **REMOVED FROM THE APP (2026-08-23).** Neither this readout nor its projected
> variant is shown any more, `POST /api/volume` and `generate.py:volume_ratio` are
> deleted, and the Consequences strip now carries only the exact LP feasibility
> verdict. See §4b, "Why the readout went away", for the reasoning. The section is
> kept because the subset-simulation method is the non-obvious part and is worth
> having written down if the readout is ever revived. What survives in code is the
> in-region **sample count**, used only to decide whether to offer a resample.

The Consequences panel answered *"how much smaller does the near-optimal space get
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

### 10.14 Guided tour (`Tour.svelte`, **? guide** in the top bar)
A spotlight walkthrough: four dimmed panels leave a real gap around the element a
step is describing (rather than an SVG mask), so the highlighted control **stays
clickable** — the point is to teach the buttons, not to lock the user out. Auto-runs
once on a first visit (`ee.tourSeen`), reopenable from the top bar.

It teaches a **question**, not a control panel: each step sets the app up for what
it is about to explain (switches view, opens the wind × wind facet, applies the
constraint), so the user watches the real views react. The scenario is *"what if we
build essentially no offshore wind?"* — capping the lever at 5 % of its own
feasible maximum.

**Why that scenario, and why it has a second half.** Measured on the v13 polytope:

| constraint | space retained | other levers moved | forced (gained a floor) |
|---|---|---|---|
| `wind_offshore ≤ 57` (5 %) | 11.7 % | 0 (one changes by <0.05 %) | none |
| `wind_onshore ≤ 220` (5 %) | 2.5 % | 5 | photovoltaics |

Ruling out offshore wind is a **free choice** here: it costs you 88 % of the design
space but forces no other decision. Ruling out onshore wind does not — PV picks up
a floor. A tour that only did the first case would look like the tool does nothing,
so the walkthrough runs both and the contrast *is* the lesson. This is §10.8's
"weakly-coupled, corner-trimmed box" showing up in the UI.

The step text never asserts specific numbers (an uploaded polytope will have its
own), and the axis pair falls back to the first two axes if a dataset has no
`wind_offshore`/`wind_onshore`. Steps carry an explicit `setup` tag rather than
deriving behaviour from their title or target — two steps share the flexibility
panel, and matching on prose breaks the moment the wording or an axis name changes.

## 10b. Facet shape taxonomy (corner gaps)

### Why the classification is complete, not a taste call

A facet `F = π_ij(P)` is an orthogonal projection of a convex body, so it is
**always convex** — no holes, no reflex vertices, no disconnected pieces. Let `B`
be its tight bounding box. Each of `x_min, x_max, y_min, y_max` is attained by some
point of `F`, so **F touches all four sides of B**. The sides therefore carry no
information, and the only structural freedom left is *which corners of B the facet
cannot reach*. There are four. That is what makes this taxonomy exhaustive rather
than a list of shapes someone happened to notice.

### Normalisation

    u = (x − x_min)/(x_max − x_min)      v = (y − y_min)/(y_max − y_min)

maps `F` into the unit square, still touching all four sides. Scale- and unit-free,
so a GW×GW pair and a GW×ktCO₂/h pair are directly comparable.

### The corner gap

For each corner `c ∈ {(0,0), (1,0), (0,1), (1,1)}`:

    g_c = min ‖p − c‖∞ = min max( |p_u − c_u| , |p_v − c_v| )
         p ∈ F̂         p ∈ F̂

`g_c ∈ [0,1]`, and `g_c = 0 ⟺ c ∈ F̂` — that pair of extremes is simultaneously
attainable.

**Why L∞ and not Euclidean.** The L∞ ball around a corner is an axis-aligned
square, so `g_c` is exactly *the largest t such that no feasible design has both
coordinates within t of their respective extremes*. That is the decision statement
verbatim. Euclidean distance blends the two shortfalls into a number with no such
reading — a point very close in `u` and far in `v` would still score small.

Two identities fall out:

    g_UR = 1 − max min(p_u, p_v)     the highest level BOTH can simultaneously reach
    g_LL =     min max(p_u, p_v)     the lowest level BOTH can simultaneously be held to

### Computing it exactly

`F̂` is a hull-ordered polygon. Ray-cast to test whether `c` is inside (→ 0);
otherwise minimise over each edge. Along an edge `t ↦ max(|a+bt|, |d+et|)` is a max
of two convex functions, hence convex, so **ternary search yields the exact
minimum** — 80 iterations shrink the bracket by `(2/3)⁸⁰ ≈ 1e-14`. No sampling
grid, so no resolution artefacts. (`generate.py:corner_gaps`.)

The one approximation is upstream: the polygon is the hull of 72 support-LP
maximisers, so it is *inscribed* in the true shadow. A vertex whose normal cone is
narrower than 5° could be clipped, which would slightly **over**state a gap.

### The six categories

With `H = { c : g_c ≥ θ }`:

| H | category | statement |
|---|---|---|
| ∅ | `independent` | no corner cut — choose the two separately |
| ⊇ {LL, UR} | `band` | substitute one-for-one; the total is pinned |
| ⊇ {UL, LR} | `locked` | must move together |
| {UR} | `tradeoff` | cannot both be large |
| {LL} | `at_least_one` | cannot both be small |
| {LR} or {UL} | `dependency` | **directional** — one at scale requires the other |

**Test order matters**: the diagonal pairs are checked *before* the single corners,
because a facet cutting both LL and UR is a band, not a trade-off with a footnote.

**Why the diagonals differ.** `{LL, UR}` is the **sum** diagonal — cutting UR bounds
`x+y` above, cutting LL bounds it below; both ⇒ `x+y` confined ⇒ a diagonal band ⇒
strict substitution. `{UL, LR}` is the **difference** diagonal — both ⇒ `x−y`
confined ⇒ lockstep.

### The threshold is a reporting choice, not a noise floor

A corner that is genuinely attained is the maximiser for *every* direction in its
quadrant, so 72 directions always recover it: `g_c = 0` is exact. The inscribed-hull
error is bounded by `R(1 − cos(Δθ/2)) ≈ 7e-4` on the unit square. **Numerical noise
is ~1e-3, not 0.15.** So `θ` answers "how much of a corner must be cut before it is
worth reporting", and nothing else.

A data-driven `θ` was **tried and rejected**. The largest jump in the sorted
non-zero gaps on `polytope_13` falls at **0.216**, which reclassifies
`DAC × biomass` — the only `at_least_one` pair, gap 0.18 — and
`photovoltaics × biomass` (0.16) as `independent`. The distribution is dominated by
the 21 trade-offs at 0.25–0.42, so any break-finder tracks *that* cluster and
swallows the rare categories. Categories are not separable by gap magnitude; only
materiality is.

Hence: a documented default (`GAP_EPS = 0.15`, clear of the near-box pairs which
top out at 0.11 and the trade-offs which start at 0.25), **exposed as a slider** so
the sensitivity is visible, with the continuous gap always shown and threshold-
sensitive calls marked *borderline*. Sensitivity on v13:

| θ | tradeoff | dependency | at_least_one | independent |
|---|---|---|---|---|
| 0.10 | 23 | 5 | 1 | 7 |
| **0.15** | **21** | **4** | **1** | **10** |
| 0.20 | 21 | 3 | 0 | 12 |
| 0.25 | 21 | 2 | 0 | 13 |

### Where it lives

`generate.py` computes the gaps (cached with the shadow) and classifies at the
default θ for API consumers. The frontend re-classifies from the raw gaps
(`lib/facets.ts`) so the slider is instant and needs no refetch. **The two
implementations must agree** — verified across all 36 pairs including the
directional `needs`, 0 disagreements.

### Result on polytope_13

`21 tradeoff · 10 independent · 4 dependency · 1 at_least_one · 0 band · 0 locked`

Cross-checked against independent LPs: pinning nuclear to its lowest *or* highest
2% leaves PV's range untouched (a true box); maxing `ccs_lump` lifts biomass's floor
to 89% of its range; pinning DAC low forces biomass to 61% of its. `band`/`locked`
are absent because ε = 0.1 is generous and the other seven technologies absorb
almost any pair you constrain — they are detected so a tighter or uploaded polytope
is not mislabelled as one of the simple cases.

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
| `/api/marginals` | POST | uniform sample of `π_S(P)` over the shown axes — the distribution the Profiles violins draw (§4b) |
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
