# AI-Assisted Decision-Making for Designing Energy Systems — Design Sketch

> Semester project (ETH, with the Energy Science Center).
> Goal: an interactive system that lets decision-makers explore the space of
> near-optimal energy-system designs, returning *agency* to the user instead of
> handing them a single "optimal" solution.

This document is the **first architecture sketch**. No application code yet — it
defines the data, the container layout, the API contract, the projection
strategy, the frontend component plan, and a milestone roadmap. Code scaffolding
is the next step.

---

## 1. Problem framing

To decarbonize the energy system we optimize the *selection and sizing* of
technologies. But the cost-optimal point is rarely the only acceptable one:
many designs are within a few percent of optimal cost yet differ enormously in
*which* technologies they rely on. This is the **near-optimal / MGA space**
("Modelling to Generate Alternatives").

A mathematically feasible design can still violate real-world constraints that
weren't in the optimizer. So we want a human in the loop: let experts **see**,
**filter**, and eventually **steer** the generation of candidate designs.

This project's first job is the *seeing* part — project the high-dimensional
solution set down to 2D/3D (PCA, t-SNE, UMAP) and make it interactively
explorable. Later phases add steering (re-sampling/re-weighting the space from
user feedback) and persistence (a database).

---

## 2. The data

Two files in the project root, both 10-dimensional. The 10 axes are
**9 technologies + total cost**:

```
nuclear, photovoltaics, wind_offshore, wind_onshore, electrolysis,
DAC, battery, ccs_lump, biomass, net_present_cost
```

### `polytope.npz` — the near-optimal space definition

| key         | shape     | meaning                                                        |
|-------------|-----------|----------------------------------------------------------------|
| `A`         | (172, 10) | constraint matrix                                              |
| `b`         | (172,)    | RHS — the polytope is `{ x : A·x ≤ b }`                        |
| `X`         | (153, 10) | 153 points inside the polytope (representation/vertices), in normalized ~[0,1] coords; all satisfy `A·x ≤ b` |
| `name_list` | (10,)     | the 10 axis labels above                                       |

### `polytope_samples.npz` — 20,000 uniform samples of that space

| key                    | shape          | meaning                                                  |
|------------------------|----------------|----------------------------------------------------------|
| `chrrt_norm`           | (4, 5000, 10)  | sampler A (Coordinate Hit-and-Run, rounded+thinned), **normalized** units, 4 chains × 5000 |
| `chrrt_phys`           | (4, 5000, 10)  | same samples in **physical** units (GW, MtCO₂, NPC, …)   |
| `har_norm`             | (4, 5000, 10)  | sampler B (Hit-and-Run), normalized                      |
| `har_phys`             | (4, 5000, 10)  | sampler B, physical                                      |
| `round_transformation` | (10, 10)       | affine map for normalized ↔ physical                     |
| `round_shift`          | (10,)          | translation part of that map                             |
| `chrrt_rhat` / `_ess`  | (10,)          | MCMC convergence diagnostics (r̂≈1.0, ESS≈20k → converged)|
| `har_rhat` / `_ess`    | (10,)          | same for sampler B (also converged, lower ESS)           |
| `names`                | (10,)          | axis labels (same as above)                              |
| `u_star`               | (9,)           | the cost-optimal design (per technology)                 |
| `config`               | object         | ε=0.05 cost slack, `c_star` (optimal cost), seeds, sampler params, tool versions |

**Key facts that drive design decisions**
- **20,000 points** → must render with WebGL; SVG/plain-d3 will not stay
  interactive. (regl for the scatter.)
- **Two normalizations.** `*_norm` is already ~[0,1] and is the right input for
  projections (Euclidean distance is meaningful). `*_phys` spans
  ~1e2 → 1e6 across axes, so it is for **tooltips, axis labels, and
  parallel-coordinates display**, not for distance-based projection.
- **Two samplers** (`chrrt`, `har`) — expose as a user toggle; they should look
  similar if sampling is unbiased (a nice built-in validation view).
