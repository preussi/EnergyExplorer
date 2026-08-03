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
expands it over the matrix; **parallel coords / violins** have no tab either —
they are the bottom strip of both views. Projections offered: **PCA** + **STAR** (star
coordinates, frontend). t-SNE/UMAP were removed.

## Layout

- `backend/app/` — `main.py` (routes), `data.py` (`Dataset`, polytope loading,
  **sample generation**, norm↔phys), `projections.py` (PCA only, live),
  `generate.py` (hit-and-run + LPs: sampling, steering, shadows, extremes, flex).
- `backend/data/` — `polytope_NN.npz` only (versioned; currently **v13**). The
  loader (`data.py:_resolve`) auto-picks the newest suffix. **No samples file** —
  the cloud is generated from the polytope at build time (see Data below).
- `frontend/src/lib/` — `Landing` (entry page), `ScatterGL`, `ParallelCoords`,
  `FacetView`, `DependenceMatrix`, `RadarGlyph`, `StarWheel`, `FlexBars`,
  `api.ts`, `colors.ts`.

## Data — 9 axes = 9 technologies (cost is NOT a design axis)

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

## API (backend/app/main.py)

`/api/health`, `/api/meta`, `/api/projection`, `/api/color`, `/api/samples`,
`/api/extremes`, `/api/dependence`, `/api/shadow_pairs`, `POST /api/shadow`,
`POST /api/flexibility`, `POST /api/volume`, `POST /api/generate`, `/api/clusters`,
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
  back to the landing page on 404 (`UnknownDataset`). "⟳ change dataset" clears
  both. Recipes older than `SESSION_TTL_DAYS` (env, default 30) are swept at
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
- **Dimension toggles (Settings) = display marginalization, not model surgery.**
  Disabling an axis only filters the *presentation* layer (parallel coords, the
  Coupling matrix, the docked facet, color/slice pickers) — the samples, dependence matrix, and facet shadows
  are unchanged (already marginals/projections over the full space). The dropped
  axis still *constrains* the rest (a facet shadow already projected it out). The
  **Map (PCA)** deliberately ignores toggles (would need a refit over the subset).
- **Self-generated samples = a single hit-and-run chain**, so `meta.diagnostics`
  rhat/ess are empty — don't surface convergence stats as if from the old
  multi-chain file.

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
