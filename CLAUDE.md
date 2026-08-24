# Energy Explorer — CLAUDE.md

Interactive exploration of **near-optimal energy-system designs** (MGA /
"Modelling to Generate Alternatives" space). ETH semester project with the Energy
Science Center. The point is to give decision-makers *agency*: instead of one
"optimal" answer, let experts **see / filter / steer** the space of designs within
a few % of cost-optimal.

See `DESIGN.md` (architecture + roadmap), `PROCESSES.md` (the math behind every
process — read this before touching projections, sampling, or generation), and
`README.md` (quickstart). `docs/OPTIMUM_DATA_ISSUE.md` documents a known upstream
data problem (see below).

## Architecture

Two containers (Postgres planned, not yet present):

```
frontend (nginx)  Svelte 5 + Vite + regl-scatterplot
   WebGL scatter of 20k designs · parallel coords · radar glyphs · overlays
   /api/* proxied to backend
backend (FastAPI)  numpy + scikit-learn + scipy
   loads the polytope · GENERATES the sample cloud (hit-and-run) at build time ·
   PCA live (only projection) · clusters · extremes (LP) · generation
```
The app opens on a **landing page**: pick a preloaded polytope (or upload one) +
a sample count → a "Generate & Explore" button builds the dataset (generates the
cloud + precomputes every view) and enters the tool. There are two tabs:
**Coupling** (default, the dependence matrix) and **Map**. The exact facet view
has no tab — clicking a matrix cell docks it beside the matrix, and ⛶ full view
expands it over the matrix; the **Profiles rail** (parallel coords / violins) has
no tab either — it is the **left rail** of both views. Projections offered: **PCA** +
**STAR** (star coordinates, frontend). t-SNE/UMAP were removed.

**The old control sidebar is gone** (removed 2026-08). The screen is
Profiles-rail-left, graph-right. Everything the sidebar held now sits next to what
it affects — see *Where the controls live* below. **Slicing was removed too**:
dragging along a row does the same job, so the slider was a second way to say it.

## Layout

- `backend/app/` — `main.py` (routes), `data.py` (`Dataset`, polytope loading,
  **sample generation**, norm↔phys), `projections.py` (PCA only, live),
  `generate.py` (hit-and-run + LPs: sampling, steering, shadows, extremes, flex).
- `backend/data/` — `polytope_NN.npz` only (versioned; currently **v13**). The
  loader (`data.py:_resolve`) auto-picks the newest suffix. **No samples file** —
  the cloud is generated from the polytope at build time (see Data below).
- `frontend/src/lib/` — `Landing` (entry page), `ScatterGL`, `ParallelCoords`,
  `FacetView`, `DependenceMatrix`, `RadarGlyph`, `StarWheel`, `ConsequenceStrip`,
  `Tour` (guided walkthrough), `api.ts`, `cluster.ts` (axis ordering), `colors.ts`.

### Where the controls live

The layout numbers are custom properties on `.stage` (`--edge`, `--graph-top`,
`--rail-w`, `--graph-left`) — `.graph-region`, `.pc-panel`, `.tray`,
`.settings-pop`, `.map-tool` and `.layers-pop` all derive from them. They used to
be copy-pasted and drifted. **Grep for a stale `var(--…)` after changing these**: a
`calc()` naming a dropped property silently voids the whole declaration.

- **`ParallelCoords` is ROTATED**: each axis is a horizontal **row**, not a vertical
  column, so it fits the rail. `scales[a]` maps value → **x**; `yPos[a]` is the row
  centre; `rowH` is the pitch. A design is still one polyline, running top-to-bottom.
  Brushing drags **sideways**; `nearestAxis` matches on **clientY**. If you touch the
  drawing code, remember violin density is thickness in **y** and extent in **x**.
- **Profiles rail** (`.pc-panel`): `.pc-head` · `.pc-bar` (the typed-limit entry,
  nothing else) · `.pc-body` (the rows, the only flexing child) · `ConsequenceStrip`.
  **Render the strip unconditionally** — one that appears with the first constraint
  resizes the canvas and rebuilds every axis scale mid-interaction.
- **Brushing a row is the ONLY way to constrain.** A typed min/max entry existed
  alongside it and was removed: a typed limit and a brush on the same axis both
  narrowed it with no way to see which did what. `allConstraints === brushConstraints`.
  The guided tour drives its demo through `pcoords.setBrush()` so the demo constraint
  is a real, visible brush rather than one materialising from nowhere.
- **Don't re-add a constraint chip list or a per-lever "forced/capped" list.** Both
  existed and both were deleted as duplicates: a constraint is already legible on
  its row (amber band, brush rect, "% left"), and in a rail they truncated.
