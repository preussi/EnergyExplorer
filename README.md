# Energy Explorer

Interactive exploration of **near-optimal energy-system designs** (MGA space).
See [DESIGN.md](DESIGN.md) for the architecture sketch and roadmap, and
[PROCESSES.md](PROCESSES.md) for how every process works (data provenance,
sampling math, projections, generation, and the visual-analytics overlays).

Phase 0 scaffold: a FastAPI backend that loads the polytope/sample data and
serves projections, plus a Svelte + regl frontend that renders the 2D PCA
scatter of 20,000 designs (colored by total cost).

```
backend/     FastAPI + numpy + scikit-learn (data/ holds the two .npz files,
             baked into the image)
frontend/    Svelte + Vite + regl (WebGL scatter)
docker-compose.yml
```

## Run with Docker (both containers)

```bash
docker compose up --build
```

- Frontend → http://localhost:5173
- Backend API → http://localhost:8000/api/meta  (docs at /docs)

nginx in the frontend container proxies `/api/*` to the backend, so the browser
sees a single origin.

## Run locally (no Docker)

Backend:
```bash
cd backend
pip install -r requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload --port 8000
```

Frontend (proxies /api to localhost:8000 via Vite):
```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

## Precomputing t-SNE / UMAP

PCA runs live. t-SNE and UMAP are expensive on 20k points, so they are
**precomputed once** into `backend/cache/` as `{method}_{sampler}_{dims}d.npy`
and **mounted into the container** (the `./backend/cache:/app/cache` volume in
`docker-compose.yml`). The files persist across rebuilds — they are build
artifacts, not baked into the image. Until a file exists, requesting that
method returns HTTP 425 with a hint.

Build all four (`tsne|umap` × `chrrt|har`) inside the running container, writing
straight into the mounted host folder:

```bash
docker compose exec backend python -m app.projections          # skips existing
docker compose exec backend python -m app.projections --force  # recompute all
```

The builder logs per-step timing and skips any projection whose `.npy` already
exists. Typical cost: ~30–40 s per t-SNE, ~5–30 s per UMAP. A `manifest.json`
lists what was written. UMAP needs `umap-learn` (already enabled in
`requirements.txt`).

To build locally without Docker instead:
```bash
cd backend
DATA_DIR=./data CACHE_DIR=./cache python -m app.projections
```

## API

| endpoint           | purpose                                              |
|--------------------|------------------------------------------------------|
| `GET /api/health`  | liveness                                             |
| `GET /api/meta`    | axes, samplers, methods, optimum, MCMC diagnostics   |
| `GET /api/projection?method=pca&sampler=chrrt&dims=2[&sample=N]` | projected points + per-point cost |
| `GET /api/samples?sampler=chrrt&space=phys&fields=nuclear,battery` | raw values (backs parallel coords) |

## Next (Phase 1 / 2)

- Pan/zoom + lasso selection on the scatter
- Parallel-coordinates view over the 10 axes, with linked brushing
- Build + serve the t-SNE/UMAP cache
- Overlay the cost optimum (`u_star`) in every view
