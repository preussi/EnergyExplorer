# Energy Explorer

Interactive exploration of **near-optimal energy-system designs** (MGA space).
See [DESIGN.md](DESIGN.md) for the architecture sketch and roadmap, and
[PROCESSES.md](PROCESSES.md) for how every process works (data provenance,
sampling math, projections, generation, and the visual-analytics overlays).

The app opens on a **landing page**: choose a preloaded polytope (or upload your
own `.npz`) and a sample count, then "Generate & Explore" builds the dataset — the
backend **generates a uniform near-optimal sample cloud** from the polytope
(hit-and-run) and precomputes every view — and drops you into the tool (Coupling
matrix and a PCA/STAR Map, with parallel coords / violins as a strip under both).
Clicking a cell in the Coupling matrix opens that pair's exact facet beside it.

```
backend/     FastAPI + numpy + scikit-learn + scipy
             (data/ holds only polytope_NN.npz — samples are generated, not shipped)
frontend/    Svelte 5 + Vite + regl (WebGL scatter, parallel coords, facets, matrix)
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

## Data & sampling

Only the **polytope** (`data/polytope_NN.npz`) ships; it is tiny (~46 KB). The
sample cloud is **generated on demand** from the polytope with uniform hit-and-run
when you build a dataset from the landing page (default 20k samples; a count
outside 1k–100k is rejected with 422, not clamped), so there is no large samples
file and no precomputed projection cache (PCA is live, t-SNE/UMAP were removed).
See [PROCESSES.md](PROCESSES.md) §4 & §15.

## Sessions

Building a dataset creates a **session**, and its id goes into the URL
(`?ds=<id>`) and browser storage. So a refresh drops you back into the same
dataset rather than the landing page, that link opens it for a colleague too, and
several people can each hold their own dataset against one backend at the same
time. Only a few-KB *recipe* is stored per session (which polytope, how many
samples, which seed) — the cloud is regenerated identically on demand, so sessions
also survive a backend restart. `docker-compose.yml` mounts `./backend/sessions`
to keep them across rebuilds. See [PROCESSES.md](PROCESSES.md) §16.

## API

| endpoint           | purpose                                              |
|--------------------|------------------------------------------------------|
| `GET /api/health`  | liveness                                             |
| `GET /api/datasets`| preloaded polytopes to build from                    |
| `POST /api/build/preloaded` · `POST /api/build/upload` | generate the cloud + warm all views; return meta |
| `GET /api/meta`    | axes, methods, optimum, diagnostics, session id      |
| `GET /api/projection?method=pca&dims=2[&sample=N]` | projected points + optimum + loadings |
| `GET /api/samples?space=phys&fields=nuclear,battery[&sample=N]` | raw values (backs parallel coords) |

The full endpoint list (dependence, shadow, flexibility, volume, extremes,
generate, clusters, …) is in [PROCESSES.md](PROCESSES.md) §13.
