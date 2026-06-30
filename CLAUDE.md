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
   loads .npz at startup · PCA live · t-SNE/UMAP precomputed+cached ·
   clusters · extremes (LP) · generation (LP feasibility + hit-and-run)
```

## Layout

- `backend/app/` — `main.py` (routes), `data.py` (`Dataset`, npz loading,
  norm↔phys), `projections.py` (PCA/t-SNE/UMAP + cache builder), `generate.py`
  (steering/candidate generation).
- `backend/data/` — `polytope_NN.npz`, `polytope_samples_NN.npz` (versioned;
  currently v08). The loader (`data.py:_resolve`) auto-picks the newest suffix.
- `backend/cache/` — precomputed t-SNE/UMAP `.npy`, mounted as a volume.
- `backend/scripts/npz_to_csv.py` — npz → `data/csv/` export helper.
- `frontend/src/lib/` — `ScatterGL`, `ParallelCoords`, `FacetView`,
  `DependenceMatrix`, `RadarGlyph`, `StarWheel`, `FlexBars`, `api.ts`, `colors.ts`.

## Data (10 axes = 9 technologies + net present cost)

Axes: `nuclear, photovoltaics, wind_offshore, wind_onshore, electrolysis, DAC,
battery, ccs_lump, biomass, net_present_cost`.

- `polytope_NN.npz` — `A`, `b`, `X`: polytope `{x : A·x ≤ b}` (v08: 429 rows).
  **`z_star`** = the real cost optimum (9-D physical techs). **`u_star`** = per-axis
  normalization maxima, **NOT** the optimum (see below).
- `polytope_samples_NN.npz` — 20k uniform samples from **two samplers** (`chrrt`,
  `har`), each in **normalized** (`*_norm`, ~[0,1], use for projections/distance)
  and **physical** (`*_phys`, GW/MtCO₂/NPC, use for tooltips/axis labels) units;
  `round_transformation`/`round_shift` map between them; `config["c_star"]` =
  optimal cost, `config["z_star_physical"]` = optimum.

## API (backend/app/main.py)

`/api/health`, `/api/meta`, `/api/projection`, `/api/color`, `/api/samples`,
`/api/extremes`, `/api/dependence`, `/api/shadow_pairs`, `POST /api/shadow`,
`POST /api/flexibility`, `POST /api/generate`, `/api/clusters`.

## Dev / commands

```bash
docker compose up --build                 # frontend :5173 · backend :8000 (/docs)

# local hot-reload
cd backend && DATA_DIR=./data uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev                # Vite proxies /api → 127.0.0.1:8000

# quality gates (run before pushing)
cd frontend && npm run check && npm run build

# build projection cache (t-SNE/UMAP; persisted via ./backend/cache volume)
docker compose exec backend python -m app.projections [--force]
```

Deploys to ETH IVIA via `helm/` (see commit history / CI).

## Gotchas (learned the hard way — see PROCESSES.md §14)

- **PCA runs live; t-SNE/UMAP must be precomputed** (seconds–minutes on 20k pts).
  Until a cache file exists the endpoint returns HTTP 425.
- **Two normalizations.** Use `*_norm` for projection/distance; `*_phys` only for
  display. Don't compute Euclidean distance on physical units.
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