- **t-SNE/UMAP on 20k points takes seconds–minutes** → precompute and cache;
  never run on-request. **PCA is instant** → can run live.
- `u_star` and `c_star` give us a reference point (the optimum) to mark in every
  view.

---

## 3. Architecture: containers

Start with **2 containers**; add the DB as a 3rd when persistence is needed.

```
┌──────────────────────────────────────────────────────────────────────┐
│  frontend  (container 1)  — Svelte + Vite, served by nginx            │
│                                                                        │
│   Overview view        Detail view            Controls                │
│   ┌───────────────┐    ┌─────────────────┐    ┌──────────────────┐    │
│   │ 2D scatter    │◄──►│ parallel coords │    │ sampler: chrrt|har│   │
│   │ (regl, 20k)   │    │ (10 axes, d3)   │    │ method: pca|tsne… │    │
│   │ PCA/t-SNE/UMAP│    │ brush per axis  │    │ color-by: tech    │    │
│   └───────────────┘    └─────────────────┘    │ norm | physical   │    │
│        linked brushing & selection            └──────────────────┘    │
└───────────────────────────────▲────────────────────────────────────--─┘
                                 │  REST / JSON
┌────────────────────────────────┴───────────────────────────────────┐
│  backend  (container 2)  — Python + FastAPI + uvicorn                │
│   - load .npz at startup into memory                                 │
│   - compute + CACHE projections (sklearn PCA/t-SNE, umap-learn)      │
│   - serve: samples (downsampled), axis metadata, norm↔phys params,   │
│            convergence diagnostics, the optimum (u_star/c_star)      │
│   - (later) re-sample / re-weight polytope from user constraints     │
└────────────────────────────────▲───────────────────────────────────┘
                                  │  (phase 3)
┌────────────────────────────────┴───────────────────────────────────┐
│  db  (container 3, later)  — Postgres                                │
│   - saved sessions, user feedback, custom-generated candidates       │
└─────────────────────────────────────────────────────────────────────┘
```

### Why this stack

- **Backend = Python/FastAPI** — essentially forced: PCA/t-SNE live in sklearn,
  UMAP in `umap-learn`, and the later "co-adaptive generation" task wants numpy
  next to the polytope math (`A`, `b`, the rounding transform). FastAPI gives
  typed JSON endpoints + auto OpenAPI docs.
- **Frontend = Svelte + Vite + regl-scatterplot + d3** — light, fast, minimal
  boilerplate. The scatter uses **regl-scatterplot** (WebGL, scales to ~20M pts)
  which gives pan/zoom, **point hover-picking**, and **lasso/box selection** out
  of the box — the latter is the substrate for Phase 2 linked brushing. d3-scale
  drives axis mapping; d3 will also back the parallel-coordinates view.
- **DB later = Postgres** — relational is enough for sessions/feedback; add
  `pgvector` only if we later want similarity search over designs.

---

## 4. Backend API contract (v1)

JSON over REST. All arrays are plain numbers (downsampled where noted). Physical
values are included for display; projections are computed on normalized space.