- **The rail's fixed heights are load-bearing, and so is `halfOf`'s cap.**
  `.pc-head` / `.vol-slot` / `.lim-slot` / `.pc-key` are all `flex: none` with a
  FIXED height so that constraining something cannot steal height from `.pc-body` —
  that would resize the canvas and re-pitch every violin mid-drag. (Verified:
  `.pc-body` is the same height idle and constrained.) The corollary is that those
  slots are reserved space, so keep them as small as their content allows.
  Separately, `ParallelCoords.halfOf` caps the row half-thickness: at 13 a 9-axis
  rail drew ~43 px of ink into a ~76 px pitch and the panel read as mostly empty.
  It is 22 now. If you change it, move the min/max numbers with it — they sit at
  `y + half + 13`, and at the old `+ 3` they land *inside* the flex band.
- **The per-lever consequences sit directly under the rows, above the key.** They
  used to be at the very bottom, below a static legend, which buried the one block
  in the rail that responds to what you just did. The idle text was also deleted:
  the headline already says "whole space · drag along a row to narrow it", so
  "nothing constrained yet" underneath it was a second placeholder saying the same
  thing.
- **Per-axis flexibility is drawn on the row**, not in a side panel (`FlexBars` is
  gone). `ParallelCoords` takes `flexRanges` (current) + `flexBase` (unconstrained)
  + `optimum`, and renders an amber band inside a grey track, the surviving
  `min`/`max` under the row's two ends, "% left" in the label gutter, and a white
  optimum tick. Grey track and % appear only once the two ranges differ, so an
  unconstrained view stays clean. **All keyed by axis NAME**: columns are permuted by
  `pcCols` and can come from a candidate set, so index-keying lands on the wrong row.
- The gutter stacks the name and "% left" on **two lines**. They were on one axis
  head originally and overlapped.
