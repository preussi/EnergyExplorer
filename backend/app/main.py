"""FastAPI app: serves metadata, projections and raw sample values."""
from __future__ import annotations

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .data import SAMPLERS, SPACES, get_dataset
from .generate import generate as run_generate
from .projections import METHODS, project

app = FastAPI(title="Energy Explorer API", version="0.1.0")

# Allow the Vite dev server (and others) during development. In the Docker
# setup nginx proxies /api to the backend, so requests are same-origin there.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/meta")
def meta():
    ds = get_dataset()
    return {
        "axes": ds.axes,
        "samplers": list(SAMPLERS),
        "methods": list(METHODS),
        "spaces": list(SPACES),
        "n_samples": ds.n_samples,
        "cost_axis": ds.axes[ds.cost_idx],
        "optimum": {
            "u_star": ds.u_star.tolist(),
            "c_star": ds.c_star,
            "epsilon": ds.epsilon,
        },
        "diagnostics": ds.diagnostics,
    }


@app.get("/api/projection")
def projection(
    method: str = Query("pca"),
    sampler: str = Query("chrrt"),
    dims: int = Query(2, ge=2, le=3),
    sample: int | None = Query(None, description="optionally downsample to N points"),
):
    ds = get_dataset()
    try:
        pts, ev, cached, opt, comp = project(ds, method, sampler, dims)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        # 425 Too Early: the expensive projection hasn't been built yet.
        raise HTTPException(status_code=425, detail=str(e))

    n = pts.shape[0]
    index = np.arange(n)
    if sample is not None and sample < n:
        rng = np.random.default_rng(42)
        index = np.sort(rng.choice(n, size=sample, replace=False))
        pts = pts[index]

    return {
        "method": method,
        "sampler": sampler,
        "dims": dims,
        "points": pts.astype(float).tolist(),
        "index": index.astype(int).tolist(),
        "optimum": opt.astype(float).tolist() if opt is not None else None,
        "explained_variance": ev,
        # PCA loadings: per-component weights over the technology axes, so the UI
        # can name what each axis represents. None for t-SNE/UMAP.
        "components": comp.astype(float).tolist() if comp is not None else None,
        "feature_names": [ds.axes[i] for i in ds.tech_idx],
        "cached": cached,
    }


@app.get("/api/color")
def color(
    sampler: str = Query("chrrt"),
    field: str = Query("net_present_cost", description="axis name or 'chain'"),
    space: str = Query("phys"),
):
    """Per-sample scalar used to color the scatter, aligned to sample row order."""
    ds = get_dataset()
    if field == "chain":
        vals = ds.chain_ids()
        return {
            "field": "chain",
            "values": vals.astype(int).tolist(),
            "min": 0,
            "max": int(vals.max()),
            "categorical": True,
        }
    if field not in ds.axes:
        raise HTTPException(status_code=400, detail=f"unknown field {field!r}")
    try:
        data = ds.get_samples(sampler, space)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    vals = data[:, ds.axes.index(field)]
    return {
        "field": field,
        "values": vals.astype(float).tolist(),
        "min": float(vals.min()),
        "max": float(vals.max()),
        "categorical": False,
    }


@app.get("/api/samples")
def samples(
    sampler: str = Query("chrrt"),
    space: str = Query("phys"),
    fields: str = Query(..., description="comma-separated axis names"),
):
    ds = get_dataset()
    try:
        data = ds.get_samples(sampler, space)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    names = [f.strip() for f in fields.split(",") if f.strip()]
    try:
        cols = [ds.axes.index(n) for n in names]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"unknown field: {e}")

    return {
        "fields": names,
        "values": data[:, cols].astype(float).tolist(),
        "index": list(range(data.shape[0])),
    }


class Constraint(BaseModel):
    axis: str
    min: float | None = None
    max: float | None = None


class GenerateRequest(BaseModel):
    sampler: str = "chrrt"
    n: int = 2000
    seed: int = 42
    constraints: list[Constraint] = []


@app.post("/api/generate")
def generate(req: GenerateRequest):
    """Add the user's constraints to the polytope and re-sample fresh candidate
    designs from the constrained near-optimal region (projected into PCA space)."""
    ds = get_dataset()
    if req.sampler not in SAMPLERS:
        raise HTTPException(status_code=400, detail=f"unknown sampler {req.sampler!r}")
    cons = [c.model_dump() for c in req.constraints]
    return run_generate(ds, req.sampler, req.n, cons, seed=req.seed)


@app.get("/api/clusters")
def clusters(
    method: str = Query("pca"),
    sampler: str = Query("chrrt"),
    dims: int = Query(2, ge=2, le=3),
    k: int = Query(6, ge=2, le=12),
):
    """K-means clusters of the projected designs, each characterized by the
    technologies that most distinguish it from the overall mean. Returns centroid
    positions in projection space (so the UI can label them on the scatter)."""
    from sklearn.cluster import KMeans

    ds = get_dataset()
    try:
        pts, _, _, _, _ = project(ds, method, sampler, dims)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=425, detail=str(e))

    km = KMeans(n_clusters=k, random_state=42, n_init=5).fit(pts)
    labels = km.labels_
    centroids = km.cluster_centers_

    tech = ds.get_samples(sampler, "phys")[:, ds.tech_idx]
    tech_names = [ds.axes[i] for i in ds.tech_idx]
    gmean = tech.mean(0)
    gstd = tech.std(0) + 1e-9

    out = []
    for c in range(k):
        m = labels == c
        cmean = tech[m].mean(0)
        z = (cmean - gmean) / gstd
        order = np.argsort(-np.abs(z))[:3]
        out.append({
            "x": float(centroids[c, 0]),
            "y": float(centroids[c, 1]),
            "count": int(m.sum()),
            "top": [
                {"name": tech_names[i], "z": float(z[i]), "value": float(cmean[i])}
                for i in order
            ],
        })
    return {"method": method, "sampler": sampler, "k": k, "clusters": out}