```
GET /api/meta
  → {
      "axes": ["nuclear", ..., "net_present_cost"],   # 10 labels
      "samplers": ["chrrt", "har"],
      "methods": ["pca", "tsne", "umap"],
      "spaces": ["norm", "phys"],
      "n_samples": 20000,
      "optimum": { "u_star": [...9...], "c_star": 1258541.7, "epsilon": 0.05 },
      "diagnostics": {
        "chrrt": { "rhat": [...10...], "ess": [...10...] },
        "har":   { "rhat": [...10...], "ess": [...10...] }
      }
    }

GET /api/projection?method=pca&sampler=chrrt&dims=2[&sample=5000]
  → {
      "method": "pca", "sampler": "chrrt", "dims": 2,
      "points": [[x, y], ...],                 # projected coords, length n
      "index":  [0, 1, ...],                   # row id back into full sample set
      "explained_variance": [0.41, 0.19],      # PCA only
      "cached": true
    }
  # `sample` optionally downsamples for transport (e.g. 5000 of 20000).
  # t-SNE/UMAP are read from a precomputed cache; 404/425 if not yet built.

GET /api/samples?sampler=chrrt&space=phys&fields=nuclear,battery[&index=...]
  → {
      "fields": ["nuclear", "battery"],
      "values": [[...], ...],                   # per-row values for those axes
      "index":  [...]
    }
  # Backs the parallel-coordinates view and tooltips. `index` lets the frontend
  # fetch only the brushed/selected subset.

GET /api/clusters?method=pca&sampler=chrrt&k=6
  → { "clusters": [ { "x", "y",            # centroid in projection space
                       "count",
                       "top": [ {"name","z","value"}, ... ] }, ... ] }
  # K-means over the projected points; each cluster characterized by the
  # technologies whose mean most deviates (z-score) from the global mean.
  # Drives the cluster labels overlaid on the scatter.

GET /api/color  → per-point scalar for coloring (cost / technology / chain)

POST /api/generate
  body → { sampler, n, seed, constraints: [ {axis, min?, max?} ] }   # physical units
  → { feasible, n, points (PCA), values (phys, all axes), fields, radius }
  # Adds the constraints as rows to the polytope (A,b), checks feasibility via the
  # Chebyshev-center LP, then draws fresh uniform hit-and-run samples from the
  # constrained near-optimal region. feasible=false ⇒ region empty.

GET /api/health  → { "status": "ok" }
```

Notes
- **Caching:** projections are computed once per `(method, sampler, dims)` and
  written to `backend/cache/` (e.g. `.npy` + a small JSON manifest). On startup
  the backend can pre-build PCA for both samplers, and t-SNE/UMAP can be a
  warm-up job or a `make cache` step.
- **Transport size:** 20k×2 floats is small; full 20k×10 physical is ~1.6 MB
  JSON — fine, but prefer the `fields`/`index` params so the parallel-coords
  view only pulls what it shows.
- **Determinism:** fix `random_state` for t-SNE/UMAP so the layout is stable
  across reloads.

---

## 5. Projection strategy

| method | where it runs        | speed on 20k | notes                                           |
|--------|----------------------|--------------|-------------------------------------------------|
| PCA    | live or cached       | instant      | report explained variance; linear, interpretable; can show loadings (which technologies drive each axis) |
| t-SNE  | **precomputed/cached** | minutes    | great local cluster structure; non-deterministic unless seeded; no out-of-sample mapping |
| UMAP   | **precomputed/cached** | seconds–min | preserves more global structure than t-SNE; has reusable transform for new points (useful in phase 3) |

Decisions
- **Project the `*_norm` space.** Optionally drop or down-weight
  `net_present_cost` from the projection and instead **encode cost as point
  color** — cost is a constraint here (all points ≤ 5% above optimum), so
  spatial structure is more interesting over the 9 technology axes.
- **Color-by** is a frontend control: by cost, or by any single technology, or
  by sampler/chain (to eyeball convergence).
- Always overlay the **optimum** (`u_star`) projected into the same space as a
  distinct marker.

---

## 6. Frontend component plan (Svelte)

```
frontend/src/
  lib/
    api.ts                # typed fetch wrappers for the endpoints above
    stores.ts             # Svelte stores: selection, brush ranges, controls
    ScatterGL.svelte      # regl WebGL scatter; pan/zoom; lasso/box select
    ParallelCoords.svelte # d3 parallel coordinates, 10 axes, per-axis brush
    Controls.svelte       # sampler / method / space / color-by selectors
    Tooltip.svelte        # physical-unit detail for a hovered/selected point
    Legend.svelte
  routes/ (or App.svelte) # layout wiring the views together
```

Interaction model (the core of the project)
- **Linked brushing:** selecting points in the scatter highlights the same rows
  in parallel coordinates, and brushing an axis range in parallel coordinates
  filters/dims the scatter. One shared `selection` store drives both.