- **Axis-name menu** (click a row's name): clear constraint / hide. Its handler
  must `stopPropagation` — the SVG beneath treats a click as "clear brush".
- **The Map camera is bounded** so you can't pan the cloud off-screen or zoom into
  nothingness. regl-scatterplot builds its camera itself and never forwards
  `scaleBounds`/`translationBounds`, so `ScatterGL.applyCameraBounds()` reaches
  through `scatter.get("camera")` (a dom-2d-camera, which does support them). Points
  are normalized to clip space `[-1,1]·(1-PAD)`, so the bounds are projection-
  independent. Re-apply after `reset()` — it re-runs `initCamera`.
- **Generate is contextual**, not a standing panel: `ConsequenceStrip` offers
  "resample this region" only when in-region samples < `VOL_TRUST_K` (200), which
  is the one case the base cloud genuinely can't show.
- **`inRegion` must count the base cloud (`samples`), never `dispValues`.**
  In candidate mode `dispValues` *is* the constrained region (hit-and-run run inside
  it), so every row passes and the count is meaningless. It is a statement about the
  CLOUD (is the sample dense enough to draw?), never about the size of the space —
  that is the whole reason the percentage below is gone.
- **There is no "% of the space left" readout, and don't re-add one.** Two
  defensible measures existed and both were removed (2026-08-23). **A** = what
  fraction of *designs* survive (count the cloud). **B** = what fraction of reachable
  *combinations of the shown axes* survive (re-based on `π_S(P)`). A weights a value
  by how many ways it can be realized; B only asks whether it can be. They disagree
  in **both** directions — on v13, capping `wind_onshore` into its top 20% gives
  A = 0.34% but B = 10.34%, so B is **not monotone** under hiding axes (correct, not
  a bug). What sank the readout was not the math: one headline percentage implied a
  precision a Monte-Carlo volume over a 9-D body does not have, and it changed
  meaning when a *display* toggle changed which axes were shown. What the strip says
  now is only what is exact — the LP feasibility verdict. `POST /api/volume`,
  `volume_ratio` and `projected_volume_ratio` are deleted. **PROCESSES.md §4b.**
- **The Profiles violins ship worldview B: they are measured in the PROJECTION onto
  the shown axes.** This is what makes hiding an axis reshape them instead of being
  a no-op — see the "hiding ≠ constraining ≠ marginalizing" note below, which still
  holds for everything else. `POST /api/marginals` returns a uniform sample of
  `π_S(P)` (`generate.py:projected_marginals`, hit-and-run whose chord comes from an
  LP in the lifted space — the projection is never constructed, Fourier–Motzkin OOMs
  here). ~3.5 ms/sample, so it is debounced 350 ms and cached per **axis set**
  (order-independent, so reordering rows is free). Verified for `|S| = 2` against a
  uniform sample of the exact shadow polygon: KS 0.009 (the full-space cloud scores
  0.218 against the same reference).
  - `ParallelCoords` takes `marginalValues` and uses it for the ghost violin **and**
    the brushed conditional one, so both come from the same distribution. `selected`
    still indexes the base cloud and still drives the polylines and the count —
    don't reuse it to index the projected sample, they are different row sets.
  - With every axis shown `π_S(P) = P`, so the frontend skips the request entirely
    and the base cloud is used. On a failed fetch it falls back to A and **says so**
    in the Profiles header — a silent fallback here draws a different distribution
    under the same label.
  - Still A, deliberately: the dependence matrix. The facet outlines and flexibility
    bands are *invariant* under projection, so they are not inconsistent at all.
- **Topbar: identity left, view centre, actions right.** It is **pinned flush to
  the top, full width, square** (`border-radius: 0`, bottom border only) — it is
  chrome, not a floating card, and once the page background went flat a rounded
  bar inset by 16 px left a strip of page above it that read as a gap. Left is the
  wordmark + a **dataset chip** (name + compact count; clicking it is "change
  dataset"); right is the Map's gesture hint, then `?`, then `⚙`. It used to be
  the wordmark plus three identically-styled word pills on the LEFT, which gave
  the rarest and most disruptive action the most prominent slot and left the
  loaded dataset's name visible only inside Settings.
  `--bar-h` (60 px) is the single source of truth: `--graph-top`, `.settings-pop`
  and the map inset all derive from it — **including `App.mapInset`'s `BAR_H`,
  which is a JS mirror and will not follow the CSS on its own.** Order at the right
  end is `⚙` then `?`.
- **The right cell of the topbar is Map-only** (besides the buttons). The Coupling
  sentence was deleted — the matrix already has a title, a metric toggle, a colour
  legend and a per-pair caption, so it was a paragraph restating the panel below
  it. The hint ellipsises rather than wrapping; the bar is a fixed height and
  wrapping text resized it.
- **The Map's controls are ONE right-hand column**: the `▤ layers` button at the
  graph's top-right, and the **Pinned designs** tray directly under it, both on
  `right: var(--edge)` so their right edges line up. The tray used to sit in the
  top-LEFT corner, so the map had a floating box in each corner and no relationship
  between them. `.layers-pop` opens into the tray's slot and covers it while open —
  fine for a transient menu.
- **`.settings-pop` anchors top-RIGHT**, under its button, sharing that corner with
  `.layers-pop`. That is safe only because `showSettings`/`showLayers` are mutually
  exclusive — keep them that way. It covers the `▤ layers` button while open, which
  is fine for a transient panel.
- **Settings is ordered by frequency and SCOPED to the view**: Color by → Visible
  axes → (Facet shapes *in Coupling* | Map clusters *on the Map*) → Appearance
  (theme + reduce motion) → Dataset. Two of the six sections used to be inert in
  whichever view you were in. The "visible axes" paragraph must describe worldview
  B — it used to say hiding was "display only" and the violins were "already
  marginals over the full space", which stopped being true (see above).
- **The Map canvas is FULL-BLEED and the data is not.** `.graph-region.bleed` is
  `inset: 0`, so the cloud runs under the top bar and the Profiles rail with no
  seam; `ScatterGL`'s `inset` prop then biases the normalizers so the cloud still
  centres on the *visible* window. Without it a fifth of the cloud hides behind the
  rail. Bias the NORMALIZERS, not the camera: points, contours, pins, overlays and
  the hit-test all go through `nx`/`ny`, so one change keeps them consistent —
  `invScales` and the camera's `translationBounds` have to follow (both do).
  Because the normalizers now depend on the container size, a resize must
  `redraw()`, not just reposition overlays. The Coupling matrix deliberately does
  **not** bleed: half a cell behind a panel is unreadable in a way a point cloud
  is not. `App.mapInset` mirrors the `.stage` custom properties — keep them in step.
- **Map-only controls** (projection, StarWheel, overlays, drag mode, reset) live in
  a floating `▤ layers` popover at the graph's **top-right**. It shares its corner
  with nothing, but `.settings-pop` (top-left) and the compare `.tray` (also
  top-left, Map only) do overlap — settings is transient, so that is deliberate.
  `showLayers` and `showSettings` are still mutually exclusive.
- **Settings** holds what is global: visible axes, Color by, cluster k, reduce
  motion, dataset readout.

## Data — 9 axes = 9 technologies (cost is NOT a design axis)

**Units** (shipped in the npz as `units`, surfaced via `/api/meta`, abbreviated by
`colors.ts:shortUnit`):

| axes | quantity | unit |
|---|---|---|
| nuclear · photovoltaics · wind_offshore · wind_onshore · electrolysis | installed power capacity | **GW** |
| battery · biomass | energy capacity | **GWh** |
| DAC · ccs_lump | capture-rate capacity | **ktCO₂eq/h** |
| net_present_cost (not an axis) | total system cost | **MEur** — `c_star` ≈ 1.259e6 MEur (~€1.26 tn), `epsilon` = **0.1** |

`ccs_lump` is a **lumped** axis over six technologies (`BF_BOF_CCS`,
`biomass_plant_CCS`, `cement_post_comb`, `natural_gas_turbine_CCS`, `NG_DRI_CCS`,
`SMR_CCS`) — `/api/meta.axis_members` carries the membership. Note `epsilon` is 0.1
in v13, i.e. designs within **+10%** of cost-optimal; PROCESSES.md §1 still says 0.05.

Axes: `nuclear, photovoltaics, wind_offshore, wind_onshore, electrolysis, DAC,
battery, ccs_lump, biomass`.

- `polytope_NN.npz` — `A`(m,10), `b`, `X`, `name_list` (10, last = cost). At load,
  `data.py` **Fourier–Motzkin projects cost out** → a clean **9-D** technology
  polytope `{x : A·x ≤ b}` (~219 rows) with near-optimality (`cost ≤ (1+ε)c*`)
  baked in. **`z_star`** = the cost optimum (9-D techs); **`u_star`** = per-axis
  fmax normalization maxima (`phys = norm × u_star`), **NOT** the optimum.
- **Samples are generated, not shipped** (2026-07 migration; the old
  `polytope_samples_NN.npz` was dropped). `Dataset.__init__` runs uniform
  **hit-and-run** (`generate.hit_and_run` from the Chebyshev center) over the 9-D
  polytope to produce the normalized cloud. The count is chosen on the landing
  page (default **20k**; outside **1k–100k** the build endpoints reject with 422 —
  they do not clamp), seeded (42) for reproducibility.
  A single generated chain → `diagnostics.rhat`/`ess` are **empty** (not
  meaningful); the file-based default (`load_default`) generates `DEFAULT_N_SAMPLES`
  (env, 20k) lazily. `Dataset` still accepts a `samp` npz mapping for back-compat.
- **Display payload cap.** The cloud may be 100k designs, so `/api/projection` and
  `/api/samples` both take `sample=N` and serve the **same** seeded subset
  (`_display_subset`, rng 42, sorted). The frontend passes one `DISPLAY_N` (50k) to
  both, which is why projected points and raw rows line up positionally — change
  one call site and you must change the other.

## Demo dataset (synthetic, for workshops)

`backend/data/polytope_0_infrastructure_demo.npz` — "Riverside city plan (demo)",
**8 axes**, built by `backend/scripts/make_demo_polytope.py`. A municipal capital
plan (metro/tram/cycle/bus, housing, retrofit, district heating, flood defence)
hitting four statutory targets under three shared resource limits; `c*` ≈ €10.1 bn,
ε = 0.1, builds in ~3 s at 20k. **Every number in it is invented** — a disclaimer
key travels inside the npz, and the friendly name carries "(demo)".

- **It exists because the taxonomy is only half-demonstrable on v13.** `band` and
  `locked` never occur there at ε = 0.1; this body has 3 bands, plus an
  `at_least_one`, and dCor reaches 0.90 vs v13's 0.33.
- **The stem sorts BEFORE `polytope_13` on purpose.** `data.py:_resolve` takes the
  *last* glob match, so a name like `polytope_demo` would silently become the
  shipped default for id-less requests. Verified: the default still resolves to
  `polytope_13.npz`.
- **The axis ORDER is load-bearing.** The guided tour has no dataset-specific
  knowledge — it caps `axes[0]` to show "costs options, forces nothing" and
  `axes[1]` for the opposite. Measured here: `heat_mw` forces nothing, `retrofit`
  forces housing ≥ 8.9k and flood ≥ 20.1 km. They lead the list for that reason.
- Cost is written as an **epigraph column** exactly like the upstream file (one
  row `c_norm·x − ε·c*·t ≤ c*`, plus `0 ≤ t ≤ 1`), so `_eliminate` recovers
  `c·x ≤ (1+ε)c*` at load. `A` acts on **normalised** coordinates
  (`phys = norm × u_star`); `u_star` is the per-axis LP max over the near-optimal
  body, `z_star` the physical optimum. `X` is optional and omitted.
- Run of show + the numbers: **`docs/WORKSHOP_DEMO.md`**.

## API (backend/app/main.py)

`/api/health`, `/api/meta`, `/api/projection`, `/api/color`, `/api/samples`,
`/api/extremes`, `/api/dependence`, `/api/shadow_pairs`, `POST /api/shadow`,
`POST /api/flexibility`, `POST /api/marginals`, `POST /api/generate`, `/api/clusters`,
`/api/datasets` (list preloaded polytopes), `POST /api/build/preloaded`
(`{dataset_id, n_samples}`), `POST /api/build/upload` (multipart polytope-only
`.npz` + `n_samples` form field).

**Dataset building.** The landing page builds a dataset before the tool loads.
Both build endpoints: validate (`validate_polytope`, 422 on mismatch) →
`sessions.create` (constructs the `Dataset`, which **generates the sample cloud**,
and writes the recipe) → **eagerly warm** dependence/shadow_pairs/extremes/flex so
views open instantly → return the `/api/meta` payload **carrying the session id**.
`n_samples` outside 1k–100k is a 422 (`_require_n`), **not** clamped.
Frontend `reloadAll()` re-fetches every view. Uploads are polytope-only (~tens of
KB); the build request itself can take ~30s at 100k samples + eager LPs, so nginx
keeps a long `proxy_read_timeout`.

## Sessions — multi-tenant + persistent (`backend/app/sessions.py`)

There is **no "active dataset"**. Each build mints a session id; every request
names the dataset it wants via the **`X-Dataset-Id` header** (set once in
`api.ts`'s `fetchRetry`) or **`?ds=`** (so links are shareable and curl / `/docs`
work). Requests with no id get the shipped default. Resolution happens in the
`current_dataset` FastAPI dependency — endpoints take `ds: Dataset =
Depends(current_dataset)` and never reach for module state.

- **Persistence is a recipe, not a snapshot.** `SESSION_DIR/<id>/session.json`
  stores `{source, stem, n_samples, seed, name}` (+ `polytope.npz` for uploads,
  ~46 KB). Hit-and-run is seeded, so replaying the recipe regenerates the *same*
  cloud — verified byte-identical across a container restart. A session is a few
  KB on disk instead of 1.4–7 MB.
- **Memory is bounded.** Only the `MAX_SESSIONS` (env, default 8) most recently
  used stay resident, LRU. Evicted ids still resolve — they just cost a rebuild
  (~5 s at 20k, ~13 s at 100k). A per-id lock keeps concurrent requests from
  regenerating the same cloud N times.
- **Caches are per dataset.** Shadows/dependence/extremes/flex live in `ds.cache`
  (see `generate._slot`). They used to be module globals, which silently made the
  backend single-tenant: a second user's build served the first user's LP results.
- **Frontend.** The id is kept in `localStorage` *and* the URL; on boot `App`
  tries `getMeta()` with it, entering the tool directly on success and falling
  back to the landing page on 404 (`UnknownDataset`). Clicking the **dataset chip**
  returns to the landing page but **does not clear the id** — it used to, which
  made it a one-way door: the id was the only handle on the built cloud, so backing
  out meant regenerating it. `App.resumeMeta` holds the session you stepped away
  from and the landing page shows "← keep exploring <name>" for as long as it does;
  `adoptSession` overwrites the id only when a new dataset is actually built. Recipes older than `SESSION_TTL_DAYS` (env, default 30) are swept at
  startup.
- `docker-compose.yml` mounts `./backend/sessions`, so `up --build` doesn't send
  everyone back to the landing page. On Kubernetes without a PVC, sessions live
  only as long as the pod — the id still resolves for as long as it does.

## Dev / commands

```bash
docker compose up --build                 # frontend :5173 · backend :8000 (/docs)

# local hot-reload
cd backend && DATA_DIR=./data uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev                # Vite proxies /api → 127.0.0.1:8000

# quality gates (run before pushing)
cd frontend && npm run check && npm run build
```

Deploys to ETH IVIA via `helm/` (see commit history / CI). No projection cache /
PVC anymore (PCA is live).

## Gotchas (learned the hard way — see PROCESSES.md §14)

- **The demo scenario in the guided tour is a two-parter on purpose.** Capping
  offshore wind leaves 11.7% of the space but forces *nothing* (this polytope is a
  weakly-coupled box); capping onshore wind leaves 2.5% and puts a floor under PV.
  Showing only the first case makes the tool look inert — the contrast is the
  lesson. Don't "fix" the tour by dropping the second half, and don't write step
  text asserting numbers: an uploaded polytope has its own (PROCESSES §10.14).

- **Never cache polytope-derived results in a module global.** Every user holds a
  different dataset; a global cache serves one user's LPs to another. Hang it off
  `ds.cache` (`generate._slot`). Same rule for "the current dataset" — it is a
  request parameter, not module state.

- **PCA is the only backend projection, computed live.** t-SNE/UMAP removed; STAR
  is a frontend linear projection. No cache, no HTTP 425.
- **fmax normalization.** `phys = norm × u_star` for the 9 techs (offset 0). Use
  `*_norm` for projection/distance, `*_phys` for display.
- **Docker images are immutable** — edits need `docker compose build <svc> &&
  up -d <svc>`; "my changes vanished" = stale image.
- **Vite proxy targets `127.0.0.1`, not `localhost`** (Node ≥17 IPv6 `::1` vs
  IPv4-only uvicorn).
- **Svelte 5 `$effect`**: never write state an effect also reads (infinite loop).
  Layout effects write scales; render effects only read.
- **regl-scatterplot**: color value in the 3rd tuple slot (`valueA`), normalized
  to [0,1]; set `zDataType` explicitly.
- **Debounce ≠ cancellation**: every debounced fetch that writes state needs a
  sequence token or response-identity check, or out-of-order responses clobber
  newer state.
- **Hiding an axis ≠ constraining one ≠ marginalizing one.** Keep the three apart;
  conflating them is the single most confusing thing the UI can do.
  - *Hide* (the axis-label menu; the list in Settings is only the un-hide) does not
    shrink the body: a hidden axis still constrains every other one, and dropping it
    from the view can never make an infeasible set feasible. What it DOES change is
    which space the distributions are read in — **the violins re-measure in the
    projection onto the axes you kept** (worldview B, `POST /api/marginals`), so
    hiding an axis reshapes them. This used to be a pure no-op with zero network
    traffic; it no longer is. Everything else still ignores it: **dCor values, the
    facets, the flexibility bands, Map (PCA), StarWheel and the radar glyphs** — the
    middle two because projection genuinely leaves them invariant, the rest because
    a subset would need a refit.
  - *Constrain* (drag along a row, or a typed limit) adds halfspaces and genuinely
    shrinks the body. This is what `allConstraints` drives; `min == max` is valid
    for `/api/flexibility` but `generate()` needs a thin band (`radius >= 1e-7`).
  - *Marginalize* is exact for pairs via `shadow()`; there is no k-D endpoint.
- **Self-generated samples = a single hit-and-run chain**, so `meta.diagnostics`
  rhat/ess are empty — don't surface convergence stats as if from the old
  multi-chain file.
- **`net_present_cost` in the frontend is dead code**, not a working field. The
  `SHORT` label maps and the `.tt-row.cost` tooltip style are leftovers from the
  10-column era; `/api/color` and `/api/samples` both **400** on it. See below for
  what reviving it would take.

## Cost is recoverable, but is not currently loaded (not yet implemented)

`data.py:_eliminate` FM-projects cost out and drops the 10-D system (`A10`/`b10`
are locals) — only the scalars `c_star` and `epsilon` survive. But the npz still
carries the structure: cost is an **epigraph variable**, so 2 rows bound it to
`[0,1]` (`0 = c*`, `1 = (1+ε)c*`) and **66 rows** read `a_r·x − k_r·cost ≤ b_r`,
giving a convex piecewise-linear envelope

```
cost_norm(x) = max_r (a_r·x − b_r) / k_r     cost_phys(x) = c_star·(1 + ε·cost_norm(x))
```

Verified against the file's own vertices `X` (which keep their cost column):
interior vertices reproduce the stored cost to ~1e-10; the 135 vertices on the cost
cap sit above the envelope, exactly as an epigraph implies. Two caveats if this is
ever built: cost is a **derived readout** (the cheapest realization of a mix), not a
free design axis, and it would show structurally high dependence with all 9 techs;
and while `cost ≤ t` is exactly those 66 halfspaces (so a budget slider tightening
ε is legitimate), `cost ≥ t` is **not** convex-representable and must be blocked.

## Theming (light/dark)

Tokens live in `app.css` under `:root` (dark) and `:root[data-theme="light"]`.
**Light is a selected set, not an inversion** — every value was contrast-checked,
and both themes are audited in full (see below). **A white-on-dark wash and a
black-on-white wash at the same alpha do NOT read the same**: light-on-dark blooms,
dark-on-light does not, so the light `--s-*`/`--b-*` scrims are ~1.4x their dark
counterparts and the fixed greys (`--rule`, `--field-border`, `--axis-line`) are a
step darker. Copying a dark alpha straight into light is the single most common way
this theme loses its structure. `App` writes `data-theme` on
`<html>`; the OS preference only supplies the *initial* value, after which the
user's choice wins in both directions and persists to `localStorage`.

- **Use the tokens, never a literal.** The workhorses are the neutral scrims
  `--s-02/03/05/09` (surfaces) and `--b-08/12/20` (borders): the chrome is a white
  wash over a dark panel, and those become black washes over a white one. Also
  `--panel-glass`, `--halo` (marker outlines), `--tick` (white-on-dark marks),
  `--rule`, `--axis-line`, `--amber`, `--warn`, `--danger`, `--extreme`, `--limit`.
  Accent washes are `color-mix(in srgb, var(--accent) N%, transparent)`.
- **`--grid` is for chart scaffolding** (radar rings and spokes), NOT the `--s-*`
  scrims. The scrims are surface washes; asked to draw a hairline at their alpha
  the radar web came out at ~1.08:1 on white — completely invisible in light mode.
  `--grid` is tuned per theme (0.17 white / 0.22 black).
- **Pins store a palette SLOT, never a resolved colour** (`Pin.slot` →
  `colors.ts:pinColor(slot, theme)`). The pin palette is theme-dependent: dark
  keeps the airy pastels, which suit big filled marks on a dark panel but are
  1.2–1.6:1 on white, so light borrows the contrast-checked categorical steps.
  A colour baked in at pin time would keep the old theme's value after a flip.
- **Any "white mark" default is a light-mode bug.** `ParallelCoords.overlayColor`
  defaults to `null` = `--tick`, and the interpolated `RadarGlyph` takes
  `var(--tick)`; both were literal `#ffffff` and vanished on a light panel. Pass a
  colour only when it *means* something (a pin's identity).
- **A canvas holds baked pixels — CSS cannot restyle it.** `ParallelCoords`
  (violins, flex bands) and `ScatterGL` read live token values via
  `colors.ts:canvasTokens()/themeToken()` and repaint when the **`themeTick`**
  prop changes. Any new canvas colour must go through those, and its render
  `$effect` must read `themeTick`, or it will stay the old theme's colour.
- **The facet-category palette is theme-independent.** `#3987e5 / #d95926 /
  #199e70` was validated on *both* surfaces (all-pairs: CVD ΔE 10.2, normal 20.9,
  contrast 4.46:1 dark and 3.23:1 light), so `facets.ts` needs no light variant —
  one palette, nothing to drift. The reference light steps were tried and rejected:
  aqua `#1baf7a` is only 2.82:1 on white.
- **The heatmap ramp is theme-independent, on purpose.** The dependence matrix uses
  viridis in *both* themes (`colors.ts:ramp()` takes no theme argument, so a call
  site cannot reintroduce the drift). It used to switch to a single-hue teal ramp on
  light, on the argument that a sequential ramp's low end should recede into ITS
  surface — but the matrix is a legend-read value encoding, and a cell that changes
  hue when you flip the theme reads as a different VALUE. What flips instead is only
  what is *not* carrying the value: the 1px gap between cells (`--panel-glass`) and
  the labels. The in-cell number is chosen against the **cell**, not the theme —
  `.val` is white below `VAL_INK_AT` and `#10151c` above it, a deliberate pair of
  literals, because the surface it sits on no longer depends on the theme. The
  crossover of the two contrast curves is t = 0.475, where both land at ~4.4:1 (the
  old 0.55 threshold left a 3.37:1 worst case).
  The MARK ramp (scatter points, PC lines, facet dots) is still theme-dependent —
  those sit on the surface and have to stay visible against it.
- **Frost, not shadow.** `--shadow-md/lg` are `none`; panels are separated from
  what is behind them by `--panel-glass` (62% dark / 72% light) + `backdrop-filter:
  blur(22px) saturate(180%)` + a `--b-12` border. The `saturate` is what makes it
  read as *frost* rather than a grey veil — it keeps the colour of what is
  underneath alive. With no cast shadow the border does real work, so don't drop
  it. The tokens are kept rather than deleted so a component reaching for
  `--shadow-lg` gets `none` instead of an invalid declaration voiding its rule.
- **Panels floating on the map must be OPAQUE, not glass.** `--panel-glass` is
  72/88%, and a 20k-point cloud reads straight through it — the hover tooltip and
  the cluster labels were both unreadable over dense regions. They use
  `--panel-solid` + a small blur. The tooltip also had `background:
  var(--on-accent)f2`, i.e. token-string concatenation: it only parsed because
  both values happen to be 6-digit hex, and `--on-accent` is the *text* colour for
  accent fills, so in dark mode the tooltip was a dark green card. **Never
  concatenate onto a var().**
- **Light mode gives the violins a cool blue** (`--violin-fill/-line`), not the
  neutral grey dark mode uses. On white a mid-grey outline reads as a drop shadow
  of the row rather than as a mark, and it went muddy where it crossed the warm
  amber flexibility band. ~4.2:1 on white, ~4:1 over the band.
- **The Profiles legend swatches are mini-SVGs and must use the TOKENS.** They were
  drawn with the dark theme's literal `rgba(139,148,158,…)` / `rgba(244,180,60,…)`,
  so in light mode the key showed different colours from the marks it explains.
- **The BACKGROUND is flat; the panels above it are not** (2026-08-23). `--page`
  is one solid colour per theme — it used to be a pair of radial gradients, and
  that fade is gone for good, on the landing page too. Everything sitting *on* it
  keeps its glass: `.panel` is translucent `--panel-glass` + `backdrop-filter:
  blur(10px)` + `--shadow-lg`, and the marker glows / text shadows are intact.
  Flattening the panels as well was tried for about an hour and reverted — with a
  flat ground *and* flat panels there is nothing left to separate a floating
  popover from the chart behind it. If you flatten again, flatten one layer only.

## Scale limits (measured)

Build 2.2 s (20k hit-and-run) · one facet shadow 71 ms · all 36 pairs 2.4 s ·
dependence 0.7 s. The **facet sweep is the quadratic term**: C(n,2) shadows, so
16 axes ≈ 15 s, 20 ≈ 30 s, 24 ≈ 47 s, 40 ≈ 245 s.

- **`MAX_AXES = 24`, `WARN_AXES = 16`** (`data.py`), enforced in
  `validate_polytope` (422). Both walls land at ~24: the facet warm above, and the
  Profiles rail, whose rows hit the 5 px violin floor around there.
- **`shadow_pairs` is time-bounded** (`budget_s`, default 25 s) and computes
  most-coupled-first, so the interesting pairs are ready even when it truncates.
  Truncated sweeps are **not cached** — the leftovers come back `pending` and the
  matrix fetches them singly. Don't remove that fallback.
- **`shadow_pairs` carries the polygons**, so the matrix draws every mini-outline
  from ONE response (~19 KB). It used to fire one request per pair: 36 at 9 axes,
  276 at the cap. The API returns each pair once in canonical order, so a cell
  needing (column, row) transposes the polygon and swaps LR/UL (`api.ts:flipGaps`)
  — LL and UR are fixed under transpose.

## Facet shape taxonomy (`generate.py`)

A facet is an orthogonal projection of a convex body, so it is **always convex** —
no holes, no reflex vertices — and its bounding box is tight, so it touches all
four sides. The only structural freedom left is **which corners of the box it
cannot reach**, which makes the classification complete rather than ad hoc.

`corner_gaps(poly)` normalizes the facet into the unit square (scale-free, so
GW×GW and GW×ktCO₂/h pairs compare) and returns the Chebyshev gap from each
corner; `classify_facet` labels it. Both ride along on `/api/shadow` and
`/api/shadow_pairs` as `corner_gaps` + `shape`.

| gap cut | category | statement |
|---|---|---|
| none | `independent` | choose the two separately |
| UR | `tradeoff` | cannot both be large |
| LL | `at_least_one` | cannot both be small |
| LR / UL | `dependency` | **directional** — `needs` at scale requires `needed` |
| LL+UR | `band` | substitute one-for-one; total pinned |
| UL+LR | `locked` | must move together |

**The threshold is a live control, not a constant.** `generate.py` classifies at
the default for API consumers, but the frontend re-classifies from the raw gaps
(`lib/facets.ts`) so the Settings slider re-labels with no refetch — the two
implementations are verified to agree on all 36 pairs, `needs` included. A
data-driven θ was **tried and rejected**: the largest jump in v13's sorted gaps
falls at 0.216, which reclassifies `DAC × biomass` (the only `at_least_one`) and
`photovoltaics × biomass` as independent. The distribution is dominated by the 21
trade-offs at 0.25–0.42, so any break-finder tracks that cluster and swallows the
rare categories. Categories are not separable by gap magnitude — only materiality
is. See PROCESSES.md §10b.

`GAP_EPS = 0.15` is calibrated on v13: near-box pairs top out at 0.11, trade-offs
start at 0.25. On v13 the split is **21 tradeoff · 10 independent · 4 dependency ·
1 at_least_one**; `band`/`locked` do not occur (ε=0.1 is generous) but are detected
so a tighter or uploaded polytope isn't mislabelled. Verified against independent
LPs: pinning nuclear to its lowest *or* highest 2% leaves PV's range untouched;
maxing ccs_lump lifts biomass's floor to 89% of its range; pinning DAC low forces
biomass to 61% of its.

**Palette** (matrix upper triangle + facet header): validated with the dataviz
skill's checker on the **all-pairs** pairlist against this app's panel (`#141b24`)
— a matrix puts any two categories side by side, so adjacent-only validation is
not enough. `tradeoff #3987e5 · dependency #d95926 · at_least_one #199e70`, with
`independent` deliberately neutral so structure is what pops. **Three hues is the
cap** — every 4th candidate failed the normal-vision floor — so `band`/`locked`
share the neutral fill and are told apart by a dashed outline plus their label.
Identity is never colour-alone: the mini outline *is* the shape, the legend is
always present, and the caption spells out the sentence.

## Optimum: `z_star`, not `u_star` (data issue RESOLVED)

`docs/OPTIMUM_DATA_ISSUE.md`: the old "optimum outside the polytope" was a misread.
**`u_star` is the per-axis normalization maxima** (→ unit corner `[1,…,1,0]`), not
the optimum. The **real optimum is `z_star`** (v08+), verified *inside* the polytope
(`A·z_star ≤ b`, 0 violations). `data.py` loads the optimum from `z_star`;
`self.u_star` is a back-compat alias holding the optimum so `/api/meta` and the
"u* optimum" pin stay correct. Caveat: the upstream data is **provisional** (the
producer is fixing an algorithm-accuracy flaw), so treat absolute magnitudes as
indicative. Nothing is precomputed from it — PCA runs live on every request, so a
new `polytope_NN.npz` needs no rebuild step, just a fresh dataset build.
