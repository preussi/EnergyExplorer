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
   loads .npz at startup · PCA live (only projection) · clusters ·
   extremes (LP) · generation (LP feasibility + hit-and-run)
```
Frontend default view is **Profiles** (violin parallel-coords). Projections offered:
**PCA** + **STAR** (star coordinates, frontend). t-SNE/UMAP were removed.

## Layout

- `backend/app/` — `main.py` (routes), `data.py` (`Dataset`, npz loading,
  norm↔phys), `projections.py` (PCA only, live), `generate.py`
  (steering/candidate generation).
- `backend/data/` — `polytope_NN.npz`, `polytope_samples_NN.npz` (versioned;
  currently **v13**). The loader (`data.py:_resolve`) auto-picks the newest suffix.
- `backend/scripts/npz_to_csv.py` — npz → `data/csv/` export helper.
- `frontend/src/lib/` — `ScatterGL`, `ParallelCoords`, `FacetView`,
  `DependenceMatrix`, `RadarGlyph`, `StarWheel`, `FlexBars`, `api.ts`, `colors.ts`.

## Data — 9 axes = 9 technologies (cost is NOT a design axis)

Axes: `nuclear, photovoltaics, wind_offshore, wind_onshore, electrolysis, DAC,
battery, ccs_lump, biomass`.

- `polytope_NN.npz` — `A`(m,10), `b`, `X`, `name_list` (10, last = cost). At load,
  `data.py` **Fourier–Motzkin projects cost out** → a clean **9-D** technology
  polytope `{x : A·x ≤ b}` (~219 rows) with near-optimality (`cost ≤ (1+ε)c*`)
  baked in. **`z_star`** = the cost optimum (9-D techs); **`u_star`** = per-axis
  fmax normalization maxima (`phys = norm × u_star`), **NOT** the optimum.
- `polytope_samples_NN.npz` (schema v2) — key `samples` (N, **9**), fmax-normalized
  technologies from a **single** hit-and-run sampler; `chain_id`, `rhat`,
  `ess_bulk`. v13 ships ~410k samples; `data.py` caps to `MAX_SAMPLES` (40k, seeded)
  for a manageable payload. Cost was dropped entirely (2026-07 migration).

## API (backend/app/main.py)

`/api/health`, `/api/meta`, `/api/projection`, `/api/color`, `/api/samples`,
`/api/extremes`, `/api/dependence`, `/api/shadow_pairs`, `POST /api/shadow`,
`POST /api/flexibility`, `POST /api/volume`, `POST /api/generate`, `/api/clusters`,
`POST /api/upload` (multipart polytope+samples .npz → swaps the active dataset),
`POST /api/reset` (restore the shipped default).

**Data uploading.** The active dataset is a module-global override in `data.py`
(`get_dataset` returns the uploaded `Dataset` if `set_active` was called, else the
file-based default). Uploads must match the shipped schema (`validate_npz` checks
keys/shapes, 422 on mismatch). Swapping datasets must call `generate.reset_caches()`
— the shadow/dependence/extremes/flex caches are keyed globally, not per dataset.
Frontend `reloadAll()` re-fetches every view after upload/reset. nginx needs
`client_max_body_size` raised (samples .npz is tens of MB).

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

## Optimum: `z_star`, not `u_star` (data issue RESOLVED)

`docs/OPTIMUM_DATA_ISSUE.md`: the old "optimum outside the polytope" was a misread.
**`u_star` is the per-axis normalization maxima** (→ unit corner `[1,…,1,0]`), not
the optimum. The **real optimum is `z_star`** (v08+), verified *inside* the polytope
(`A·z_star ≤ b`, 0 violations). `data.py` loads the optimum from `z_star`;
`self.u_star` is a back-compat alias holding the optimum so `/api/meta` and the
"u* optimum" pin stay correct. Caveats: the upstream data is **provisional** (the
producer is fixing an algorithm-accuracy flaw), and t-SNE/UMAP caches built on old
data are **stale** — rebuild with `python -m app.projections --force`.