- **Overview → detail:** scatter is the map; parallel coords is the per-design
  readout across all 10 axes.
- This linked pair is exactly the substrate for phase 3, where a user's
  brush/constraints become inputs that re-steer sampling.

---

## 7. Repository layout (target)

```
energy-explorer/
  docker-compose.yml
  data/                       # the two .npz files (mounted read-only)
  backend/
    Dockerfile
    pyproject.toml            # fastapi, uvicorn, numpy, scikit-learn, umap-learn
    app/
      main.py                 # FastAPI app + routes
      data.py                 # load npz, norm↔phys, sampler access
      projections.py          # PCA / t-SNE / UMAP + caching
    cache/                    # precomputed projections (gitignored)
  frontend/
    Dockerfile                # node build → nginx serve
    package.json              # svelte, vite, d3, regl
    vite.config.ts
    src/ ...                  # as in §6
  DESIGN.md                   # this file
  README.md                   # quickstart
```

`docker-compose.yml` (shape, not final):
```yaml
services:
  backend:
    build: ./backend
    volumes: [ "./data:/data:ro", "./backend/cache:/app/cache" ]
    ports: [ "8000:8000" ]
  frontend:
    build: ./frontend
    ports: [ "5173:80" ]
    depends_on: [ backend ]
  # db:  (phase 3) postgres:16
```

---

## 8. Milestone roadmap

**Phase 0 — Scaffold (next step)**
- [ ] Repo layout + `docker-compose up` brings up backend + frontend.
- [ ] Backend loads both `.npz`, exposes `/api/meta` and `/api/health`.
- [ ] Frontend renders an empty Svelte app talking to the backend.

**Phase 1 — Projection + overview** ✅
- [x] `/api/projection` for PCA (live) — explained variance returned.
- [x] `ScatterGL` renders 20k points (regl), pan/zoom, color-by control.
- [x] Overlay the optimum (`u_star` projected via PCA); sampler & color-space toggles.
- [x] `/api/color` endpoint: color by cost, any technology, or MCMC chain.

**Phase 2 — Detail + linking** ✅
- [x] Precompute + cache t-SNE **and UMAP** (both samplers) into the mounted
      `backend/cache/`; serve via `/api/projection` (HTTP 425 until built).
- [x] `ParallelCoords` over the 10 axes (physical units), canvas-rendered
      (base layer for all 20k designs + highlight layer for the selection).
- [x] Linked brushing/selection: drag-to-brush axes filters → highlights in the
      scatter; lasso-select in the scatter (Pan/Select mode toggle) highlights in
      parallel coords. Shared row-index selection; Clear resets both.

**Phase 3 — Steering + persistence**
- [x] **3a** Translate user brushes / manual constraints into added inequalities
      on the polytope; re-sample (numpy hit-and-run + Chebyshev-center feasibility)
      to generate customized candidates, projected into the base PCA space.
      `POST /api/generate`; "Generate" shows the candidate set with a back banner.
- [x] **3b** Manual constraint controls (axis + min/max), constraint chips (incl.
      live brush constraints), designs-count, graceful infeasibility message.
- [ ] **3c** Add Postgres (container 3): save sessions, bookmark designs, feedback.
- [ ] **3d** Compare steered candidates vs. full space; objective-directed (MGA)
      generation; iterative co-adaptive loop; hopsy for rigorous sampling.

---

## 9. Open questions / decisions to confirm

- **t-SNE vs UMAP priority** — UMAP has a reusable transform (good for phase 3
  out-of-sample mapping); t-SNE is the more familiar name. Build both, default
  to one?
- **Include cost in the projection** or always treat it as color/constraint?
  (Leaning: exclude from geometry, encode as color.)
- **Downsampling for transport** — show all 20k or a representative 5k by
  default with "load all" on demand?
- **Backend cache build** — at container startup (slow first boot) or via an
  explicit `make cache` / one-off job?
- **Auth/sessions** — needed at all for the semester scope, or single-user/local?
```
