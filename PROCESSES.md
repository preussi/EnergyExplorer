# Energy Explorer — Processes & How They Work

Technical companion to [DESIGN.md](DESIGN.md) (architecture/roadmap) and
[README.md](README.md) (quickstart). This document explains **every process in
the system**: where the data comes from, the math behind each transformation,
how each visualization is computed, and how the pieces interact.

---

## Contents

1. [System architecture](#1-system-architecture)
2. [Data provenance — where the 20,000 solutions come from](#2-data-provenance)
3. [The near-optimal polytope](#3-the-near-optimal-polytope)
4. [Uniform sampling — how hit-and-run works](#4-uniform-sampling--hit-and-run)
5. [Normalized vs physical space](#5-normalized-vs-physical-space)
6. [Projections (PCA / t-SNE / UMAP)](#6-projections)
7. [Cluster labels](#7-cluster-labels)
8. [Candidate generation (steering)](#8-candidate-generation-steering)
9. [Extreme designs (MGA alternatives)](#9-extreme-designs)
10. [Visual analytics overlays](#10-visual-analytics-overlays)
11. [Interaction model](#11-interaction-model)
12. [Color pipeline](#12-color-pipeline)
13. [API reference](#13-api-reference)
14. [Development & deployment workflow](#14-development--deployment-workflow)

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
│   loads .npz at startup · projections (PCA live, t-SNE/    │
│   UMAP cached) · clusters · extremes (LP) · generation     │
│   (LP feasibility + hit-and-run)                           │
└──────────────▲────────────────────────────────────────────┘
               │ read-only volume
        data/polytope.npz · data/polytope_samples.npz
```

- The **frontend** never computes statistics on the 10-D data; it renders what
  the backend serves and handles interaction (selection, brushing, pinning).
- The **backend** holds everything in memory (the data is ~6 MB) and caches
  expensive results (t-SNE/UMAP to disk, extremes in memory).

## 2. Data provenance

The 20,000 solutions were produced **upstream** of this project by an
energy-system MGA pipeline (provenance is stamped in
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
10-D, so an MCMC random walk is used. One hit-and-run step from point `x`:

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

The app's own generator (`backend/app/generate.py`) implements the HAR variant
in ~40 lines of numpy (burn-in 250, thinning 8) — sufficient for interactive
candidate generation; `hopsy` remains the rigorous option for final results.

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

10-D → 2-D for the map view. Computed on **normalized technology axes only**
(9-D; cost is excluded from the geometry and encoded as color).

| method | runs | optimum placement | axis meaning |
|---|---|---|---|
| **PCA** | live (<100 ms) | exact (`transform`) | linear combos; loadings shown |
| **t-SNE** | precomputed → `backend/cache/` | embedded jointly at build time | non-metric |
| **UMAP** | precomputed → cache | embedded jointly | non-metric |

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
*Reading it:* the samples are uniform in 10-D, so 2-D density = the polytope's
**thickness along the 8 projected-out dimensions**. Ridges = mixes with many
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

### 10.8 Facet views (exact 2-D shadows) — the "Facets" tab
Why: a 2-D projection of this 10-D body is *supposed* to look like a filled
blob — measured: the strongest tech-tech correlation is |r|=0.36, the PCA
spectrum is nearly flat (28/16/12/11/11/8/6/5/2 %), and 23/172 constraints are
plain per-tech bounds, so the body is a weakly-coupled, corner-trimmed box.
The information lives in the **boundary**, so the Facets tab shows it exactly:
- `GET /api/shadow_pairs` ranks all 45 axis pairs by **boxiness** = area(exact
  shadow polygon) / area(its bounding box). Low boxiness = real trade-off facets
  (best: CCS×biomass 0.56, CCS×cost 0.61, DAC×cost 0.62; worst: every
  nuclear pair ≈ 1.0 — nuclear is decoupled from everything).
- `POST /api/shadow {x, y, constraints}` computes the polygon by
  **support-function LPs**: for 72 directions θ, maximize cosθ·xᵢ + sinθ·xⱼ
  over `A·x ≤ b` (+ user constraint rows); the maximizers' convex hull is the
  exact orthogonal projection of the polytope. Unconstrained shadows are cached.
- UI: ranked small multiples (top 10) → enlarged facet with the sample cloud,
  the exact white boundary, the optimum, and — when constraints/brushes are
  active — the **constrained polygon** in teal (debounced 300 ms).
*Reading it:* samples thin out near the boundary because the body's thickness
→ 0 there — the boundary designs are feasible but have few variations.

### 10.9 Remaining-flexibility bars
`POST /api/flexibility {constraints}` solves 2 LPs per axis (20 total, ~50 ms)
for the **exact** remaining [min, max] of every technology + cost under the
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
pairwise measures over the 10 axes (`GET /api/dependence`, cached per sampler,
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
identical results. The heatmap cell click opens that pair as an exact Facet, and
the Facets tab's ranking dropdown can re-sort the 45 pairs by any of these
instead of boxiness. A companion scatter plots **dCor vs (1 − boxiness)** per
pair: the up-right trend is a built-in cross-check that the sampled cloud's
dependence matches the polytope's exact facet structure (a sampler sanity test).

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
color so 20k lines ≈ 64 stroke calls), and the legend. Color-by: cost (default),
any technology (phys or norm units), or chain id.

## 13. API reference

| endpoint | method | purpose |
|---|---|---|
| `/api/health` | GET | liveness |
| `/api/meta` | GET | axes, samplers, methods, n, optimum (u\*, c\*, ε), R̂/ESS |
| `/api/projection?method&sampler&dims[&sample]` | GET | 2-D points + index + optimum + (PCA) explained variance & loadings; 425 if an expensive method isn't cached |
| `/api/color?sampler&field&space` | GET | per-point scalar (cost / technology / chain) + min/max + categorical flag |
| `/api/samples?sampler&space&fields` | GET | raw per-design values (parallel coords, tooltips) |
| `/api/clusters?method&sampler&k` | GET | k-means region centroids + z-score characterization |
| `/api/extremes?sampler` | GET | 18 LP extreme designs (values, cost, PCA coords) |
| `/api/shadow_pairs` | GET | all 45 axis pairs ranked by shadow boxiness (cached) |
| `/api/dependence?sampler` | GET | 10×10 distance-correlation, mutual-information & Pearson matrices (cached, 1.5k subsample) |
| `/api/shadow` | POST | exact 2-D shadow polygon of the (constrained) polytope |
| `/api/flexibility` | POST | exact remaining [min,max] per axis under constraints |
| `/api/volume` | POST | relative volume of the constrained region vs the full space (subset-simulation estimate; accurate in the tail) |
| `/api/generate` | POST | constraints → feasibility LP → hit-and-run candidates + PCA coords |
| `/api/upload` | POST | multipart polytope + samples `.npz` → validated (schema-checked) and swapped in as the active dataset; clears all polytope-derived caches |
| `/api/reset` | POST | discard an uploaded dataset and restore the shipped default |

## 14. Development & deployment workflow

```bash
# everything in Docker
docker compose up --build            # frontend :5173 · backend :8000 (/docs)

# local dev loop (hot reload)
cd backend && $env:DATA_DIR="./data"; python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev           # Vite proxies /api → 127.0.0.1:8000

# quality gates
cd frontend && npm run check && npm run build

# projection cache (t-SNE/UMAP; persisted via the ./backend/cache volume)
docker compose exec backend python -m app.projections [--force]
```

Gotchas learned along the way:
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
- **20k points** — WebGL for the scatter; canvas (not SVG) for 20k polylines;
  precompute/cache anything slower than ~100 ms.
- **Debounce ≠ cancellation** — `clearTimeout` can't stop a fetch already in
  flight; every debounced fetch whose response writes state needs a sequence
  token (`const my = ++seq; … if (my !== seq) return;`) or a response-identity
  check (e.g. shadow responses carry their `x`/`y` pair), or out-of-order
  responses clobber newer state. Found by adversarial review in three places.
